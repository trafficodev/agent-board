"""MCP server for Agent Board.

Launches one tool group (boards, canvas, cards, edges, or sessions) over
stdio, so a client only registers the capability surface it actually needs
instead of loading all tools under one server. Groups live in `mcp_tools/`.

Auto-starts the board backend on first use (via client.py).

Registration across every installed provider (Claude, Codex, Gemini) is
handled by `../manage_mcp.py install` / `uninstall` -- run that instead of
hand-editing each provider's config. See that script if you need the raw
command/args shape it registers.
"""

import asyncio
import json
import sys

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent

from mcp_tools import GROUPS


def _resolve_group() -> str:
    available = ", ".join(sorted(GROUPS))
    if len(sys.argv) < 2:
        print(f"Usage: mcp_server.py <group>  (groups: {available})", file=sys.stderr)
        sys.exit(1)
    group_name = sys.argv[1]
    if group_name not in GROUPS:
        print(f"Unknown group '{group_name}'. Available: {available}", file=sys.stderr)
        sys.exit(1)
    return group_name


def build_app(group_name: str) -> Server:
    group = GROUPS[group_name]
    app = Server(f"agent-board-{group_name}")

    @app.list_tools()
    async def list_tools():
        return group.TOOLS

    @app.call_tool()
    async def call_tool(name: str, arguments: dict):
        try:
            result = group.dispatch(name, arguments)
        except KeyError:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    return app


async def main():
    app = build_app(_resolve_group())
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
