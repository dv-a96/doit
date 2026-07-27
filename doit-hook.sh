#!/usr/bin/env bash

# Path to the shell execution log file
DOIT_SHELL_LOG="$HOME/.doit/shell_history.log"
MAX_LOG_LINES=500
TRIM_TO_LINES=200
mkdir -p "$HOME/.doit"

# -----------------------------------------------------------------------------
# Step 1: Assign a unique Session ID for each terminal window/tab
# -----------------------------------------------------------------------------
if [ -z "$DOIT_SESSION_ID" ]; then
    export DOIT_SESSION_ID="session_$(date +%s)_$$"
fi

_doit_log_command() {
    # Get the last executed command from history
    local LAST_CMD
    LAST_CMD=$(fc -ln -1 2>/dev/null | sed 's/^[[:space:]]*//')

    # Skip logging if the command is empty or starts with 'doit'
    if [[ -z "$LAST_CMD" ]] || [[ "$LAST_CMD" =~ ^doit([[:space:]]|$) ]]; then
        return
    fi

    # Avoid logging the exact same command repeatedly in a row
    if [[ "$LAST_CMD" == "$_DOIT_LAST_LOGGED_CMD" ]]; then
        return
    fi
    _DOIT_LAST_LOGGED_CMD="$LAST_CMD"

    # Capture timestamp, current working directory, and session_id
    local TIMESTAMP
    TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    local CURRENT_PWD="$PWD"
    local SESSION_ID="${DOIT_SESSION_ID:-default_session}"

    # Safely escape command, path, and session strings into valid JSON formats
    local ESCAPED_CMD
    ESCAPED_CMD=$(python3 -c "import json, sys; print(json.dumps(sys.argv[1]))" "$LAST_CMD" 2>/dev/null)
    local ESCAPED_PWD
    ESCAPED_PWD=$(python3 -c "import json, sys; print(json.dumps(sys.argv[1]))" "$CURRENT_PWD" 2>/dev/null)
    local ESCAPED_SESSION
    ESCAPED_SESSION=$(python3 -c "import json, sys; print(json.dumps(sys.argv[1]))" "$SESSION_ID" 2>/dev/null)

    # Append valid JSON entry to the log file with session_id
    if [[ -n "$ESCAPED_CMD" ]] && [[ -n "$ESCAPED_PWD" ]] && [[ -n "$ESCAPED_SESSION" ]]; then
        echo "{\"timestamp\": \"$TIMESTAMP\", \"session_id\": $ESCAPED_SESSION, \"pwd\": $ESCAPED_PWD, \"command\": $ESCAPED_CMD}" >> "$DOIT_SHELL_LOG"
    fi

    # Rotate/trim log file if it exceeds MAX_LOG_LINES
    if [ -f "$DOIT_SHELL_LOG" ]; then
        local LINE_COUNT
        LINE_COUNT=$(wc -l < "$DOIT_SHELL_LOG" 2>/dev/null || echo 0)
        if [ "$LINE_COUNT" -gt "$MAX_LOG_LINES" ]; then
            local TEMP_LOG="${DOIT_SHELL_LOG}.tmp"
            tail -n "$TRIM_TO_LINES" "$DOIT_SHELL_LOG" > "$TEMP_LOG" 2>/dev/null
            mv "$TEMP_LOG" "$DOIT_SHELL_LOG" 2>/dev/null
        fi
    fi
}

# Register the hook function to PROMPT_COMMAND for Bash shell
if [ -n "$BASH_VERSION" ]; then
    if [[ ";$PROMPT_COMMAND;" != *";_doit_log_command;"* ]]; then
        PROMPT_COMMAND="_doit_log_command; $PROMPT_COMMAND"
    fi
# Register the hook function to precmd for Zsh shell
elif [ -n "$ZSH_VERSION" ]; then
    autoload -Uz add-zsh-hook
    add-zsh-hook precmd _doit_log_command
fi