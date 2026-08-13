"""Shared authored-change identity fields for mutating MCP tools."""

from ..models import ChangeContext


CHANGE_CONTEXT_PROPERTIES = {
    "provider": {"type": "string", "description": "Native agent provider"},
    "native_session_id": {"type": "string", "description": "Native provider session ID"},
    "reviewed_commit_sha": {
        "type": "string",
        "pattern": "^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$",
        "description": "Full Git commit SHA reviewed for this change",
    },
}


def change_context_from_arguments(arguments: dict) -> ChangeContext | None:
    values = {
        key: arguments.get(key, "")
        for key in CHANGE_CONTEXT_PROPERTIES
    }
    if not any(values.values()):
        return None
    return ChangeContext.authored(**values)
