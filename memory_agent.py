# memory_agent.py
import json
import litellm
import memory_manager
from llm_brain import load_config, clean_json_markdown

def process_memory_in_background(user_instruction: str):
    """
    Background worker function that analyzes if the user input contains
    long-term facts, path mappings, or updates/deletions for existing memories.
    Runs asynchronously without delaying the primary CLI agent execution.
    """
    try:
        existing_memories = memory_manager.get_formatted_memories()
        config = load_config()
        model = config["model"]
        provider = config["provider"]

        system_prompt = """You are a dedicated Background Memory Agent for a CLI tool named 'doit'.
Your ONLY task is to extract persistent user facts, personal context (e.g., family events, user details, preferences), project path mappings, or terminal instructions to remember.

CURRENT EXISTING MEMORIES:
{existing_memories}

INSTRUCTIONS:
1. Analyze the user's input.
2. Determine if the user is stating ANY long-term fact, personal information, event context (e.g., "my mom is turning 50 in a week", "my name is John", "this is my project folder"), OR updating/invalidating an existing memory.
3. DO NOT store routine CLI commands or standard status checks (e.g., "list files", "show status").

Output MUST be a single valid JSON object (no markdown):
{
  "has_memory_action": true | false,
  "action": "add" | "delete" | "update" | "none",
  "memory_id_to_remove": "ID of memory if updating/deleting, else null",
  "new_fact_to_save": "Clear, concise sentence stating the fact to remember, else null"
}"""

        response = litellm.completion(
            model=model,
            custom_llm_provider=provider,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Analyze input for memory updates: {user_instruction}"}
            ],
            system_instruction=system_prompt,
            temperature=0.0
        )

        raw_content = response.choices[0].message.content
        cleaned = clean_json_markdown(raw_content)
        result = json.loads(cleaned)

        if not result.get("has_memory_action"):
            return

        action = result.get("action")
        mem_id_to_remove = result.get("memory_id_to_remove")
        new_fact = result.get("new_fact_to_save")

        # Execute memory changes on disk
        if mem_id_to_remove:
            memory_manager.remove_memory_by_id(mem_id_to_remove)

        if action in ("add", "update") and new_fact:
            memory_manager.add_memory(new_fact)

    except Exception:
        # Background agent fails silently to avoid interrupting the main CLI flow
        pass