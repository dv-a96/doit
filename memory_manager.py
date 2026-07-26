# memory_manager.py
import os
import json
import threading
from datetime import datetime

MEMORY_DIR = os.path.expanduser("~/.doit")
MEMORY_FILE = os.path.join(MEMORY_DIR, "memories.json")

# Lock to prevent race conditions during concurrent file access between threads
_lock = threading.Lock()

def init_memory():
    """Ensure the ~/.doit directory and memories.json exist."""
    if not os.path.exists(MEMORY_DIR):
        os.makedirs(MEMORY_DIR, exist_ok=True)
    if not os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump({"memories": []}, f, indent=2)

# Automatically initialize on import
init_memory()

def load_memories() -> list:
    """Load all persistent memories safely from the JSON file."""
    with _lock:
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("memories", [])
        except (json.JSONDecodeError, FileNotFoundError):
            return []

def save_memories(memories_list: list):
    """Save the updated memories list back to the persistent file safely."""
    with _lock:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump({"memories": memories_list}, f, indent=2, ensure_ascii=False)

def add_memory(fact: str) -> dict:
    """Add a new memory fact if it doesn't already exist."""
    memories = load_memories()
    
    # Avoid exact duplicate entries
    for mem in memories:
        if mem["fact"].strip().lower() == fact.strip().lower():
            return mem

    new_mem = {
        "id": f"mem_{int(datetime.now().timestamp() * 1000)}",
        "fact": fact.strip(),
        "created_at": datetime.now().isoformat()
    }
    memories.append(new_mem)
    save_memories(memories)
    return new_mem

def remove_memory_by_id(memory_id: str):
    """Remove a specific memory by its ID."""
    memories = load_memories()
    memories = [m for m in memories if m.get("id") != memory_id]
    save_memories(memories)

def get_formatted_memories(user_instruction: str = "") -> str:
    """
    Returns a formatted string of memories for system prompts.
    Performs basic keyword-matching filtering if the list grows large.
    """
    memories = load_memories()
    if not memories:
        return "No memories stored yet."

    # If the total memory count is small (<= 15), pass all memories to maximize context
    if len(memories) <= 15 or not user_instruction:
        selected_memories = memories
    else:
        # Basic keyword relevance scoring against user instruction
        keywords = set(user_instruction.lower().split())
        scored = []
        for m in memories:
            fact_words = set(m["fact"].lower().split())
            overlap = len(keywords.intersection(fact_words))
            scored.append((overlap, m))
        
        scored.sort(key=lambda x: x[0], reverse=True)
        # Select top 10 most relevant memories
        selected_memories = [m for score, m in scored[:10]]

    lines = [f"- [{m['id']}] {m['fact']}" for m in selected_memories]
    return "\n".join(lines)