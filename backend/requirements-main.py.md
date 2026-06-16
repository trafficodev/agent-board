# Requirements for main.py

Source of truth: user prompts only. Do not add anything not literally stated by the user.

## Requirements

- [2026-05-22] Board is standalone — no dependency on Better Claude app
  - Source: "its the other way around, the agent kanban doesnt know about better agent app, better agent agents are responsible of adding the session to the history (withing the cli/mcp tool the agent would use)"
- [2026-05-22] Better Claude agents push session history to the board via CLI/MCP tool; the board doesn't pull from Better Claude
  - Source: same as above
