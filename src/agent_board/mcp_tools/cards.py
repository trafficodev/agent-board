"""Card CRUD and search tools."""

from mcp.types import Tool

from .. import card_projection
from .. import client
from .context import CHANGE_CONTEXT_PROPERTIES, change_context_from_arguments


SEMANTICS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "kind": {
            "type": "string",
            "enum": ["product_area", "feature", "requirement", "task", "bug", "test", "decision", "finding"],
            "description": (
                "Semantic kind. Parent rules (the only constraint on setting a kind): "
                "'product_area' must be top-level; 'feature' must sit under a "
                "'product_area'; 'requirement' must sit under a 'feature' (and needs "
                "outcome + acceptance_criteria); the delivery kinds ('task', 'bug', "
                "'test', 'decision', 'finding') may sit under no parent, a kind-less "
                "parent, or any other delivery kind, but not under a catalog kind. "
                "Catalog kinds ('product_area'/'feature'/'requirement') additionally "
                "require catalog_lifecycle. No kind requires a linked remote and none "
                "creates an implied edge — kind is independent of edges and remote state."
            ),
        },
        "catalog_lifecycle": {"type": "string", "enum": ["proposed", "active", "accepted", "deprecated"]},
        "outcome": {"type": "string"},
        "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
        "exclusions": {"type": "array", "items": {"type": "string"}},
        "owning_surface": {"type": "string"},
        "ownership": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "team": {"type": "string"}, "agent": {"type": "string"},
                "repository": {"type": "string"}, "component": {"type": "string"},
            },
        },
        "evidence": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "kind": {"type": "string", "enum": ["source", "test", "commit", "screenshot", "validation"]},
                    "locator": {"type": "string"}, "revision": {"type": "string"},
                    "verified_at": {"type": "string"},
                    "state": {"type": "string", "enum": ["uncovered", "implemented", "verified", "partial"]},
                },
                "required": ["kind", "locator"],
            },
        },
        "decisions": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"}, "text": {"type": "string"},
                    "state": {"type": "string", "enum": ["proposed", "accepted", "rejected", "superseded"]},
                    "rationale": {"type": "string"}, "decided_by": {"type": "string"},
                    "decided_at": {"type": "string"}, "supersedes": {"type": "string"},
                },
                "required": ["id", "text"],
            },
        },
    },
}

_BULK_COMMON_CARD_PROPERTIES = {
    "title": {"type": "string"},
    "body": {"type": "string"},
    "column_id": {"type": "string"},
    "parent_id": {"type": ["string", "null"]},
    "position": {"type": "integer"},
    "priority": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
    "labels": {"type": "array", "items": {"type": "string"}},
    "metadata": {"type": "object"},
    "semantics": SEMANTICS_SCHEMA,
    "external_id": {"type": "string"},
}

BULK_OPERATION_SCHEMAS = [
    {
        "type": "object", "additionalProperties": False,
        "properties": {
            "op": {"const": "create"}, "ref": {"type": "string"},
            **_BULK_COMMON_CARD_PROPERTIES,
        },
        "required": ["op", "title", "column_id"],
    },
    {
        "type": "object", "additionalProperties": False,
        "properties": {
            "op": {"const": "update"}, "card_id": {"type": "string"},
            **_BULK_COMMON_CARD_PROPERTIES,
        },
        "required": ["op", "card_id"],
    },
    {
        "type": "object", "additionalProperties": False,
        "properties": {
            "op": {"const": "move"}, "card_id": {"type": "string"},
            "column_id": {"type": "string"}, "position": {"type": "integer"},
        },
        "required": ["op", "card_id", "column_id"],
    },
    {
        "type": "object", "additionalProperties": False,
        "properties": {"op": {"const": "delete"}, "card_id": {"type": "string"}},
        "required": ["op", "card_id"],
    },
    {
        "type": "object", "additionalProperties": False,
        "properties": {
            "op": {"const": "note"}, "card_id": {"type": "string"},
            "text": {"type": "string"},
            "kind": {"type": "string", "enum": ["note", "question"]},
        },
        "required": ["op", "card_id", "text"],
    },
]

