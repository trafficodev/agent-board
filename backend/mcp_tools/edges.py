"""Controlled directional relationships between cards."""

from mcp.types import Tool

import client
from card_semantics import RelationshipType
from typing import get_args


RELATIONSHIP_TYPES = list(get_args(RelationshipType))

TOOLS = [
    Tool(
        name="list_edges",
        description="List edges (typed connections between cards) on a board, optionally filtered by card or type",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "card_id": {"type": "string", "description": "Only edges touching this card, either direction"},
                "type": {"type": "string", "enum": RELATIONSHIP_TYPES},
            },
            "required": ["board_id"],
        },
    ),
    Tool(
        name="create_edge",
        description=(
            "Create a controlled directional relationship. depends_on points from the dependent "
            "card to its prerequisite and drives dependency ordering."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "from_card_id": {"type": "string"},
                "to_card_id": {"type": "string"},
                "type": {"type": "string", "enum": RELATIONSHIP_TYPES},
                "label": {"type": "string"},
            },
            "required": ["board_id", "from_card_id", "to_card_id", "type"],
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
                type=arguments["type"], label=arguments.get("label", ""),
            )
        case "delete_edge":
            return client.delete_edge(arguments["board_id"], arguments["edge_id"])
        case _:
            raise KeyError(name)
