import os
import json
import re
import litellm

# Silencing LiteLLM output and debugging helpers
litellm.suppress_warnings = True
os.environ["LITELLM_LOG"] = "ERROR" # Suppress info/warning logs from LiteLLM

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
    Analyzes a generated bash command using a separate, secondary LLM call.
    Determines if the command modifies the filesystem or system state.
    Returns a dict: {"is_destructive": bool, "explanation": str}
    """
    config = load_config()
    model = config["model"]
    provider = config["provider"]
    
    if config.get("gemini_api_key"):
        os.environ["GEMINI_API_KEY"] = config["gemini_api_key"]
        os.environ["GOOGLE_API_KEY"] = config["gemini_api_key"]

    safety_system_prompt = """You are a security compliance tool for a CLI agent.
Your job is to analyze a bash command and decide if it modifies the filesystem or system state (creates, moves, deletes, edits files/directories, installs packages, write modifications, etc.).

Strict Rules:
1. Commands that ONLY display information or read state (like 'ls', 'grep', 'cat', 'pwd', 'echo', 'head', 'find', 'diff') are NOT dangerous/destructive. For these, you MUST set is_destructive to false.
2. Commands that modify the filesystem (like 'rm', 'mv', 'mkdir', 'touch', 'cp', 'chmod', 'chown', or output redirections like '>' and '>>') are dangerous. For these, you MUST set is_destructive to true.

You must ONLY respond with a valid JSON object. Do not include any markdown formatting (NO ```json blocks), no thoughts, and no extra text.
The JSON must strictly follow this structure:
{
  "is_destructive": true | false,
  "explanation": "A short sentence explaining what the command does to the filesystem"
}"""

    try:
        response = litellm.completion(
            model=model,
            custom_llm_provider=provider,
            messages=[
                {"role": "system", "content": safety_system_prompt},
                {"role": "user", "content": f"Analyze this command: {command}"}
            ],
            system_instruction=safety_system_prompt,
            temperature=0.0
        )
        
        raw_content = response.choices[0].message.content
        cleaned_content = clean_json_markdown(raw_content)
        return json.loads(cleaned_content)
        
    except Exception:
        # Fallback to safe mode (assume dangerous) if the LLM call or parsing fails
        return {
            "is_destructive": True,
            "explanation": "Could not verify command safety automatically. Assuming potentially destructive."
        }

def query_llm(user_instruction: str) -> dict:
    """
    Send the request to the LLM and return a built-in dictionary with the decision and content.
    """
    config = load_config()
    model = config["model"]
    provider = config["provider"]
    
    if config.get("gemini_api_key"):
        os.environ["GEMINI_API_KEY"] = config["gemini_api_key"]
        os.environ["GOOGLE_API_KEY"] = config["gemini_api_key"] # Safeguard environment variable
    
    if isinstance(user_instruction, (list, tuple)):
        user_instruction = " ".join(user_instruction)
    else:
        user_instruction = str(user_instruction)

    system_prompt = """You are the brain of a CLI agent named 'doit'.
You must ONLY respond with a valid JSON object. Do not include any markdown formatting (NO ```json blocks), no thoughts, and no extra text.

The JSON must strictly follow this structure:
{
  "action_type": "command" | "chat" | "error",
  "content": "the bash command OR the text reply/joke OR the error explanation"
}

Rules:
1. If the user wants to do something in the terminal (list files, delete, move, create, network check), set action_type to 'command' and provide the EXACT shell command, you should be able to handle a single command per query.
2. If they ask for a joke, chat, or explanation, set action_type to 'chat' and provide the response text.
3. If the request is impossible or unachievable in a shell, set action_type to 'error' and give an explantion why you cannot do it as an ai based command line assistant."""

    try:
        response = litellm.completion(
            model=model,
            custom_llm_provider=provider,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_instruction}
            ],
            # Pass system instructions natively through the parameter Gemini expects
            system_instruction=system_prompt,
            temperature=0.0
        )
        
        raw_content = response.choices[0].message.content
        cleaned_content = clean_json_markdown(raw_content)
        
        parsed_response = json.loads(cleaned_content)
        
        # =====================================================================
        # NEW SAFETY ENRICHMENT STEP
        # =====================================================================
        # If the generated action is indeed a terminal command, invoke the separate safety tool
        if parsed_response.get("action_type") == "command":
            command = parsed_response.get("content", "")
            safety_info = _check_command_safety(command)
            
            # Enrich the response dictionary with the safety flags required by doit
            parsed_response["is_destructive"] = safety_info.get("is_destructive", True)
            parsed_response["explanation"] = safety_info.get("explanation", "")
        else:
            # If it's a chat or error, default safety flags to safe values
            parsed_response["is_destructive"] = False
            parsed_response["explanation"] = ""
            
        return parsed_response
        
    except Exception as e:
        return {
            "action_type": "error",
            "content": f"Failed to get or parse response from model ({model}). Error: {str(e)}",
            "is_destructive": False,
            "explanation": ""
        }