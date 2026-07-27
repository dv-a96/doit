# history_manager.py
import os
import json
from datetime import datetime

HISTORY_DIR = os.path.expanduser("~/.doit")
HISTORY_FILE = os.path.join(HISTORY_DIR, "history.json")
MAX_HISTORY_TURNS = 10  # Limit to prevent excessive context window loading

def init_history():
    """Ensure the ~/.doit directory and history.json file exist."""
    if not os.path.exists(HISTORY_DIR):
        os.makedirs(HISTORY_DIR, exist_ok=True)
    if not os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump({"turns": []}, f, indent=2)

# Automatically initialize history when the module is imported
init_history()

def load_history() -> dict:
    """Load history from the JSON file."""
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {"turns": []}

def save_history(history_data: dict):
    """Save history back to the file."""
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history_data, f, indent=2, ensure_ascii=False)

def clear_history():
    """Clear all saved history."""
    save_history({"turns": []})

def get_all_turns() -> list:
    """Return the raw list of history turns."""
    return load_history().get("turns", [])

def add_turn(user_instruction: str, assistant_response: dict):
    """
    Add a new interaction turn with session tracking. 
    'assistant_response' is the JSON output dictionary from the LLM.
    """
    history = load_history()
    session_id = os.environ.get("DOIT_SESSION_ID", "default_session")
    
    new_turn = {
        "timestamp": datetime.now().isoformat(),
        "session_id": session_id,
        "user_instruction": user_instruction,
        "assistant_response": assistant_response,
        "execution_result": None  # Will be updated by the main process after execution
    }
    
    history["turns"].append(new_turn)
    
    # Keep only the last N turns
    if len(history["turns"]) > MAX_HISTORY_TURNS:
        history["turns"] = history["turns"][-MAX_HISTORY_TURNS:]
        
    save_history(history)

def update_last_turn_execution(execution_result: dict):
    """Update the last saved turn with its shell execution output."""
    history = load_history()
    if history["turns"]:
        history["turns"][-1]["execution_result"] = execution_result
        save_history(history)