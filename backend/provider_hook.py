"""Provider-native before-tool hook for Agent Board MCP attribution."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable

from git_revision import current_commit


_MUTATING_TOOLS = {
    "create_card",
    "bulk_cards",
    "update_card",
    "move_card",
    "delete_card",
    "add_session",
    "add_card_note",
    "answer_card_question",
    "revert_card",
    "set_change_vote",
}
_IDENTITY_TOOLS = _MUTATING_TOOLS | {"get_card_changes"}


_TOOL_NAME_PATTERNS = {
    "claude": re.compile(r"^mcp__agent_board__(?P<name>[a-z_]+)$"),
    "codex": re.compile(r"^mcp__agent_board__(?P<name>[a-z_]+)$"),
    "gemini": re.compile(r"^mcp_agent_board_(?P<name>[a-z_]+)$"),
}


def _agent_board_tool_name(native_name: str, provider: str) -> str:
    match = _TOOL_NAME_PATTERNS[provider].fullmatch(native_name)
    if match is None:
        return ""
    name = match.group("name")
    return name if name in _IDENTITY_TOOLS else ""


def inject_context(
    payload: dict,
    provider: str,
    head_lookup: Callable[[str], str] = current_commit,
) -> dict:
    tool_name = _agent_board_tool_name(
        str(payload.get("tool_name", "")),
        provider,
    )
    tool_input = payload.get("tool_input")
    if not tool_name or not isinstance(tool_input, dict):
        return payload
    native_session_id = str(payload.get("session_id", "")).strip()
    if not native_session_id:
        raise ValueError("Agent Board requires the provider's native session ID")
    updated = dict(tool_input)
    updated["provider"] = provider
    updated["native_session_id"] = native_session_id
    if tool_name in _MUTATING_TOOLS:
        updated["reviewed_commit_sha"] = head_lookup(str(payload.get("cwd", "")))
    return updated


def hook_output(payload: dict, provider: str) -> dict:
    updated = inject_context(payload, provider)
    if updated is payload:
        return {}
    if provider == "gemini":
        return {
            "hookSpecificOutput": {
                "hookEventName": "BeforeTool",
                "tool_input": updated,
            }
        }
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": updated,
        }
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("claude", "codex", "gemini"), required=True)
    args = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
        print(json.dumps(hook_output(payload, args.provider)))
    except (ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
