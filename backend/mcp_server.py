"""Single-process MCP server for all Agent Board tools."""

import asyncio
import json

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent

from mcp_tools import GROUPS


def _tool_owners() -> dict[str, object]:
    owners: dict[str, object] = {}
    for group in GROUPS.values():
        for tool in group.TOOLS:
            if tool.name in owners:
                raise RuntimeError(f"Duplicate Agent Board MCP tool: {tool.name}")
            owners[tool.name] = group
    return owners


def build_app() -> Server:
    owners = _tool_owners()
    tools = [tool for group in GROUPS.values() for tool in group.TOOLS]
    app = Server("agent-board")

    @app.list_tools()
    async def list_tools():
        return tools

    @app.call_tool()
    async def call_tool(name: str, arguments: dict):
        group = owners.get(name)
        if group is None:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
        try:
            result = group.dispatch(name, arguments)
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    return app


async def main():
    app = build_app()
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
