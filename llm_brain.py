# llm_brain.py
import os
import json
import re
import litellm
import logging
import warnings
import history_manager
import memory_manager
import shell_history_manager

# Suppress Pydantic serialization warnings caused by LiteLLM / Gemini tool call formats
warnings.filterwarnings("ignore")
# Silence LiteLLM specific internal loggers
logging.getLogger("LiteLLM").setLevel(logging.ERROR)
logging.getLogger("LiteLLM Router").setLevel(logging.ERROR)
logging.getLogger("LiteLLM Proxy").setLevel(logging.ERROR)
# Silencing LiteLLM output and debugging helpers
os.environ["LITELLM_LOG"] = "ERROR"
os.environ["LITELLM_SUPPRESS_LOGGING"] = "YES"
litellm.suppress_warnings = True

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
    Combines system prompt, session-filtered conversation turns, and current user instruction.
    Session Scoping: Only historical turns belonging to active DOIT_SESSION_ID are injected directly.
    """
    turns = history_manager.get_all_turns()
    current_session = os.environ.get("DOIT_SESSION_ID", "default_session")
    
    messages = [{"role": "system", "content": system_prompt}]
    
    for turn in turns:
        # Session scoping filter
        turn_session = turn.get("session_id", "default_session")
        if turn_session != current_session:
            continue
            
        # 1. Add user's original request
        messages.append({"role": "user", "content": turn["user_instruction"]})
        
        # 2. Reconstruct past clarification tool interactions
        assistant_resp = turn["assistant_response"]
        if "clarification_history" in assistant_resp:
            for item in assistant_resp["clarification_history"]:
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
                messages.append({
                    "role": "tool",
                    "name": "ask_user_clarification",
                    "tool_call_id": item["tool_call_id"],
                    "content": json.dumps({"user_response": item["user_response"]})
                })
        
        # 3. Add assistant's final JSON response
        clean_assistant_resp = {k: v for k, v in assistant_resp.items() if k != "clarification_history"}
        messages.append({"role": "assistant", "content": json.dumps(clean_assistant_resp)})
        
        # 4. Add execution output feedback
        exec_res = turn.get("execution_result")
        if exec_res:
            stdout_clean = exec_res.get('stdout', '').strip() or "None"
            stderr_clean = exec_res.get('stderr', '').strip() or "None"
            
            feedback = (
                f"[PREVIOUS COMMAND EXECUTION RESULT]\n"
                f"- Return Code: {exec_res.get('returncode')}\n"
                f"- Standard Output (STDOUT): {stdout_clean}\n"
                f"- Standard Error (STDERR): {stderr_clean}"
            )
            messages.append({
                "role": "user", 
                "content": feedback
            })
            
    # Append current active instruction
    messages.append({"role": "user", "content": user_instruction})
    return messages


def query_llm(user_instruction: str) -> dict:
    """Send the request to the LLM including multi-turn history, user memories, and live shell activity."""
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

    clarification_history = []
    memories_context = memory_manager.get_formatted_memories(user_instruction)

    current_pwd = os.getcwd()
    current_session_id = os.environ.get("DOIT_SESSION_ID", "default_session")
    current_timeline, other_timeline = shell_history_manager.get_session_aware_timeline(limit=10)

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
                "description": "Call this tool when you are not sure about the user's intent, when there are multiple logical options, or when critical details are missing.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The clarification question context to print to the user."
                        },
                        "options": {
                            "type": "array",
                            "items": {
                                "type": "string"
                            },
                            "description": "A list of explicit options the user can choose from."
                        }
                    },
                    "required": ["question", "options"]
                }
            }
        }
    ]

    system_prompt = f"""You are the brain of a CLI agent named 'doit'. You support multi-turn conversations and multi-tasking across separate terminal windows.

CURRENT TERMINAL STATE:
- Session ID: {current_session_id}
- Current Working Directory (PWD): {current_pwd}

RECENT ACTIVITY IN THIS TERMINAL WINDOW:
{current_timeline}

RECENT ACTIVITY IN OTHER TERMINAL WINDOWS:
{other_timeline}

PERSISTENT USER MEMORIES:
{memories_context}

STRICT RULES:
1. CONTEXT SCOPING:
   - Resolve pronouns or implicit commands relative to RECENT ACTIVITY IN THIS TERMINAL WINDOW.
   - Ignore actions in OTHER terminal windows unless user explicitly asks to repeat/refer to them.

2. COMMAND EXECUTION:
   - If user demands an action in terminal, set action_type to "command" and call 'call_safety_check'.

3. NON-COMMANDS:
   - If user asks for info/explanations without execution, set action_type to "chat".

4. OUTPUT FORMAT:
   - Respond ONLY with valid JSON:
   {{
     "action_type": "command" | "chat" | "error",
     "content": "the bash command OR explanation OR error message",
     "is_destructive": true | false,
     "explanation": "safety evaluation details"
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
You MUST respond with a valid JSON object. Do not include markdown formatting.
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
        
        if clarification_history:
            final_response_dict["clarification_history"] = clarification_history
        
        history_manager.add_turn(user_instruction, final_response_dict)
        return final_response_dict
        
    except Exception as e:
        return {
            "action_type": "error",
            "content": f"Failed to get or parse response from model ({model}). Error: {str(e)}",
            "is_destructive": False,
            "explanation": ""
        }


# -----------------------------------------------------------------------------
# 🔧 AUTOMATIC SELF-CORRECTION DIAGNOSTIC FUNCTION
# -----------------------------------------------------------------------------
def handle_self_correction(failed_command: str, stderr: str, returncode: int) -> dict:
    """
    Analyzes a failed command execution, diagnoses the root cause, and generates a corrected command.
    """
    config = load_config()
    model = config["model"]
    provider = config["provider"]

    correction_system_prompt = """You are an expert Linux System Administrator and Self-Correction Agent for 'doit'.
Your task is to analyze a failed bash command and generate a corrected command to fix the issue.

Output MUST be a single valid JSON object with NO markdown formatting:
{
  "should_fix": true | false,
  "explanation": "Brief description of why the command failed and how the fix resolves it",
  "fixed_command": "The exact corrected bash command to execute"
}

Rules:
1. ALWAYS set 'should_fix' to true if there is ANY logical recovery path (e.g., if a directory/file is missing, propose creating it with 'mkdir -p' or 'touch' before running the original command; if a command/flag is misspelled, fix it; if permissions fail, add 'sudo').
2. Only set 'should_fix' to false if the command is completely meaningless or impossible in a Linux environment.
"""

    user_payload = f"""FAILED COMMAND: {failed_command}
EXIT CODE: {returncode}
ERROR STDERR: {stderr}"""

    try:
        response = litellm.completion(
            model=model,
            custom_llm_provider=provider,
            messages=[
                {"role": "system", "content": correction_system_prompt},
                {"role": "user", "content": user_payload}
            ],
            system_instruction=correction_system_prompt,
            temperature=0.0
        )
        raw_content = response.choices[0].message.content
        cleaned = clean_json_markdown(raw_content)
        return json.loads(cleaned)
    except Exception as e:
        # Print error for debugging if needed
        return {"should_fix": False, "explanation": f"Error: {str(e)}", "fixed_command": ""}