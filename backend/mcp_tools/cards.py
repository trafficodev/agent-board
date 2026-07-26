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
        name="bulk_cards",
        description=(
            "Apply MANY card operations to one board in a SINGLE call. Prefer this over calling "
            "create_card/update_card/move_card/delete_card in a loop — it is one round-trip instead "
            "of one per card, and the whole batch lands together or not at all.\n"
            "Operations run in order. Each entry is an object with 'op' set to one of:\n"
            "  create — title, column_id, and optionally body, parent_id, priority, labels, metadata, position\n"
            "  update — card_id, plus any of title, body, column_id, parent_id, priority, labels, metadata, position\n"
            "  move   — card_id, column_id, optionally position (sub-tasks follow their parent)\n"
            "  delete — card_id\n"
            "  note   — card_id, text, optionally kind ('note' or 'question')\n"
            "A create may set 'ref' to name itself; any later operation can then use \"@<ref>\" wherever a "
            "card_id or parent_id is expected, so a parent and its sub-tasks can be built in one call.\n"
            "If any operation fails, NOTHING is applied and the response reports failed_index and error."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "operations": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 500,
                    "description": "Ordered operations to apply atomically.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "op": {"type": "string", "enum": ["create", "update", "move", "delete", "note"]},
                            "ref": {"type": "string", "description": "On create: name this card for later ops as \"@ref\""},
                            "card_id": {"type": "string", "description": "Target card, or \"@ref\" from an earlier create"},
                            "title": {"type": "string"},
                            "body": {"type": "string"},
                            "column_id": {"type": "string"},
                            "parent_id": {"type": ["string", "null"]},
                            "position": {"type": "integer"},
                            "priority": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                            "labels": {"type": "array", "items": {"type": "string"}},
                            "metadata": {"type": "object"},
                            "text": {"type": "string", "description": "note text"},
                            "kind": {"type": "string", "enum": ["note", "question"]},
                        },
                        "required": ["op"],
                    },
                },
            },
            "required": ["board_id", "operations"],
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
        case "bulk_cards":
            return client.bulk_cards(arguments["board_id"], arguments["operations"])
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
