```mermaid
graph TD
    %% Define Nodes and Styles
    A["1. CLI Entrypoint
    Input: Command Line Args
    Output: Query String (str)"]
    
    B["2. Intent Routing (Turn 1)
    Input: first_turn_prompt + Query String
    Output: tool_calls JSON (OpenAI schema)"]
    
    C["3. Safety Analysis
    Input: Shell Command (str)
    Output: Safety Status JSON (dict)"]
    
    D["4. Formatting (Turn 2)
    Input: second_turn_prompt + Safety Result
    Output: Final Action JSON (str)"]
    
    E["5. Subprocess Execution
    Input: Final Action dict
    Output: terminal output (stdout/stderr)"]

    %% Define Flow
    A --> B
    B --> |If Shell Command| C
    B --> |If Chat or Error| E
    C --> D
    D --> E