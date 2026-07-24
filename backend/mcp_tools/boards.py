"""Board and column lifecycle tools."""

from mcp.types import Tool

import client

TOOLS = [
    Tool(
        name="list_boards",
        description="List all boards",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="get_board",
        description="Get a board by ID (includes columns)",
        inputSchema={"type": "object", "properties": {"board_id": {"type": "string"}}, "required": ["board_id"]},
    ),
    Tool(
        name="create_board",
        description="Create a new board with optional columns",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "columns": {"type": "array", "items": {"type": "string"}, "description": "Column names. Defaults to Backlog/In Progress/Review/Done"},
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="update_board",
        description="Update a board's name or description",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "name": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["board_id"],
        },
    ),
    Tool(
        name="delete_board",
        description="Delete a board",
        inputSchema={"type": "object", "properties": {"board_id": {"type": "string"}}, "required": ["board_id"]},
    ),
    Tool(
        name="import_board",
        description="Import a board with columns and cards",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "columns": {"type": "array", "items": {"type": "string"}},
                "cards": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["name", "columns"],
        },
    ),
    Tool(
        name="add_column",
        description="Add a column to a board",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "name": {"type": "string"},
                "position": {"type": "integer"},
            },
            "required": ["board_id", "name"],
        },
    ),
    Tool(
        name="update_column",
        description="Update a column's name or position",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "column_id": {"type": "string"},
                "name": {"type": "string"},
                "position": {"type": "integer"},
            },
            "required": ["board_id", "column_id"],
        },
    ),
    Tool(
        name="delete_column",
        description="Delete a column from a board",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "column_id": {"type": "string"},
            },
            "required": ["board_id", "column_id"],
        },
    ),
]


def dispatch(name: str, arguments: dict):
    match name:
        case "list_boards":
            return client.list_boards()
        case "get_board":
            return client.get_board(arguments["board_id"])
        case "create_board":
            return client.create_board(
                arguments["name"],
                arguments.get("description", ""),
                arguments.get("columns"),
            )
        case "update_board":
            return client.update_board(
                arguments["board_id"],
                name=arguments.get("name"),
                description=arguments.get("description"),
            )
        case "delete_board":
            return client.delete_board(arguments["board_id"])
        case "import_board":
            return client.import_board(
                arguments["name"],
                arguments["columns"],
                arguments.get("cards", []),
                description=arguments.get("description", ""),
            )
        case "add_column":
            return client.add_column(arguments["board_id"], arguments["name"], arguments.get("position"))
        case "update_column":
            return client.update_column(
                arguments["board_id"],
                arguments["column_id"],
                name=arguments.get("name"),
                position=arguments.get("position"),
            )
        case "delete_column":
            return client.delete_column(arguments["board_id"], arguments["column_id"])
        case _:
            raise KeyError(name)
