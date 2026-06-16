# Requirements for card_store.py

Source of truth: user prompts only. Do not add anything not literally stated by the user.

## Requirements

- [2026-05-22] Board does not depend on or know about the Better Claude app; external agents push session history to the board
  - Source: "its the other way around, the agent kanban doesnt know about better agent app, better agent agents are responsible of adding the session to the history (withing the cli/mcp tool the agent would use)"