CARD_PAGE_PROPERTIES = {
    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
    "cursor": {"type": "string", "description": "Opaque next_cursor from the previous page."},
}

CARD_EXPLORATION_PROPERTIES = {
    **CARD_PAGE_PROPERTIES,
    "include": {
        "type": "array", "items": {"type": "string"},
        "description": card_projection.INCLUDE_FIELDS_DESCRIPTION,
    },
    "exclude": {
        "type": "array", "items": {"type": "string"},
        "description": card_projection.EXCLUDE_FIELDS_DESCRIPTION,
    },
}

EXACT_PROJECTION_TOOLS = frozenset({
    "list_cards", "search_cards", "relevant_candidates",
})

TOOLS = [
    Tool(
        name="list_cards",
        description=(
            "Discover cards in cursor-paginated envelopes. Returns all card and derived fields by "
            "default; include/exclude select the exact projection. Pass next_cursor back to continue."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "column_id": {"type": "string"},
                "parent_id": {"type": "string", "description": "Filter to children of this card. Use 'null' for top-level only."},
                "priority": {"type": "string"},
                "label": {"type": "string"},
                "sort": {"type": "string", "enum": ["board", "updated_desc", "created_desc", "priority_desc", "title_asc", "sessions_desc"]},
                **CARD_EXPLORATION_PROPERTIES,
            },
            "required": ["board_id"],
        },
    ),
    Tool(
        name="search_cards",
        description=(
            "Exact/literal card search — 100% deterministic substring, field, regex, boolean, "
            "date-range, and numeric-comparison matching. Zero fuzziness: it finds only what "
            "literally appears (or a regex/range that literally matches), never a paraphrase or "
            "synonym. Returns matching cards plus their visible ancestors/descendants "
            "in the card hierarchy. Returns a paginated envelope with relation_roles. Returns all "
            "available fields by default; include/exclude select the exact projection. Every operator "
            "composes with every other: negation, "
            "field scoping, regex, grouping, and comparisons can all appear in the same query."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "query": {
                    "type": "string",
                    "description": (
                        "Space-separated terms are implicit AND. Operators:\n"
                        "- Plain text: `login bug` — substring match (case-insensitive) anywhere on the card.\n"
                        "- \"quoted phrase\": `\"login bug\"` — keeps spaces inside one term instead of splitting on them.\n"
                        "- -negation: `-label:bug` or bare `-urgent` — term must NOT match.\n"
                        "- field:value — scope to one field. Fields: title, body, label (alias: labels/type), "
                        "priority, column, id, external_id (alias: external), metadata, session, edge, edge_type, "
                        "file (alias: files/edited_file/edited_files), commit (alias: commits/git_commit/"
                        "git_commits), worktree (alias: worktrees/worktree_path/worktree_paths). "
                        "Example: `file:frontend/src/App.tsx commit:abc1234 priority:high`.\n"
                        "- /regex/flags — literal regex on any field or unscoped, e.g. `title:/^Fix.*bug$/i`, "
                        "`/TODO|FIXME/`. Only `i` (case-insensitive) flag is supported. Quote the value if the "
                        "pattern itself contains a space or parenthesis, e.g. `contains:\"/foo(bar) baz/\"` — "
                        "unquoted parens are always parsed as grouping syntax (see below).\n"
                        "- contains:value — rg-backed: also greps the actual contents of every file this card "
                        "references (via edited_files/projects metadata), not just card text. Supports regex too: "
                        "`contains:/render\\w+Data/`.\n"
                        "- has:field — card has any value for file/commit/worktree/session/edge, "
                        "e.g. `has:file`, `has:commit`.\n"
                        "- (a OR b) — explicit OR grouping, only recognized INSIDE parentheses; can nest, e.g. "
                        "`(label:bug OR (label:regression OR label:hotfix)) priority:high`. Terms and groups at "
                        "the top level (and within a group) are always implicit AND. A bare OR with no "
                        "parentheses is NOT an operator — it is parsed as a literal word, e.g. `foo OR bar` "
                        "requires the literal substrings \"foo\", \"or\", AND \"bar\" all present. A group can be "
                        "negated: `-(label:bug OR label:regression)`.\n"
                        "- Date range/comparison on `created` or `updated` (compares created_at/updated_at): "
                        "`created:>2026-01-01`, `updated:<2026-06-01`, `created:>=2026-01-01`, "
                        "`created:2026-01-01..2026-02-01` (inclusive range, both bounds match). Bare date with no "
                        "operator means exact equality.\n"
                        "- Numeric comparison on `position` or `sessions` (session_history count): "
                        "`sessions:>3`, `position:<5`, `position:>=2`. Regex syntax is rejected on "
                        "created/updated/position/sessions (parse_search_query raises ValueError) — comparisons "
                        "and regex don't mix.\n"
                        "Combined example: `(label:bug OR label:regression) sessions:>1 -commit:abc1234 "
                        "created:>2026-01-01`."
                    ),
                },
                "priority": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                "label": {"type": "string"},
                "sort": {"type": "string", "enum": ["board", "updated_desc", "created_desc", "priority_desc", "title_asc", "sessions_desc"]},
                **CARD_EXPLORATION_PROPERTIES,
            },
            "required": ["board_id"],
        },
    ),
    Tool(
        name="relevant_candidates",
        description=(
            "Discover a bounded keyword-ranked card shortlist. Returns a paginated envelope with "
            "relevance_score, column, and error. Returns all available fields by default; "
            "include/exclude select the exact projection."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "query": {"type": "string"},
                "priority": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                "label": {"type": "string"},
                "max_candidates": {"type": "integer", "minimum": 1, "maximum": 50, "default": 40},
                **CARD_EXPLORATION_PROPERTIES,
            },
            "required": ["board_id", "query"],
        },
    ),
    Tool(
        name="create_card",
        description="Create a new card (task) on a board. Optionally set parent_id to make it a sub-task.",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string", "description": "Description"},
                "column_id": {"type": "string", "description": "Column to place the card in. Use list_boards to see column IDs."},
                "external_id": {"type": "string", "description": "Stable external identity for integrations."},
                "parent_id": {"type": "string", "description": "Parent card ID for sub-tasks. Omit for top-level."},
                "priority": {"type": "string", "enum": ["critical", "high", "medium", "low"], "default": "medium"},
                "labels": {"type": "array", "items": {"type": "string"}},
                "metadata": {"type": "object"},
                "semantics": SEMANTICS_SCHEMA,
                **CHANGE_CONTEXT_PROPERTIES,
            },
            "required": ["board_id", "title", "column_id"],
        },
    ),
    Tool(
        name="bulk_cards",
        description=(
            "Apply MANY card operations to one board in a SINGLE call. Prefer this over calling "
            "create_card/update_card/move_card/delete_card in a loop — it is one round-trip instead "
            "of one per card, and the whole batch lands together or not at all.\n"
            "Operations run in order. Each entry is an object with 'op' set to one of:\n"
            "  create — title, column_id, and optionally body, parent_id, priority, labels, metadata, position\n"
            "  update — card_id, plus any of title, body, column_id, parent_id, priority, labels, metadata, position\n"
            "  move   — card_id, column_id, optionally position (sub-tasks follow their parent)\n"
            "  delete — card_id\n"
            "  note   — card_id, text, optionally kind ('note' or 'question')\n"
            "A create may set 'ref' to name itself; any later operation can then use \"@<ref>\" wherever a "
            "card_id or parent_id is expected, so a parent and its sub-tasks can be built in one call.\n"
            "If any operation fails, NOTHING is applied and the response reports failed_index and error."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "operations": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 500,
                    "description": "Ordered operations to apply atomically.",
                    "items": {"oneOf": BULK_OPERATION_SCHEMAS},
                },
                **CHANGE_CONTEXT_PROPERTIES,
            },
            "required": ["board_id", "operations"],
        },
    ),
    Tool(
        name="get_card",
        description="Get a card by ID",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "card_id": {"type": "string"},
            },
            "required": ["board_id", "card_id"],
        },
    ),
    Tool(
        name="update_card",
        description="Update card fields. semantics replaces the complete semantic object; pass {} to clear it.",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "card_id": {"type": "string"},
                "title": {"type": "string"},
                "external_id": {"type": "string"},
                "body": {"type": "string"},
                "parent_id": {"type": ["string", "null"]},
                "priority": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                "labels": {"type": "array", "items": {"type": "string"}},
                "metadata": {"type": "object"},
                "semantics": SEMANTICS_SCHEMA,
                **CHANGE_CONTEXT_PROPERTIES,
            },
            "required": ["board_id", "card_id"],
        },
    ),
    Tool(
        name="move_card",
        description="Move a card to a different column. All sub-tasks move with it.",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "card_id": {"type": "string"},
                "column_id": {"type": "string", "description": "Target column ID"},
                "position": {"type": "integer", "description": "Position in target column. Omit to append."},
                **CHANGE_CONTEXT_PROPERTIES,
            },
            "required": ["board_id", "card_id", "column_id"],
        },
    ),
    Tool(
        name="delete_card",
        description="Delete a card",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "card_id": {"type": "string"},
                **CHANGE_CONTEXT_PROPERTIES,
            },
            "required": ["board_id", "card_id"],
        },
    ),
]


