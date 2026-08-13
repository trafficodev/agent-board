import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent_board import client
from agent_board.models import (
    CHANGE_NATIVE_SESSION_ENV,
    CHANGE_PROVIDER_ENV,
    CHANGE_REVIEWED_COMMIT_ENV,
)


class ClientStartupTest(unittest.TestCase):
    def setUp(self):
        self.original_base_url = client.BASE_URL
        self.original_attempts = client._STARTUP_ATTEMPTS
        self.original_process = client._process
        self.original_ready_url = client._ready_url
        client.BASE_URL = "http://localhost:8123"
        client._STARTUP_ATTEMPTS = 2
        client._process = None
        client._ready_url = ""

    def tearDown(self):
        client.BASE_URL = self.original_base_url
        client._STARTUP_ATTEMPTS = self.original_attempts
        client._process = self.original_process
        client._ready_url = self.original_ready_url

    def test_reuses_healthy_server_without_starting_another(self):
        with patch.object(client, "_is_running", return_value=True), patch.object(
            client.subprocess, "Popen"
        ) as popen:
            client.ensure_server()

        popen.assert_not_called()

    def test_starts_missing_local_server_on_loopback(self):
        process = Mock()
        process.poll.return_value = None

        with patch.dict(
            client.os.environ,
            {
                "AGENT_BOARD_HOME": "/tmp/agent-board-test-home",
                "BETTER_CLAUDE_INTERNAL_TOKEN": "legacy-secret",
                "BETTER_AGENT_INTERNAL_TOKEN": "secret",
                "UNRELATED_PARENT_SECRET": "must-not-leak",
            },
            clear=False,
        ), patch.object(client, "_is_running", side_effect=[False, False, True]), patch.object(
            client.subprocess, "Popen", return_value=process
        ) as popen, patch.object(client.time, "sleep"):
            client.ensure_server()

        args, kwargs = popen.call_args
        self.assertEqual(args[0][-4:], ["--host", "127.0.0.1", "--port", "8123"])
        self.assertIn("agent_board.main:app", args[0])
        # Packaged: the server is launched as an installed module, so no cwd is forced.
        self.assertNotIn("cwd", kwargs)
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["env"]["AGENT_BOARD_HOME"], "/tmp/agent-board-test-home")
        self.assertNotIn("BETTER_CLAUDE_INTERNAL_TOKEN", kwargs["env"])
        self.assertNotIn("BETTER_AGENT_INTERNAL_TOKEN", kwargs["env"])
        self.assertNotIn("UNRELATED_PARENT_SECRET", kwargs["env"])

    def test_does_not_auto_start_remote_endpoint(self):
        client.BASE_URL = "https://boards.example.com"

        with patch.object(client, "_is_running", return_value=False), patch.object(
            client.subprocess, "Popen"
        ) as popen:
            with self.assertRaisesRegex(client.AgentBoardUnavailableError, "auto-start requires"):
                client.ensure_server()

        popen.assert_not_called()

    def test_malformed_url_reports_unavailable_without_starting(self):
        client.BASE_URL = "http://localhost:not-a-port"

        with patch.object(client, "_is_running", return_value=False), patch.object(
            client.subprocess, "Popen"
        ) as popen:
            with self.assertRaisesRegex(client.AgentBoardUnavailableError, "Invalid AGENT_BOARD_URL"):
                client.ensure_server()

        popen.assert_not_called()

    def test_launch_error_is_wrapped_as_unavailable(self):
        with patch.object(client, "_is_running", return_value=False), patch.object(
            client.subprocess, "Popen", side_effect=OSError("missing runtime")
        ):
            with self.assertRaisesRegex(client.AgentBoardUnavailableError, "could not start"):
                client.ensure_server()

    def test_failed_start_cleans_up_child_and_reports_unavailable(self):
        process = Mock()
        process.poll.return_value = None

        with patch.object(client, "_is_running", return_value=False), patch.object(
            client.subprocess, "Popen", return_value=process
        ), patch.object(client.time, "sleep"):
            with self.assertRaisesRegex(client.AgentBoardUnavailableError, "failed to start"):
                client.ensure_server()

        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=client._STOP_TIMEOUT_SECONDS)
        self.assertIsNone(client._process)

    def test_failed_start_kills_child_that_ignores_terminate(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("uvicorn", client._STOP_TIMEOUT_SECONDS), None]

        with patch.object(client, "_is_running", return_value=False), patch.object(
            client.subprocess, "Popen", return_value=process
        ), patch.object(client.time, "sleep"):
            with self.assertRaises(client.AgentBoardUnavailableError):
                client.ensure_server()

        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertEqual(process.wait.call_count, 2)

    def test_request_failure_invalidates_readiness_without_retry(self):
        client._ready_url = client._normalized_base_url()

        with patch.object(
            client.urllib.request,
            "urlopen",
            side_effect=client.urllib.error.URLError("connection lost"),
        ) as urlopen:
            with self.assertRaisesRegex(client.AgentBoardUnavailableError, "connection lost"):
                client.list_boards()

        urlopen.assert_called_once()
        self.assertEqual(client._ready_url, "")

        with patch.object(client, "_is_running", return_value=True) as is_running:
            client.ensure_server()

        is_running.assert_called_once_with()
        self.assertEqual(client._ready_url, client._normalized_base_url())

    def test_authored_card_write_fails_before_http_without_complete_context(self):
        with patch.dict(client.os.environ, {}, clear=True), patch.object(client, "_req") as request:
            with self.assertRaisesRegex(ValueError, "provider must be"):
                client.create_card("board", "T", "column")

        request.assert_not_called()

    def test_authored_card_write_forwards_canonical_context(self):
        environ = {
            CHANGE_PROVIDER_ENV: "codex",
            CHANGE_NATIVE_SESSION_ENV: "native-1",
            CHANGE_REVIEWED_COMMIT_ENV: "a" * 40,
        }
        with patch.dict(client.os.environ, environ, clear=True), patch.object(
            client, "_req", return_value={}
        ) as request:
            client.create_card("board", "T", "column")

        context = request.call_args.args[3]
        self.assertEqual(context.provider, "codex")
        self.assertEqual(context.native_session_id, "native-1")
        self.assertEqual(context.reviewed_commit_sha, "a" * 40)

if __name__ == "__main__":
    unittest.main()
