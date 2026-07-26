#!/usr/bin/env bash

# Path to the shell execution log file
DOIT_SHELL_LOG="$HOME/.doit/shell_history.log"
mkdir -p "$HOME/.doit"

_doit_log_command() {
    # Get the last executed command from history
    local LAST_CMD
    LAST_CMD=$(fc -ln -1 2>/dev/null | sed 's/^[[:space:]]*//')

    # Skip logging if the command is empty or starts with 'doit' (to prevent duplicates)
    if [[ -z "$LAST_CMD" ]] || [[ "$LAST_CMD" =~ ^doit([[:space:]]|$) ]]; then
        return
    fi

    # Avoid logging the exact same command repeatedly in a row
    if [[ "$LAST_CMD" == "$_DOIT_LAST_LOGGED_CMD" ]]; then
        return
    fi
    _DOIT_LAST_LOGGED_CMD="$LAST_CMD"

    # Capture timestamp and current working directory
    local TIMESTAMP
    TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    local CURRENT_PWD="$PWD"

    # Safely escape command and path strings into valid JSON formats
    local ESCAPED_CMD
    ESCAPED_CMD=$(python3 -c "import json, sys; print(json.dumps(sys.argv[1]))" "$LAST_CMD" 2>/dev/null)
    local ESCAPED_PWD
    ESCAPED_PWD=$(python3 -c "import json, sys; print(json.dumps(sys.argv[1]))" "$CURRENT_PWD" 2>/dev/null)

    # Append valid JSON entry to the log file
    if [[ -n "$ESCAPED_CMD" ]] && [[ -n "$ESCAPED_PWD" ]]; then
        echo "{\"timestamp\": \"$TIMESTAMP\", \"pwd\": $ESCAPED_PWD, \"command\": $ESCAPED_CMD}" >> "$DOIT_SHELL_LOG"
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