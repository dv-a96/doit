# llm_brain.py
import os
import json
import re
import litellm
import warnings
import history_manager
import memory_manager

# Suppress Pydantic serialization warnings caused by LiteLLM / Gemini tool call formats
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

# Silencing LiteLLM output and debugging helpers
litellm.suppress_warnings = True
os.environ["LITELLM_LOG"] = "ERROR"

def load_config() -> dict:
    """Load configuration from ~/doit.cfg."""
    cfg_path = os.path.expanduser("~/doit.cfg")
    defaults = {
        "model": "gemini-2.5-flash",
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
    """Safely removes markdown code blocks from the model's response."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _check_command_safety(command: str) -> dict:
    """Analyzes a generated bash command to check if it modifies the filesystem."""
    command_clean = command.strip().split()
    if not command_clean:
        return {"is_destructive": False, "explanation": "Empty command."}
        
    base_cmd = command_clean[0]
    safe_commands = {"ls", "grep", "cat", "pwd", "echo", "head", "tail", "find", "diff", "env"}
    has_redirection = any(char in command for char in (">", ">>", "|"))

    if base_cmd in safe_commands and not has_redirection:
        return {
            "is_destructive": False,
            "explanation": f"The command '{base_cmd}' only reads or displays information."
        }

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


def build_messages_with_history(system_prompt: str, user_instruction: str) -> list:
    """
    Combines system prompt, saved history turns, and the current user instruction
    into a structured list of messages for the LLM, properly formatting past tool calls.
    """
    turns = history_manager.get_all_turns()
    messages = [{"role": "system", "content": system_prompt}]
    
    for turn in turns:
        # 1. Add user's original request
        messages.append({"role": "user", "content": turn["user_instruction"]})
        
        # 2. Reconstruct past clarification tool interactions if they exist
        assistant_resp = turn["assistant_response"]
        if "clarification_history" in assistant_resp:
            for item in assistant_resp["clarification_history"]:
                # Reconstruct a clean, API-compliant tool call dictionary structure
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": item["tool_call_id"],
                        "type": "function",
                        "function": {
                            "name": "ask_user_clarification",
                            "arguments": json.dumps(item["arguments"])
                        }
                    }]
                })
                # Append the simulated user response as tool output
                messages.append({
                    "role": "tool",
                    "name": "ask_user_clarification",
                    "tool_call_id": item["tool_call_id"],
                    "content": json.dumps({"user_response": item["user_response"]})
                })
        
        # 3. Add assistant's final JSON response (as a string)
        # Remove the internal history key before sending to LLM to keep context clean
        clean_assistant_resp = {k: v for k, v in assistant_resp.items() if k != "clarification_history"}
        messages.append({"role": "assistant", "content": json.dumps(clean_assistant_resp)})
        
        # 4. Add execution output feedback (if exists)
        exec_res = turn.get("execution_result")
        if exec_res:
            feedback = (
                f"System execution output of the command was:\n"
                f"Exit Code: {exec_res.get('returncode')}\n"
                f"STDOUT: {exec_res.get('stdout', '').strip()}\n"
                f"STDERR: {exec_res.get('stderr', '').strip()}"
            )
            messages.append({
                "role": "user", 
                "content": feedback
            })
            
    # Finally, append the current user instruction
    messages.append({"role": "user", "content": user_instruction})
    return messages


def query_llm(user_instruction: str) -> dict:
    """Send the request to the LLM including multi-turn history context."""
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

    # Internal list to collect clarification steps for history persistence
    clarification_history = []

    # Load formatted active memories to inject into System Prompt
    memories_context = memory_manager.get_formatted_memories(user_instruction)

    # Define tools available for the LLM
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
                            "description": "The exact bash command to be executed"
                        }
                    },
                    "required": ["command"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "ask_user_clarification",
                "description": "Call this tool when you are not sure about the user's intent, when there are multiple logical options, or when critical details are missing. You MUST provide explicit, numbered choices for the user.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The clarification question context to print to the user. E.g., 'Do you want to sort by:'"
                        },
                        "options": {
                            "type": "array",
                            "items": {
                                "type": "string"
                            },
                            "description": "A list of explicit options the user can choose from. You MUST provide at least 2 distinct, actionable options representing the possible interpretations. Do not leave this empty."
                        }
                    },
                    "required": ["question", "options"]
                }
            }
        }
    ]

    system_prompt = f"""You are the brain of a CLI agent named 'doit'. You support multi-turn conversations and rich interactions.
Analyze interaction history and stored persistent user memories to resolve contexts, paths, and preferences.

PERSISTENT USER MEMORIES:
{memories_context}

STRICT RULES:

1. MEMORY AWARENESS:
   - Use the PERSISTENT USER MEMORIES list above to resolve shortcuts, project locations, or user preferences (e.g., if memory says "LLM project is ~/school/llms/ass3", use that exact path when asked to navigate or act on it).

2. NON-COMMANDS / EXPLANATIONAL REQUESTS:
   - If the user asks "how to...", "how do I...", "explain...", or asks for information/options without explicitly demanding execution:
     a) If the request is GENERAL or AMBIGUOUS with multiple distinct formats/methods:
        YOU MUST CALL 'ask_user_clarification' FIRST.
     b) Once clarified, set action_type to "chat" and provide focused explanation in 'content'.

