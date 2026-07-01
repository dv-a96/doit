import os
import json
import litellm

def get_current_model() -> str:
    """
    Get the current model name from a configuration file in the user's home directory.
    If the file does not exist or is empty, return a default model name.
    """

    cfg_path = os.path.expanduser("~/doit.cfg")
    
    if os.path.exists(cfg_path):
        with open(cfg_path, "r") as f:
            # Read the model name from the file and strip any whitespace
            model_name = f.read().strip()
            if model_name:
                return model_name
                
    # Default in case the file is missing or empty
    return "openai/gpt-4o-mini"


def query_llm(user_instruction: str) -> dict:
    """
    Send the request to the LLM and return a built-in dictionary with the decision and content.
    """
    model = get_current_model()
    
    system_prompt = """
    You are the brain of a CLI agent named 'doit'.
    You must ONLY respond with a valid JSON object. Do not include any markdown formatting (NO ```json blocks), no thoughts, and no extra text.
    
    The JSON must strictly follow this structure:
    {
      "action_type": "command" | "chat" | "error",
      "content": "the bash command OR the text reply/joke OR the error explanation"
    }
    
    Rules:
    1. If the user wants to do something in the terminal (list files, delete, move, create, network check), set action_type to 'command' and provide the EXACT shell command.
    2. If they ask for a joke, chat, or explanation, set action_type to 'chat' and provide the response text.
    3. If the request is impossible, dangerous, or unachievable in a shell, set action_type to 'error' and explain why.
    """

    try:
        response = litellm.completion(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_instruction}
            ],
            temperature=0.0,
            response_format={"type": "json_object"}  # Ensure the model returns a JSON object
        )
        
        # Extract the raw content from the model's response
        raw_content = response.choices[0].message.content.strip()
        return json.loads(raw_content)
        
    except Exception as e:

        return {
            "action_type": "error",
            "content": f"Failed to get or parse response from model ({model}). Error: {str(e)}"
        }