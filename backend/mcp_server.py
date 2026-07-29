"""Single-process MCP server for all Agent Board tools."""

import asyncio
import json
import os
from typing import Any

import anyio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent

import db
from mcp_tools import GROUPS

_DEFAULT_MAX_CONCURRENCY = 8
_MAX_CONCURRENCY_LIMIT = 32


def _tool_owners() -> dict[str, object]:
    owners: dict[str, object] = {}
    for group in GROUPS.values():
        for tool in group.TOOLS:
            if tool.name in owners:
                raise RuntimeError(f"Duplicate Agent Board MCP tool: {tool.name}")
            owners[tool.name] = group
    return owners


def _max_concurrency() -> int:
    raw = os.environ.get("AGENT_BOARD_MCP_MAX_CONCURRENCY", "")
    if not raw:
        return _DEFAULT_MAX_CONCURRENCY
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(
            "AGENT_BOARD_MCP_MAX_CONCURRENCY must be an integer",
        ) from exc
    if not 1 <= value <= _MAX_CONCURRENCY_LIMIT:
        raise RuntimeError(
            f"AGENT_BOARD_MCP_MAX_CONCURRENCY must be between 1 and "
            f"{_MAX_CONCURRENCY_LIMIT}",
        )
    return value


class ToolDispatchOwner:
    def __init__(self, *, max_concurrency: int | None = None) -> None:
        self._owners = _tool_owners()
        self._limiter = anyio.CapacityLimiter(
            max_concurrency if max_concurrency is not None else _max_concurrency(),
        )
        self._active: set[asyncio.Task[Any]] = set()
        self._closed = False

    async def call(self, name: str, arguments: dict) -> object:
        if self._closed:
            raise RuntimeError("Agent Board MCP transport is closing")
        group = self._owners.get(name)
        if group is None:
            raise ValueError(f"Unknown Agent Board MCP tool: {name}")
        if not isinstance(arguments, dict):
            raise ValueError("Agent Board MCP tool arguments must be an object")
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("Agent Board MCP call has no lifecycle owner")
        self._active.add(task)
        try:
            return await anyio.to_thread.run_sync(
                group.dispatch,
                name,
                arguments,
                abandon_on_cancel=True,
                limiter=self._limiter,
            )
        finally:
            self._active.discard(task)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        current = asyncio.current_task()
        pending = [
            task
            for task in self._active
            if task is not current and not task.done()
        ]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


def build_app(owner: ToolDispatchOwner | None = None) -> Server:
    db.initialize_storage()
    dispatch_owner = owner or ToolDispatchOwner()
    tools = [tool for group in GROUPS.values() for tool in group.TOOLS]
    app = Server("agent-board")

    @app.list_tools()
    async def list_tools():
        return tools

    @app.call_tool()
    async def call_tool(name: str, arguments: dict):
        result = await dispatch_owner.call(name, arguments)
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    return app


async def main():
    owner = ToolDispatchOwner()
    app = build_app(owner)
    try:
        async with stdio_server() as (read, write):
            await app.run(read, write, app.create_initialization_options())
    finally:
        await owner.close()


if __name__ == "__main__":
    asyncio.run(main())
