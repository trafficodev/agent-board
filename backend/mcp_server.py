"""Single-process MCP server for all Agent Board tools."""

import asyncio
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any

import anyio
from mcp.server import Server
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, TextContent

import db
from card_semantics import sparse_value
from mcp_tools import GROUPS

_DEFAULT_MAX_CONCURRENCY = 8
_MAX_CONCURRENCY_LIMIT = 32
_STDIO_BUFFER_MESSAGES = 1024


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


def _exact_projection_value(result: object) -> object:
    if not isinstance(result, dict):
        return sparse_value(result)
    return {
        key: [
            {
                field: sparse_value(value)
                for field, value in item.items()
            }
            for item in value
        ]
        if key == "items" and isinstance(value, list)
        else sparse_value(value)
        for key, value in result.items()
    }


class ToolDispatchOwner:
    def __init__(self, *, max_concurrency: int | None = None) -> None:
        self._owners = _tool_owners()
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrency if max_concurrency is not None else _max_concurrency(),
            thread_name_prefix="agent-board-mcp",
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
            result = await asyncio.get_running_loop().run_in_executor(
                self._executor,
                group.dispatch,
                name,
                arguments,
            )
            if name in getattr(group, "EXACT_PROJECTION_TOOLS", ()):
                return _exact_projection_value(result)
            return sparse_value(result)
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
        self._executor.shutdown(wait=True, cancel_futures=True)


@asynccontextmanager
async def _stdio_transport(owner: ToolDispatchOwner, eof: anyio.Event):
    read_writer, read = anyio.create_memory_object_stream(_STDIO_BUFFER_MESSAGES)
    write, write_reader = anyio.create_memory_object_stream(0)
    stdin = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(stdin)
    transport, _ = await asyncio.get_running_loop().connect_read_pipe(
        lambda: protocol,
        sys.stdin.buffer,
    )

    async def read_stdin():
        async with read_writer:
            while line := await stdin.readline():
                try:
                    message = JSONRPCMessage.model_validate_json(
                        line.decode("utf-8", errors="replace")
                    )
                except Exception as exc:
                    await read_writer.send(exc)
                    continue
                await read_writer.send(SessionMessage(message))
        eof.set()
        await owner.close()

    async def write_stdout():
        async with write_reader:
            async for session_message in write_reader:
                content = session_message.message.model_dump_json(
                    by_alias=True,
                    exclude_none=True,
                )
                sys.stdout.buffer.write((content + "\n").encode("utf-8"))
                sys.stdout.buffer.flush()

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(read_stdin)
        task_group.start_soon(write_stdout)
        try:
            yield read, write
        finally:
            task_group.cancel_scope.cancel()
            transport.close()


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
    eof = anyio.Event()
    try:
        async with _stdio_transport(owner, eof) as (read, write):
            async with anyio.create_task_group() as task_group:
                task_group.start_soon(
                    app.run,
                    read,
                    write,
                    app.create_initialization_options(),
                )
                await eof.wait()
                task_group.cancel_scope.cancel()
    finally:
        await owner.close()


if __name__ == "__main__":
    asyncio.run(main())
