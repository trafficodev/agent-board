"""Board and column lifecycle tools."""

from mcp.types import Tool

from .. import client

TOOLS = [
    Tool(
        name="list_boards",
        description="List all boards",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="get_board",
        description="Get a board by ID (includes columns)",
        inputSchema={"type": "object", "properties": {"board_id": {"type": "string"}}, "required": ["board_id"]},
    ),
    Tool(
        name="create_board",
        description="Create a new board with optional columns",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "columns": {"type": "array", "items": {"type": "string"}, "description": "Column names. Defaults to Backlog/In Progress/Review/Done"},
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="update_board",
        description="Update a board's name, description, or linked project remote URL",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "remote_url": {"type": "string"},
            },
            "required": ["board_id"],
        },
    ),
    Tool(
        name="ensure_project_board",
        description=(
            "Get or create the board for a project, keyed by its git remote URL "
            "(scp-like, ssh://, and https:// forms all normalize to the same identity, "
            "so every worktree/clone of the same repo shares one board). Auto-creates "
            "with Open Items/In Progress/In Testing/Done columns if none exists yet. "
            "Idempotent — safe to call at the start of every session."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "remote_url": {"type": "string", "description": "e.g. output of `git remote get-url origin`"},
                "name": {"type": "string", "description": "Board name if one must be created. Defaults to the repo name."},
                "description": {"type": "string"},
                "columns": {"type": "array", "items": {"type": "string"}, "description": "Column names if one must be created. Defaults to Open Items/In Progress/In Testing/Done"},
            },
            "required": ["remote_url"],
        },
    ),
    Tool(
        name="link_project_remote",
        description=(
            "Link another repository remote URL to an existing project board. "
            "The remote is normalized and may belong to only one board."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "remote_url": {"type": "string", "description": "e.g. output of `git remote get-url origin`"},
            },
            "required": ["board_id", "remote_url"],
        },
    ),
    Tool(
        name="consolidate_project_board",
        description=(
            "Losslessly consolidate a source project board into a target board, "
            "preserving cards, history, edges, columns, and repository remotes."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "source_board_id": {"type": "string"},
                "target_board_id": {"type": "string"},
            },
            "required": ["source_board_id", "target_board_id"],
        },
    ),
    Tool(
        name="delete_board",
        description="Delete a board",
        inputSchema={"type": "object", "properties": {"board_id": {"type": "string"}}, "required": ["board_id"]},
    ),
    Tool(
        name="import_board",
        description="Import a board with columns and cards",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "columns": {"type": "array", "items": {"type": "string"}},
                "cards": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["name", "columns"],
        },
    ),
    Tool(
        name="add_column",
        description="Add a column to a board",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "name": {"type": "string"},
                "position": {"type": "integer"},
            },
            "required": ["board_id", "name"],
        },
    ),
    Tool(
        name="update_column",
        description="Update a column's name or position",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "column_id": {"type": "string"},
                "name": {"type": "string"},
                "position": {"type": "integer"},
            },
            "required": ["board_id", "column_id"],
        },
    ),
    Tool(
        name="delete_column",
        description="Delete a column from a board",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "column_id": {"type": "string"},
            },
            "required": ["board_id", "column_id"],
        },
    ),
]


def dispatch(name: str, arguments: dict):
    match name:
        case "list_boards":
            return client.list_boards()
        case "get_board":
            return client.get_board(arguments["board_id"])
        case "create_board":
            return client.create_board(
                arguments["name"],
                arguments.get("description", ""),
                arguments.get("columns"),
            )
        case "update_board":
            return client.update_board(
                arguments["board_id"],
                name=arguments.get("name"),
                description=arguments.get("description"),
                remote_url=arguments.get("remote_url"),
            )
        case "ensure_project_board":
            return client.ensure_project_board(
                arguments["remote_url"],
                name=arguments.get("name"),
                description=arguments.get("description", ""),
                columns=arguments.get("columns"),
            )
        case "link_project_remote":
            return client.link_project_remote(arguments["board_id"], arguments["remote_url"])
        case "consolidate_project_board":
            return client.consolidate_project_board(
                arguments["source_board_id"],
                arguments["target_board_id"],
            )
        case "delete_board":
            return client.delete_board(arguments["board_id"])
        case "import_board":
            return client.import_board(
                arguments["name"],
                arguments["columns"],
                arguments.get("cards", []),
                description=arguments.get("description", ""),
            )
        case "add_column":
            return client.add_column(arguments["board_id"], arguments["name"], arguments.get("position"))
        case "update_column":
            return client.update_column(
                arguments["board_id"],
                arguments["column_id"],
                name=arguments.get("name"),
                position=arguments.get("position"),
            )
        case "delete_column":
            return client.delete_column(arguments["board_id"], arguments["column_id"])
        case _:
            raise KeyError(name)