def dispatch(name: str, arguments: dict):
    match name:
        case "list_cards":
            filters = {
                k: arguments[k]
                for k in (
                    "column_id", "parent_id", "priority", "label", "sort",
                    "limit", "cursor", "include", "exclude",
                )
                if k in arguments
            }
            return client.list_cards(arguments["board_id"], **filters)
        case "search_cards":
            return client.search_cards(
                arguments["board_id"],
                query=arguments.get("query", ""),
                priority=arguments.get("priority"),
                label=arguments.get("label"),
                sort=arguments.get("sort"),
                limit=arguments.get("limit", 50),
                cursor=arguments.get("cursor"),
                include=arguments.get("include"),
                exclude=arguments.get("exclude"),
            )
        case "relevant_candidates":
            return client.relevant_candidates(
                arguments["board_id"],
                arguments["query"],
                priority=arguments.get("priority"),
                label=arguments.get("label"),
                max_candidates=arguments.get("max_candidates", 40),
                limit=arguments.get("limit", 50),
                cursor=arguments.get("cursor"),
                include=arguments.get("include"),
                exclude=arguments.get("exclude"),
            )
        case "create_card":
            fields = {
                key: arguments[key]
                for key in ("body", "parent_id", "priority", "labels", "external_id", "metadata", "semantics")
                if key in arguments
            }
            return client.create_card(
                arguments["board_id"], arguments["title"], arguments["column_id"],
                change_context=change_context_from_arguments(arguments), **fields,
            )
        case "bulk_cards":
            return client.bulk_cards(
                arguments["board_id"],
                arguments["operations"],
                change_context_from_arguments(arguments),
            )
        case "get_card":
            return client.get_card(arguments["board_id"], arguments["card_id"])
        case "update_card":
            fields = {
                k: v
                for k, v in arguments.items()
                if k not in ("board_id", "card_id", *CHANGE_CONTEXT_PROPERTIES)
            }
            return client.update_card(
                arguments["board_id"],
                arguments["card_id"],
                change_context=change_context_from_arguments(arguments),
                **fields,
            )
        case "move_card":
            return client.move_card(
                arguments["board_id"],
                arguments["card_id"],
                arguments["column_id"],
                arguments.get("position"),
                change_context_from_arguments(arguments),
            )
        case "delete_card":
            return client.delete_card(
                arguments["board_id"],
                arguments["card_id"],
                change_context_from_arguments(arguments),
            )
        case _:
            raise KeyError(name)
