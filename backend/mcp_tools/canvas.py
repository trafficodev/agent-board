"""Canvas sync tools."""

from mcp.types import Tool

import client

TOOLS = [
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
]


def dispatch(name: str, arguments: dict):
    match name:
        case "get_canvas_sync":
            return client.get_canvas_sync(arguments["board_id"])
        case "enable_canvas_sync":
            return client.enable_canvas_sync(arguments["board_id"], arguments.get("canvas_api_url"))
        case "disable_canvas_sync":
            return client.disable_canvas_sync(arguments["board_id"])
        case "sync_canvas":
            return client.sync_canvas(arguments["board_id"])
        case _:
            raise KeyError(name)
