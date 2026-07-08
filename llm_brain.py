import os
import json
import litellm

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
        
        return json.loads(cleaned_content)
        
    except Exception as e:
        return {
            "action_type": "error",
            "content": f"Failed to get or parse response from model ({model}). Error: {str(e)}"
        }