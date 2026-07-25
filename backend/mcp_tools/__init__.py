"""Tool groups for Agent Board's MCP servers.

Each group is a self-contained (TOOLS, dispatch) pair so an MCP client can
register only the capability surface it needs (e.g. just `cards`) instead of
loading all 21 tools under one server.
"""

from . import boards, canvas, cards, edges, sessions

GROUPS = {
    "boards": boards,
    "canvas": canvas,
    "cards": cards,
    "edges": edges,
    "sessions": sessions,
}
