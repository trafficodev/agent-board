"""Session activity and event log tools."""

from mcp.types import Tool

import client
from mcp_tools.context import CHANGE_CONTEXT_PROPERTIES, change_context_from_arguments

TOOLS = [
    Tool(
        name="add_session",
        description="Record that a session worked on a card",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "card_id": {"type": "string"},
                "session_id": {"type": "string"},
                "system": {"type": "string", "description": "System/agent name"},
                "action": {"type": "string", "description": "What the session did"},
                "outcome": {"type": "string", "enum": ["success", "failed", "partial"]},
                **CHANGE_CONTEXT_PROPERTIES,
            },
            "required": ["board_id", "card_id", "session_id"],
        },
    ),
    Tool(
        name="get_events",
        description="Get recent events/audit log for a board",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["board_id"],
        },
    ),
    Tool(
        name="add_card_note",
        description=(
            "Leave a work note on a card, or ASK A QUESTION about it. A question stays open until "
            "someone answers it and is surfaced to the user as an attention badge, so use kind='question' "
            "when you need a human decision and kind='note' to record working context."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "card_id": {"type": "string"},
                "text": {"type": "string"},
                "kind": {"type": "string", "enum": ["note", "question"], "default": "note"},
                **CHANGE_CONTEXT_PROPERTIES,
            },
            "required": ["board_id", "card_id", "text"],
        },
    ),
    Tool(
        name="answer_card_question",
        description="Answer an open question on a card, which clears it from the user's attention badge",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "card_id": {"type": "string"},
                "note_id": {"type": "string"},
                "answer": {"type": "string"},
                **CHANGE_CONTEXT_PROPERTIES,
            },
            "required": ["board_id", "card_id", "note_id", "answer"],
        },
    ),
    Tool(
        name="list_open_questions",
        description="List unanswered questions across every board, or one board when board_id is given",
        inputSchema={
            "type": "object",
            "properties": {"board_id": {"type": "string"}},
        },
    ),
    Tool(
        name="get_card_history",
        description=(
            "Every recorded version of a card — who changed it, when, and what changed — in full, "
            "including versions older than the summary the card itself carries, and including cards "
            "that were deleted. Pass 'at' (ISO 8601) to get the single version the card stood at that "
            "moment instead of the whole trail."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "card_id": {"type": "string"},
                "at": {"type": "string", "description": "ISO 8601 timestamp for a point-in-time view"},
            },
            "required": ["board_id", "card_id"],
        },
    ),
    Tool(
        name="revert_card",
        description=(
            "Restore a card's content to one of its earlier versions, as reported by get_card_history. "
            "Recorded forward as a new version, so nothing in the history is rewritten or lost."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "card_id": {"type": "string"},
                "version": {"type": "integer", "description": "1-based version from get_card_history"},
                **CHANGE_CONTEXT_PROPERTIES,
            },
            "required": ["board_id", "card_id", "version"],
        },
    ),
    Tool(
        name="get_session_activity",
        description=(
            "What one session did, newest first, across every board or just one. Use this to find out "
            "what another session already changed before touching the same cards."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "board_id": {"type": "string"},
                "limit": {"type": "integer", "default": 200},
            },
            "required": ["session_id"],
        },
    ),
]


def dispatch(name: str, arguments: dict):
    match name:
        case "add_session":
            return client.add_session(
                arguments["board_id"],
                arguments["card_id"],
                arguments["session_id"],
                system=arguments.get("system", ""),
                action=arguments.get("action", ""),
                outcome=arguments.get("outcome"),
                change_context=change_context_from_arguments(arguments),
            )
        case "add_card_note":
            return client.add_card_note(
                arguments["board_id"],
                arguments["card_id"],
                arguments["text"],
                kind=arguments.get("kind", "note"),
                change_context=change_context_from_arguments(arguments),
            )
        case "answer_card_question":
            return client.answer_card_question(
                arguments["board_id"],
                arguments["card_id"],
                arguments["note_id"],
                arguments["answer"],
                change_context=change_context_from_arguments(arguments),
            )
        case "list_open_questions":
            return client.open_questions(arguments.get("board_id", ""))
        case "get_events":
            return client.get_events(arguments["board_id"], arguments.get("limit", 20))
        case "get_card_history":
            return client.get_card_history(
                arguments["board_id"],
                arguments["card_id"],
                at=arguments.get("at", ""),
            )
        case "revert_card":
            return client.revert_card(
                arguments["board_id"],
                arguments["card_id"],
                arguments["version"],
                change_context=change_context_from_arguments(arguments),
            )
        case "get_session_activity":
            return client.get_session_activity(
                arguments["session_id"],
                board_id=arguments.get("board_id", ""),
                limit=arguments.get("limit", 200),
            )
        case _:
            raise KeyError(name)