3. COMMAND EXECUTION REQUESTS & FOLLOW-UPS:
   - If the user explicitly asks to RUN or EXECUTE a terminal action:
     a) Set action_type to "command", call 'call_safety_check' on the exact final bash command, and return the JSON.

4. PATH RESOLUTION:
   - Always use full or explicit paths based on memories or history.

5. OUTPUT FORMAT:
   - Respond ONLY with valid JSON (NO markdown/code blocks):
   {{
     "action_type": "command" | "chat" | "error",
     "content": "the bash command OR the text explanation/answer OR the error message",
     "is_destructive": true | false,
     "explanation": "the explanation returned by the safety tool, or empty if not a command"
   }}"""
    messages = build_messages_with_history(system_prompt, user_instruction)

    try:
        while True:
            response = litellm.completion(
                model=model,
                custom_llm_provider=provider,
                messages=messages,
                tools=tools,
                system_instruction=system_prompt,
                temperature=0.0
            )
            
            message = response.choices[0].message
            
            if not message.get("tool_calls"):
                raw_content = message.content
                break
                
            tool_call = message["tool_calls"][0]
            tool_name = tool_call.function.name
            tool_args = json.loads(tool_call.function.arguments)
            
            messages.append(message)
            tool_result_content = ""
            
            if tool_name == "call_safety_check":
                command_to_test = tool_args.get("command")
                tool_result = _check_command_safety(command_to_test)
                tool_result_content = json.dumps(tool_result)
                
                second_turn_prompt = """You are the brain of a CLI agent named 'doit'.
You have received the safety check result for the command.
You MUST respond with a valid JSON object. Do not include any markdown formatting, no thoughts, and no extra text.
You MUST output a JSON response of action_type "command" containing the exact command in "content", and the tool's returned "is_destructive" and "explanation" values."""
                messages[0]["content"] = second_turn_prompt
                
            elif tool_name == "ask_user_clarification":
                question = tool_args.get("question")
                options = tool_args.get("options", [])
                
                print(f"\n🤔 {question}")
                if options:
                    for idx, opt in enumerate(options, start=1):
                        print(f"{idx}. {opt}")
                
                try:
                    user_answer = input("\nYour answer (or press Enter to cancel): ").strip()
                except (KeyboardInterrupt, EOFError):
                    print("\nOperation cancelled by user.")
                    return {"action_type": "error", "content": "Cancelled", "is_destructive": False, "explanation": ""}
                
                if not user_answer:
                    print("Operation cancelled.")
                    return {"action_type": "error", "content": "Cancelled", "is_destructive": False, "explanation": ""}
                
                if options and user_answer.isdigit():
                    choice_idx = int(user_answer) - 1
                    if 0 <= choice_idx < len(options):
                        user_answer = options[choice_idx]
                
                # Safely capture ONLY primitive properties from the tool call to ensure valid JSON serialization
                clarification_history.append({
                    "tool_call_id": tool_call.id,
                    "arguments": tool_args,
                    "user_response": user_answer
                })
                
                tool_result_content = json.dumps({"user_response": user_answer})
            
            messages.append({
                "role": "tool",
                "name": tool_name,
                "tool_call_id": tool_call.id,
                "content": tool_result_content
            })

        cleaned_content = clean_json_markdown(raw_content)
        final_response_dict = json.loads(cleaned_content)
        
        # Embed the structural clarification history inside the final response dict before saving
        if clarification_history:
            final_response_dict["clarification_history"] = clarification_history
        
        # Save to history file
        history_manager.add_turn(user_instruction, final_response_dict)
        
        return final_response_dict
        
    except Exception as e:
        return {
            "action_type": "error",
            "content": f"Failed to get or parse response from model ({model}). Error: {str(e)}",
            "is_destructive": False,
            "explanation": ""
        }