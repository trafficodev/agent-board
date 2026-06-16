"""MCP server for Agent Board.

Exposes board operations as MCP tools so Claude Code agents can
manage cards and sessions without knowing the HTTP API.

Auto-starts the board backend on first use (via client.py).

Usage in Claude Code settings.json:
  "mcpServers": {
    "agent-board": {
      "command": "python",
      "args": ["<agent-board-repo>/backend/mcp_server.py"]
    }
  }
"""

import json
import sys

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

import client

app = Server("agent-board")


# --- Tool definitions ---

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
        name="get_canvas_sync",
        description="Get Canvas sync status for a board",
        inputSchema={"type": "object", "properties": {"board_id": {"type": "string"}}, "required": ["board_id"]},
    ),
    Tool(
        name="enable_canvas_sync",
        description="Enable Canvas sync for a board",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "canvas_api_url": {"type": "string"},
            },
            "required": ["board_id"],
        },
    ),
    Tool(
        name="disable_canvas_sync",
        description="Disable Canvas sync for a board",
        inputSchema={"type": "object", "properties": {"board_id": {"type": "string"}}, "required": ["board_id"]},
    ),
    Tool(
        name="sync_canvas",
        description="Sync a board to Canvas now",
        inputSchema={"type": "object", "properties": {"board_id": {"type": "string"}}, "required": ["board_id"]},
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
        description="Search cards by text, quoted phrase, negation, fields, file, commit, session, priority, label, or hierarchy-visible matches",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "query": {"type": "string", "description": "Examples: file:frontend/src/App.tsx commit:abc1234 has:file has:commit -label:bug"},
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
                "parent_id": {"type": "string", "description": "Parent card ID for sub-tasks. Omit for top-level."},
                "priority": {"type": "string", "enum": ["critical", "high", "medium", "low"], "default": "medium"},
                "labels": {"type": "array", "items": {"type": "string"}},
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
                "body": {"type": "string"},
                "parent_id": {"type": "string"},
                "priority": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                "labels": {"type": "array", "items": {"type": "string"}},
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


@app.list_tools()
async def list_tools():
    return TOOLS


@app.call_tool()
async def call_tool(name: str, arguments: dict):
    try:
        match name:
            case "list_boards":
                result = client.list_boards()
            case "get_board":
                result = client.get_board(arguments["board_id"])
            case "create_board":
                result = client.create_board(
                    arguments["name"],
                    arguments.get("description", ""),
                    arguments.get("columns"),
                )
            case "update_board":
                result = client.update_board(
                    arguments["board_id"],
                    name=arguments.get("name"),
                    description=arguments.get("description"),
                )
            case "delete_board":
                result = client.delete_board(arguments["board_id"])
            case "import_board":
                result = client.import_board(
                    arguments["name"],
                    arguments["columns"],
                    arguments.get("cards", []),
                    description=arguments.get("description", ""),
                )
            case "get_canvas_sync":
                result = client.get_canvas_sync(arguments["board_id"])
            case "enable_canvas_sync":
                result = client.enable_canvas_sync(arguments["board_id"], arguments.get("canvas_api_url"))
            case "disable_canvas_sync":
                result = client.disable_canvas_sync(arguments["board_id"])
            case "sync_canvas":
                result = client.sync_canvas(arguments["board_id"])
            case "add_column":
                result = client.add_column(arguments["board_id"], arguments["name"], arguments.get("position"))
            case "update_column":
                result = client.update_column(
                    arguments["board_id"],
                    arguments["column_id"],
                    name=arguments.get("name"),
                    position=arguments.get("position"),
                )
            case "delete_column":
                result = client.delete_column(arguments["board_id"], arguments["column_id"])
            case "list_cards":
                filters = {}
                for k in ("column_id", "parent_id", "priority", "label"):
                    if k in arguments:
                        filters[k] = arguments[k]
                result = client.list_cards(arguments["board_id"], **filters)
            case "search_cards":
                result = client.search_cards(
                    arguments["board_id"],
                    query=arguments.get("query", ""),
                    priority=arguments.get("priority"),
                    label=arguments.get("label"),
                )
            case "create_card":
                result = client.create_card(
                    arguments["board_id"],
                    arguments["title"],
                    arguments["column_id"],
                    body=arguments.get("body", ""),
                    parent_id=arguments.get("parent_id"),
                    priority=arguments.get("priority", "medium"),
                    labels=arguments.get("labels"),
                )
            case "get_card":
                result = client.get_card(arguments["board_id"], arguments["card_id"])
            case "update_card":
                fields = {k: v for k, v in arguments.items() if k not in ("board_id", "card_id") and v is not None}
                result = client.update_card(arguments["board_id"], arguments["card_id"], **fields)
            case "move_card":
                result = client.move_card(
                    arguments["board_id"],
                    arguments["card_id"],
                    arguments["column_id"],
                    arguments.get("position"),
                )
            case "delete_card":
                result = client.delete_card(arguments["board_id"], arguments["card_id"])
            case "add_session":
                result = client.add_session(
                    arguments["board_id"],
                    arguments["card_id"],
                    arguments["session_id"],
                    system=arguments.get("system", ""),
                    action=arguments.get("action", ""),
                    outcome=arguments.get("outcome"),
                )
            case "get_events":
                result = client.get_events(arguments["board_id"], arguments.get("limit", 20))
            case _:
                return [TextContent(type="text", text=f"Unknown tool: {name}")]
        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e}")]


async def main():
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
