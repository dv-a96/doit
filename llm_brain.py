import os
import json
import re
import litellm
import warnings
# Suppress Pydantic serialization warnings caused by LiteLLM / Gemini tool call formats
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

# Silencing LiteLLM output and debugging helpers
litellm.suppress_warnings = True
os.environ["LITELLM_LOG"] = "ERROR"

def load_config() -> dict:
    """
    Load configuration from ~/doit.cfg. 
    Returns a dict with safely fallback defaults if the file doesn't exist.
    """
    cfg_path = os.path.expanduser("~/doit.cfg")
    defaults = {
        "model": "gemini-2.5-flash",  # Updated to latest stable Gemini model without prefix
        "provider": "gemini",
        "gemini_api_key": ""
    }

    if not os.path.exists(cfg_path):
        return defaults

    with open(cfg_path, "r", encoding="utf-8") as f:
        raw_text = f.read().strip()

    if not raw_text:
        return defaults

    try:
        config = json.loads(raw_text)
        if isinstance(config, dict):
            return {**defaults, **config}
    except json.JSONDecodeError:
        pass

    parsed_config = {}
    for line in raw_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = [part.strip() for part in line.split("=", 1)]
        if key:
            parsed_config[key] = value.strip().strip('"').strip("'")

    return {**defaults, **parsed_config}


def clean_json_markdown(text: str) -> str:
    """
    Safely removes markdown code blocks (e.g., ```json ... ```) from the model's response.
    Handles unexpected leading/trailing whitespaces, newlines, or casing.
    """
    text = text.strip()
    
    # Check if the text is wrapped in markdown code blocks
    if text.startswith("```"):
        # Remove the starting fence (e.g., ```json or ```)
        # We split by newline, drop the first line, and drop the last line if it's the closing fence
        lines = text.splitlines()
        
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
            
        text = "\n".join(lines).strip()
        
    return text


def _check_command_safety(command: str) -> dict:
    """
    Analyzes a generated bash command. This is the implementation of the Tool 
    called by the LLM. It determines if the command modifies the filesystem.
    """
    # Simple, highly reliable rule-based check to avoid a third LLM call if possible,
    # fallback to LLM analysis for ambiguous commands.
    command_clean = command.strip().split()
    if not command_clean:
        return {"is_destructive": False, "explanation": "Empty command."}
        
    base_cmd = command_clean[0]
    safe_commands = {"ls", "grep", "cat", "pwd", "echo", "head", "tail", "find", "diff", "env"}
    
    # Check for redirection characters which inherently modify files
    has_redirection = any(char in command for char in (">", ">>", "|"))

    if base_cmd in safe_commands and not has_redirection:
        return {
            "is_destructive": False,
            "explanation": f"The command '{base_cmd}' only reads or displays information."
        }

    # If it's not obviously safe, we query the LLM to verify safety (Secondary Call)
    config = load_config()
    model = config["model"]
    provider = config["provider"]
    
    safety_system_prompt = """You are a security compliance tool. Analyze the command and return a JSON:
{
  "is_destructive": true | false,
  "explanation": "Brief description of filesystem impact"
}
Only 'read-only' commands are safe (is_destructive: false). Anything that writes, deletes, moves, installs, modifies or edits is destructive (is_destructive: true)."""

    try:
        response = litellm.completion(
            model=model,
            custom_llm_provider=provider,
            messages=[
                {"role": "system", "content": safety_system_prompt},
                {"role": "user", "content": f"Analyze: {command}"}
            ],
            system_instruction=safety_system_prompt,
            temperature=0.0
        )
        raw_content = response.choices[0].message.content
        cleaned_content = clean_json_markdown(raw_content)
        return json.loads(cleaned_content)
    except Exception:
        return {
            "is_destructive": True,
            "explanation": "Could not verify safety automatically. Assuming potentially destructive."
        }


