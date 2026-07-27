import os
import json
from datetime import datetime
import history_manager

LOG_FILE = os.path.expanduser("~/.doit/shell_history.log")


def _parse_timestamp(ts_str: str) -> float:
    """
    Parses an ISO timestamp string into a float epoch timestamp for sorting.
    Handles ISO 8601 strings with or without trailing 'Z'.
    """
    if not ts_str:
        return 0.0
    try:
        # Standardize 'Z' suffix for Python datetime parsing
        clean_ts = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(clean_ts).timestamp()
    except Exception:
        return 0.0


def get_recent_shell_commands(limit: int = 30) -> list:
    """
    Reads raw shell execution entries from ~/.doit/shell_history.log.
    Returns a list of parsed JSON objects containing timestamp, session_id, pwd, and command.
    """
    if not os.path.exists(LOG_FILE):
        return []

    entries = []
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
            for line in lines[-limit:]:
                line = line.strip()
                if line:
                    try:
                        parsed = json.loads(line)
                        entries.append(parsed)
                    except json.JSONDecodeError:
                        continue
    except Exception:
        return []

    return entries


def get_session_aware_timeline(limit: int = 15) -> tuple[str, str]:
    """
    Combines user terminal execution logs and agent interaction turns,
    sorts them strictly by timestamp, and splits them into two formatted timelines:
    1. Activity in the CURRENT terminal window/session.
    2. Activity in OTHER terminal windows/sessions.
    """
    current_session = os.environ.get("DOIT_SESSION_ID", "default_session")
    shell_entries = get_recent_shell_commands(limit=limit * 2)
    agent_turns = history_manager.get_all_turns()

    agent_executed_cmds = set()
    events = []

    # Process agent turns from history_manager
    for turn in agent_turns:
        ts = _parse_timestamp(turn.get("timestamp", ""))
        session_id = turn.get("session_id", "default_session")
        resp = turn.get("assistant_response", {})
        action_type = resp.get("action_type")
        content = resp.get("content", "").strip()

        if action_type == "command" and content:
            agent_executed_cmds.add(content)
            events.append({
                "timestamp": ts,
                "session_id": session_id,
                "source": "AGENT",
                "pwd": None,
                "text": content
            })

    # Process raw shell entries
    for entry in shell_entries:
        cmd = entry.get("command", "").strip()
        pwd = entry.get("pwd", "")
        ts = _parse_timestamp(entry.get("timestamp", ""))
        session_id = entry.get("session_id", "default_session")

        if not cmd:
            continue

        source = "AGENT" if cmd in agent_executed_cmds else "USER"
        events.append({
            "timestamp": ts,
            "session_id": session_id,
            "source": source,
            "pwd": pwd,
            "text": cmd
        })

    # Sort all merged events strictly in ascending order by timestamp
    events.sort(key=lambda x: x["timestamp"])

    current_window_lines = []
    other_windows_lines = []

    for event in events:
        session = event.get("session_id", "default_session")
        source_tag = "[AGENT (doit)]" if event["source"] == "AGENT" else "[USER]"
        pwd_str = f" (in {event['pwd']})" if event.get("pwd") else ""
        line = f"- {source_tag}{pwd_str}: {event['text']}"

        if session == current_session:
            current_window_lines.append(line)
        else:
            other_windows_lines.append(f"- [WINDOW/SESSION: {session}]{pwd_str}: {event['text']}")

    current_timeline = "\n".join(current_window_lines[-limit:]) or "No recent activity recorded in this terminal window."
    other_timeline = "\n".join(other_windows_lines[-limit:]) or "No recent activity recorded in other terminal windows."

    return current_timeline, other_timeline


# Alias for backward compatibility if needed
get_combined_timeline = get_session_aware_timeline