"""Arbitrary typed connections between two cards."""

from mcp.types import Tool

import client

TOOLS = [
    Tool(
        name="list_edges",
        description="List edges (typed connections between cards) on a board, optionally filtered by card or type",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "card_id": {"type": "string", "description": "Only edges touching this card, either direction"},
                "type": {"type": "string", "description": "Only edges of this type, e.g. blocked_by, blocks, duplicates"},
            },
            "required": ["board_id"],
        },
    ),
    Tool(
        name="create_edge",
        description=(
            "Create a typed connection from one card to another (e.g. blocked_by, blocks, "
            "duplicates, relates_to — any string). 'blocks' and 'blocked_by' also order the "
            "Open Items column: a blocker is sorted above the card waiting on it, so the top "
            "of the backlog is always work that nothing blocks."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "from_card_id": {"type": "string"},
                "to_card_id": {"type": "string"},
                "type": {"type": "string", "default": "relates_to"},
                "label": {"type": "string"},
            },
            "required": ["board_id", "from_card_id", "to_card_id"],
        },
    ),
    Tool(
        name="delete_edge",
        description="Delete an edge by id",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "edge_id": {"type": "string"},
            },
            "required": ["board_id", "edge_id"],
        },
    ),
]


def dispatch(name: str, arguments: dict):
    match name:
        case "list_edges":
            return client.list_edges(
                arguments["board_id"], card_id=arguments.get("card_id"), type=arguments.get("type"),
            )
        case "create_edge":
            return client.create_edge(
                arguments["board_id"], arguments["from_card_id"], arguments["to_card_id"],
                type=arguments.get("type", "relates_to"), label=arguments.get("label", ""),
            )
        case "delete_edge":
            return client.delete_edge(arguments["board_id"], arguments["edge_id"])
        case _:
            raise KeyError(name)
