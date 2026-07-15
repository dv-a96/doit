```mermaid
graph LR
    A([doit CLI Input]) --> B{Is --clear/ -c flag passed?}
    B -- Yes --> C[Clear ~/.doit/history.json] --> End([Exit])
    B -- No --> D[Load historical turns from history.json]
    D --> E[Construct Messages: System + History turns + Execution Feedback + Current Instruction]
    E --> F[Query LLM brain with historical Messages]
    F --> G[LLM returns response & safety checks]
    G --> H[Save current turn state to history.json]
    H --> I{Is Action Type Command?}
    I -- No [Chat or Error] --> J[Print output to user] --> End
    I -- Yes --> K[Execute Subprocess Command]
    K --> L[Update the last history turn with STDOUT/STDERR/Exit Code]
    L --> M[Print terminal output to user] --> End