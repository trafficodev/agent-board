import subprocess
import unittest
from unittest.mock import patch

import manage_mcp


class ManageMCPTests(unittest.TestCase):
    def test_codex_install_registers_one_server_then_removes_legacy_groups(self) -> None:
        calls: list[list[str]] = []

        def fake_run(argv: list[str]) -> subprocess.CompletedProcess:
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")

        with patch.object(manage_mcp, "_run", side_effect=fake_run):
            self.assertEqual(manage_mcp._install_codex(), "ok")

        expected_removals = [
            ["codex", "mcp", "remove", f"agent-board-{group}"]
            for group in manage_mcp.GROUP_NAMES
        ]
        self.assertEqual(
            calls[0],
            [
                "codex",
                "mcp",
                "add",
                "agent-board",
                "--",
                str(manage_mcp.VENV_PYTHON),
                str(manage_mcp.MCP_SERVER),
            ],
        )
        self.assertEqual(calls[1:], expected_removals)

    def test_install_failure_preserves_legacy_registrations(self) -> None:
        result = subprocess.CompletedProcess([], 1, "", "connection failed")
        with patch.object(manage_mcp, "_run", return_value=result) as run:
            self.assertEqual(manage_mcp._install_codex(), "FAILED: connection failed")
        run.assert_called_once()

    def test_cleanup_failure_is_reported(self) -> None:
        calls = 0

        def fake_run(argv: list[str]) -> subprocess.CompletedProcess:
            nonlocal calls
            calls += 1
            if calls == 2:
                return subprocess.CompletedProcess(argv, 1, "", "permission denied")
            return subprocess.CompletedProcess(argv, 0, "", "")

        with patch.object(manage_mcp, "_run", side_effect=fake_run):
            status = manage_mcp._install_codex()

        self.assertIn("FAILED cleanup", status)
        self.assertIn("permission denied", status)


if __name__ == "__main__":
    unittest.main()
