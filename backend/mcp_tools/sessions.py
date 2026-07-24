"""Session activity and event log tools."""

from mcp.types import Tool

import client

TOOLS = [
    Tool(
        name="add_session",
        description="Record that a session worked on a card",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "card_id": {"type": "string"},
                "session_id": {"type": "string"},
                "system": {"type": "string", "description": "System/agent name"},
                "action": {"type": "string", "description": "What the session did"},
                "outcome": {"type": "string", "enum": ["success", "failed", "partial"]},
            },
            "required": ["board_id", "card_id", "session_id"],
        },
    ),
    Tool(
        name="get_events",
        description="Get recent events/audit log for a board",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["board_id"],
        },
    ),
]


def dispatch(name: str, arguments: dict):
    match name:
        case "add_session":
            return client.add_session(
                arguments["board_id"],
                arguments["card_id"],
                arguments["session_id"],
                system=arguments.get("system", ""),
                action=arguments.get("action", ""),
                outcome=arguments.get("outcome"),
            )
        case "get_events":
            return client.get_events(arguments["board_id"], arguments.get("limit", 20))
        case _:
            raise KeyError(name)
