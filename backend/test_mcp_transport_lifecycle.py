from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


BACKEND = Path(__file__).resolve().parent
MCP_SERVER = BACKEND / "mcp_server.py"


class _RequestState:
    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.count = 0
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    state: _RequestState

    def do_GET(self) -> None:
        if self.path == "/openapi.json":
            payload = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.path != "/api/boards":
            self.send_error(404)
            return
        with self.state.lock:
            self.state.count += 1
            self.state.active += 1
            self.state.max_active = max(
                self.state.max_active,
                self.state.active,
            )
        try:
            time.sleep(self.state.delay)
            payload = b"[]"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        finally:
            with self.state.lock:
                self.state.active -= 1

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _McpProcess:
    def __init__(self, url: str, *, max_concurrency: int) -> None:
        env = {
            **os.environ,
            "AGENT_BOARD_URL": url,
            "AGENT_BOARD_MCP_MAX_CONCURRENCY": str(max_concurrency),
        }
        self.process = subprocess.Popen(
            [sys.executable, str(MCP_SERVER)],
            cwd=str(BACKEND),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._request_id = 0
        self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "lifecycle-test", "version": "1"},
            },
        )
        self.notify("notifications/initialized", {})

    def _send(self, payload: dict) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def start_request(self, method: str, params: dict) -> int:
        self._request_id += 1
        self._send({
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        })
        return self._request_id

    def read_response(self) -> dict:
        assert self.process.stdout is not None
        line = self.process.stdout.readline()
        if not line:
            stderr = self.process.stderr.read() if self.process.stderr else ""
            raise AssertionError(f"MCP transport closed early: {stderr}")
        return json.loads(line)

    def request(self, method: str, params: dict) -> dict:
        request_id = self.start_request(method, params)
        response = self.read_response()
        if response.get("id") != request_id:
            raise AssertionError(
                f"unexpected MCP response id: {response.get('id')}",
            )
        return response

    def close(self, *, timeout: float = 3.0) -> None:
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        self.process.wait(timeout=timeout)
        stderr = self.process.stderr.read() if self.process.stderr else ""
        self._close_pipes()
        if self.process.returncode != 0:
            raise AssertionError(
                f"MCP transport exited {self.process.returncode}: {stderr}",
            )

    def kill(self) -> None:
        self.process.kill()
        self.process.wait(timeout=3)
        self._close_pipes()

    def _close_pipes(self) -> None:
        for pipe in (
            self.process.stdin,
            self.process.stdout,
            self.process.stderr,
        ):
            if pipe is not None and not pipe.closed:
                pipe.close()


class MCPTransportLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = _RequestState(delay=0.08)
        _Handler.state = self.state
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )
        self.thread.start()
        host, port = self.server.server_address
        self.url = f"http://{host}:{port}"
        self.processes: list[_McpProcess] = []

    def tearDown(self) -> None:
        for client in self.processes:
            if client.process.poll() is None:
                client.kill()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def _client(self, *, max_concurrency: int = 3) -> _McpProcess:
        client = _McpProcess(
            self.url,
            max_concurrency=max_concurrency,
        )
        self.processes.append(client)
        return client

    def test_real_stdio_call_concurrency_eof_and_reconnect(self) -> None:
        client = self._client(max_concurrency=3)
        listed = client.request("tools/list", {})
        names = {
            tool["name"]
            for tool in listed["result"]["tools"]
        }
        self.assertIn("list_boards", names)
        self.assertIn("create_card", names)

        request_ids = [
            client.start_request(
                "tools/call",
                {"name": "list_boards", "arguments": {}},
            )
            for _ in range(9)
        ]
        responses = [client.read_response() for _ in request_ids]
        self.assertEqual(
            {response["id"] for response in responses},
            set(request_ids),
        )
        self.assertTrue(all("result" in response for response in responses))
        self.assertEqual(self.state.count, 9)
        self.assertEqual(self.state.max_active, 3)

        unknown = client.request(
            "tools/call",
            {"name": "unknown", "arguments": {}},
        )
        self.assertTrue(unknown["result"]["isError"])
        self.assertIn(
            "Unknown Agent Board MCP tool",
            unknown["result"]["content"][0]["text"],
        )
        self.assertEqual(self.state.count, 9)

        started = time.monotonic()
        client.close()
        self.assertLess(time.monotonic() - started, 2.0)

        reconnected = self._client()
        result = reconnected.request(
            "tools/call",
            {"name": "list_boards", "arguments": {}},
        )
        self.assertIn("result", result)
        self.assertEqual(self.state.count, 10)
        reconnected.kill()

        after_death = self._client()
        result = after_death.request(
            "tools/call",
            {"name": "list_boards", "arguments": {}},
        )
        self.assertIn("result", result)
        self.assertEqual(self.state.count, 11)
        after_death.close()

    def test_eof_cancels_queued_calls_without_duplicate_dispatch(self) -> None:
        self.state.delay = 0.4
        client = self._client(max_concurrency=1)
        for _ in range(12):
            client.start_request(
                "tools/call",
                {"name": "list_boards", "arguments": {}},
            )
        started = time.monotonic()
        client.close(timeout=2.0)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertLessEqual(self.state.count, 1)


if __name__ == "__main__":
    unittest.main()
