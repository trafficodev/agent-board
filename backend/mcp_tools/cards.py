"""Card CRUD and search tools."""

from mcp.types import Tool

import client

TOOLS = [
    Tool(
        name="list_cards",
        description="List cards on a board, optionally filtered by column, parent, priority, or label",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "column_id": {"type": "string"},
                "parent_id": {"type": "string", "description": "Filter to children of this card. Use 'null' for top-level only."},
                "priority": {"type": "string"},
                "label": {"type": "string"},
            },
            "required": ["board_id"],
        },
    ),
    Tool(
        name="search_cards",
        description="Search cards by text, quoted phrase, negation, fields, file, commit, rg-backed contains, session, priority, label, or hierarchy-visible matches",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "query": {"type": "string", "description": "Examples: file:frontend/src/App.tsx commit:abc1234 contains:renderSessionData has:file has:commit -label:bug"},
                "priority": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                "label": {"type": "string"},
            },
            "required": ["board_id"],
        },
    ),
    Tool(
        name="create_card",
        description="Create a new card (task) on a board. Optionally set parent_id to make it a sub-task.",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string", "description": "Description"},
                "column_id": {"type": "string", "description": "Column to place the card in. Use list_boards to see column IDs."},
                "external_id": {"type": "string", "description": "Stable external identity for integrations."},
                "parent_id": {"type": "string", "description": "Parent card ID for sub-tasks. Omit for top-level."},
                "priority": {"type": "string", "enum": ["critical", "high", "medium", "low"], "default": "medium"},
                "labels": {"type": "array", "items": {"type": "string"}},
                "metadata": {"type": "object"},
            },
            "required": ["board_id", "title", "column_id"],
        },
    ),
    Tool(
        name="get_card",
        description="Get a card by ID",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "card_id": {"type": "string"},
            },
            "required": ["board_id", "card_id"],
        },
    ),
    Tool(
        name="update_card",
        description="Update a card's title, description, priority, labels, or parent",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "card_id": {"type": "string"},
                "title": {"type": "string"},
                "external_id": {"type": "string"},
                "body": {"type": "string"},
                "parent_id": {"type": "string"},
                "priority": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                "labels": {"type": "array", "items": {"type": "string"}},
                "metadata": {"type": "object"},
            },
            "required": ["board_id", "card_id"],
        },
    ),
    Tool(
        name="move_card",
        description="Move a card to a different column. All sub-tasks move with it.",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "card_id": {"type": "string"},
                "column_id": {"type": "string", "description": "Target column ID"},
                "position": {"type": "integer", "description": "Position in target column. Omit to append."},
            },
            "required": ["board_id", "card_id", "column_id"],
        },
    ),
    Tool(
        name="delete_card",
        description="Delete a card",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "card_id": {"type": "string"},
            },
            "required": ["board_id", "card_id"],
        },
    ),
]


def dispatch(name: str, arguments: dict):
    match name:
        case "list_cards":
            filters = {k: arguments[k] for k in ("column_id", "parent_id", "priority", "label") if k in arguments}
            return client.list_cards(arguments["board_id"], **filters)
        case "search_cards":
            return client.search_cards(
                arguments["board_id"],
                query=arguments.get("query", ""),
                priority=arguments.get("priority"),
                label=arguments.get("label"),
            )
        case "create_card":
            return client.create_card(
                arguments["board_id"],
                arguments["title"],
                arguments["column_id"],
                body=arguments.get("body", ""),
                parent_id=arguments.get("parent_id"),
                priority=arguments.get("priority", "medium"),
                labels=arguments.get("labels"),
                external_id=arguments.get("external_id", ""),
                metadata=arguments.get("metadata"),
            )
        case "get_card":
            return client.get_card(arguments["board_id"], arguments["card_id"])
        case "update_card":
            fields = {k: v for k, v in arguments.items() if k not in ("board_id", "card_id") and v is not None}
            return client.update_card(arguments["board_id"], arguments["card_id"], **fields)
        case "move_card":
            return client.move_card(
                arguments["board_id"],
                arguments["card_id"],
                arguments["column_id"],
                arguments.get("position"),
            )
        case "delete_card":
            return client.delete_card(arguments["board_id"], arguments["card_id"])
        case _:
            raise KeyError(name)