def query_llm(user_instruction: str) -> dict:
    """
    Send the request to the LLM. Registers tools in the System context, 
    executes them if requested by the model, and returns the structured response.
    """
    config = load_config()
    model = config["model"]
    provider = config["provider"]
    
    if config.get("gemini_api_key"):
        os.environ["GEMINI_API_KEY"] = config["gemini_api_key"]
        os.environ["GOOGLE_API_KEY"] = config["gemini_api_key"]
    
    if isinstance(user_instruction, (list, tuple)):
        user_instruction = " ".join(user_instruction)
    else:
        user_instruction = str(user_instruction)

    # 1. Define the tool formally (matches 'sys.safety_tool_definition' in ACDL)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "call_safety_check",
                "description": "Analyze a bash command to check if it modifies the filesystem or system state.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The exact bash command to be executed (e.g. 'ls -la' or 'rm -rf files')"
                        }
                    },
                    "required": ["command"]
                }
            }
        }
    ]

    system_prompt = """You are the brain of a CLI agent named 'doit'.

Rules:
1. If the user wants to run or perform ANY action in the terminal (including printing/echoing text, listing files, deleting, moving, creating, network check), you MUST treat this as a terminal command. 
   - For example: if the user says "print hello" you MUST generate the command "echo hello" and set action_type to "command".
   - You MUST call the 'call_safety_check' tool with the exact command before returning the final response.
2. If the user explicitly asks for a joke, conversational chat, or general AI explanation (not a terminal action), set action_type to 'chat', provide the text in 'content', set is_destructive to false, and explanation to empty.
3. If you are not calling a tool (or after you receive the tool results), you must ONLY respond with a valid JSON object. Do not include any markdown formatting (NO ```json blocks), no thoughts, and no extra text.
   The JSON structure must be:
   {
     "action_type": "command" | "chat" | "error",
     "content": "the bash command OR the text reply/joke OR the error explanation",
     "is_destructive": true | false,
     "explanation": "the explanation returned by the safety tool, or empty if not a command"
   }
4. If the request is impossible, unachievable in a CLI shell, or contains physical actions/nonsense commands (like "jump high", "fly to the moon"), you MUST set action_type to "error" and explain in the content that as an AI CLI assistant you cannot perform physical or impossible tasks."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_instruction}
    ]

    try:
        # First turn: Send user input and register the tools
        response = litellm.completion(
            model=model,
            custom_llm_provider=provider,
            messages=messages,
            tools=tools,
            system_instruction=system_prompt,
            temperature=0.0
        )
        
        message = response.choices[0].message
        
        # Check if the LLM decided to call our safety tool (matches ACDL conditional block)
        if message.get("tool_calls"):
            tool_call = message["tool_calls"][0]
            tool_name = tool_call.function.name
            tool_args = json.loads(tool_call.function.arguments)
            
            # Execute the local function corresponding to the tool
            if tool_name == "call_safety_check":
                command_to_test = tool_args.get("command")
                # Call local safety tool logic
                tool_result = _check_command_safety(command_to_test)
                
                # Append assistant's intent to call tool, and the tool's feedback to the thread
                messages.append(message)
                messages.append({
                    "role": "tool",
                    "name": tool_name,
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(tool_result)
                })
                
                # Second turn: Send the tool result back to the LLM so it can construct the final JSON
                second_turn_prompt = """You are the brain of a CLI agent named 'doit'.
You have received the safety check result for the command.
You MUST respond with a valid JSON object. Do not include any markdown formatting, no thoughts, and no extra text.
You MUST output a JSON response of action_type "command" containing the exact command in "content", and the tool's returned "is_destructive" and "explanation" values.
The JSON structure must be:
{
  "action_type": "command",
  "content": "<the exact command that was tested>",
  "is_destructive": true | false,
  "explanation": "<the explanation from the safety tool>"
}
DO NOT refuse the command, do not return an error, and do not write chat messages. Copy the values from the tool exactly."""
                messages[0]["content"] = second_turn_prompt

                second_response = litellm.completion(
                    model=model,
                    custom_llm_provider=provider,
                    messages=messages,
                    system_instruction=second_turn_prompt,
                    temperature=0.0
                )
                
                raw_content = second_response.choices[0].message.content
            else:
                raw_content = message.content
        else:
            raw_content = message.content

        cleaned_content = clean_json_markdown(raw_content)
        return json.loads(cleaned_content)
        
    except Exception as e:
        return {
            "action_type": "error",
            "content": f"Failed to get or parse response from model ({model}). Error: {str(e)}",
            "is_destructive": False,
            "explanation": ""
        }