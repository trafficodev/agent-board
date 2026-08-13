"""Voteable card changelog tools."""

from mcp.types import Tool

from .. import client


TOOLS = [
    Tool(
        name="get_card_changes",
        description="Get a card's voteable field-level diffs and effective vote totals",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "card_id": {"type": "string"},
                "provider": {"type": "string"},
                "native_session_id": {"type": "string"},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 200},
            },
            "required": ["board_id", "card_id"],
        },
    ),
    Tool(
        name="set_change_vote",
        description=(
            "Append an up/down vote for one card change. The native session and "
            "full reviewed commit SHA are required and stored on every vote."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "card_id": {"type": "string"},
                "target_id": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "direction": {"type": "integer", "enum": [-1, 1]},
                "provider": {"type": "string"},
                "native_session_id": {"type": "string"},
                "reviewed_commit_sha": {
                    "type": "string",
                    "pattern": "^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$",
                },
            },
            "required": [
                "board_id",
                "card_id",
                "target_id",
                "direction",
                "provider",
                "native_session_id",
                "reviewed_commit_sha",
            ],
        },
    ),
    Tool(
        name="get_change_vote_audit",
        description="Get the timestamped append-only vote audit for one card change",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "card_id": {"type": "string"},
                "target_id": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 200},
            },
            "required": ["board_id", "card_id", "target_id"],
        },
    ),
]


def dispatch(name: str, arguments: dict):
    match name:
        case "get_card_changes":
            return client.get_card_changes(
                arguments["board_id"],
                arguments["card_id"],
                arguments.get("provider", ""),
                arguments.get("native_session_id", ""),
                arguments.get("offset", 0),
                arguments.get("limit", 200),
            )
        case "set_change_vote":
            return client.set_change_vote(
                arguments["board_id"],
                arguments["card_id"],
                arguments["target_id"],
                arguments["direction"],
                arguments["provider"],
                arguments["native_session_id"],
                arguments["reviewed_commit_sha"],
            )
        case "get_change_vote_audit":
            return client.get_change_vote_audit(
                arguments["board_id"],
                arguments["card_id"],
                arguments["target_id"],
                arguments.get("offset", 0),
                arguments.get("limit", 200),
            )
        case _:
            raise KeyError(name)
