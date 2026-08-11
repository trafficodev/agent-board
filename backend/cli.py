import argparse
import json
from typing import get_args

import client
from card_semantics import RelationshipType


def _json_arg(value: str):
    return json.loads(value)


def _print(result):
    print(json.dumps(result, indent=2, default=str))


def _provided(args, mapping: dict[str, str]) -> dict:
    return {
        target: getattr(args, source)
        for source, target in mapping.items()
        if hasattr(args, source)
    }


def _add_card_page_args(command: argparse.ArgumentParser) -> None:
    command.add_argument("--limit", type=int, default=50)
    command.add_argument("--cursor")


def _add_card_exploration_args(command: argparse.ArgumentParser) -> None:
    _add_card_page_args(command)
    command.add_argument(
        "--include", action="append",
        help="Add fields; repeat or comma-separate. Use * for all.",
    )
    command.add_argument(
        "--exclude", action="append",
        help="Remove fields; repeat or comma-separate. id always remains.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-board")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list_boards").set_defaults(func=lambda args: client.list_boards())

    get_board = sub.add_parser("get_board")
    get_board.add_argument("board_id")
    get_board.set_defaults(func=lambda args: client.get_board(args.board_id))

    create_board = sub.add_parser("create_board")
    create_board.add_argument("name")
    create_board.add_argument("--description", default="")
    create_board.add_argument("--columns", nargs="*")
    create_board.set_defaults(func=lambda args: client.create_board(args.name, args.description, args.columns))

    update_board = sub.add_parser("update_board")
    update_board.add_argument("board_id")
    update_board.add_argument("--name")
    update_board.add_argument("--description")
    update_board.add_argument("--remote-url")
    update_board.set_defaults(
        func=lambda args: client.update_board(args.board_id, args.name, args.description, args.remote_url)
    )

    ensure_project_board = sub.add_parser("ensure_project_board")
    ensure_project_board.add_argument("remote_url")
    ensure_project_board.add_argument("--name")
    ensure_project_board.add_argument("--description", default="")
    ensure_project_board.add_argument("--columns", nargs="*")
    ensure_project_board.set_defaults(
        func=lambda args: client.ensure_project_board(args.remote_url, args.name, args.description, args.columns)
    )

    link_project_remote = sub.add_parser("link_project_remote")
    link_project_remote.add_argument("board_id")
    link_project_remote.add_argument("remote_url")
    link_project_remote.set_defaults(
        func=lambda args: client.link_project_remote(args.board_id, args.remote_url)
    )

    consolidate_project_board = sub.add_parser("consolidate_project_board")
    consolidate_project_board.add_argument("source_board_id")
    consolidate_project_board.add_argument("target_board_id")
    consolidate_project_board.set_defaults(
        func=lambda args: client.consolidate_project_board(args.source_board_id, args.target_board_id)
    )

    delete_board = sub.add_parser("delete_board")
    delete_board.add_argument("board_id")
    delete_board.set_defaults(func=lambda args: client.delete_board(args.board_id))

    import_board = sub.add_parser("import_board")
    import_board.add_argument("name")
    import_board.add_argument("--description", default="")
    import_board.add_argument("--columns", nargs="+", required=True)
    import_board.add_argument("--cards-json", type=_json_arg, default=[])
    import_board.set_defaults(func=lambda args: client.import_board(args.name, args.columns, args.cards_json, args.description))

    for name, func in (
        ("get_canvas_sync", client.get_canvas_sync),
        ("disable_canvas_sync", client.disable_canvas_sync),
        ("sync_canvas", client.sync_canvas),
    ):
        command = sub.add_parser(name)
        command.add_argument("board_id")
        command.set_defaults(func=lambda args, call=func: call(args.board_id))

    enable_canvas_sync = sub.add_parser("enable_canvas_sync")
    enable_canvas_sync.add_argument("board_id")
    enable_canvas_sync.add_argument("--canvas-api-url")
    enable_canvas_sync.set_defaults(func=lambda args: client.enable_canvas_sync(args.board_id, args.canvas_api_url))

    add_column = sub.add_parser("add_column")
    add_column.add_argument("board_id")
    add_column.add_argument("name")
    add_column.add_argument("--position", type=int)
    add_column.set_defaults(func=lambda args: client.add_column(args.board_id, args.name, args.position))

    update_column = sub.add_parser("update_column")
    update_column.add_argument("board_id")
    update_column.add_argument("column_id")
    update_column.add_argument("--name")
    update_column.add_argument("--position", type=int)
    update_column.set_defaults(func=lambda args: client.update_column(args.board_id, args.column_id, args.name, args.position))

    delete_column = sub.add_parser("delete_column")
    delete_column.add_argument("board_id")
    delete_column.add_argument("column_id")
    delete_column.set_defaults(func=lambda args: client.delete_column(args.board_id, args.column_id))

    list_cards = sub.add_parser("list_cards")
    list_cards.add_argument("board_id")
    list_cards.add_argument("--column-id")
    list_cards.add_argument("--parent-id")
    list_cards.add_argument("--priority")
    list_cards.add_argument("--label")
    list_cards.add_argument("--sort")
    _add_card_exploration_args(list_cards)
    list_cards.set_defaults(
        func=lambda args: client.list_cards(
            args.board_id,
            column_id=args.column_id,
            parent_id=args.parent_id,
            priority=args.priority,
            label=args.label,
            sort=args.sort,
            limit=args.limit,
            cursor=args.cursor,
            include=args.include,
            exclude=args.exclude,
        )
    )

    search_cards = sub.add_parser("search_cards")
    search_cards.add_argument("board_id")
    search_cards.add_argument("--query", default="")
    search_cards.add_argument("--priority")
    search_cards.add_argument("--label")
    search_cards.add_argument("--sort")
    _add_card_exploration_args(search_cards)
    search_cards.set_defaults(
        func=lambda args: client.search_cards(
            args.board_id,
            query=args.query,
            priority=args.priority,
            label=args.label,
            sort=args.sort,
            limit=args.limit,
            cursor=args.cursor,
            include=args.include,
            exclude=args.exclude,
        )
    )

    relevant_candidates = sub.add_parser("relevant_candidates")
    relevant_candidates.add_argument("board_id")
    relevant_candidates.add_argument("query")
    relevant_candidates.add_argument("--priority")
    relevant_candidates.add_argument("--label")
    relevant_candidates.add_argument("--max-candidates", type=int, default=40)
    _add_card_exploration_args(relevant_candidates)
    relevant_candidates.set_defaults(
        func=lambda args: client.relevant_candidates(
            args.board_id,
            args.query,
            priority=args.priority,
            label=args.label,
            max_candidates=args.max_candidates,
            limit=args.limit,
            cursor=args.cursor,
            include=args.include,
            exclude=args.exclude,
        )
    )

    create_card = sub.add_parser("create_card")
    create_card.add_argument("board_id")
    create_card.add_argument("title")
    create_card.add_argument("column_id")
    create_card.add_argument("--body", default=argparse.SUPPRESS)
    create_card.add_argument("--parent-id", default=argparse.SUPPRESS)
    create_card.add_argument("--priority", default=argparse.SUPPRESS)
    create_card.add_argument("--labels", nargs="*", default=argparse.SUPPRESS)
    create_card.add_argument("--external-id", default=argparse.SUPPRESS)
    create_card.add_argument("--metadata-json", type=_json_arg, default=argparse.SUPPRESS)
    create_card.add_argument("--semantics-json", type=_json_arg, default=argparse.SUPPRESS)
    create_card.set_defaults(
        func=lambda args: client.create_card(
            args.board_id,
            args.title,
            args.column_id,
            **_provided(args, {
                "body": "body", "parent_id": "parent_id", "priority": "priority",
                "labels": "labels", "external_id": "external_id",
                "metadata_json": "metadata", "semantics_json": "semantics",
            }),
        )
    )

    bulk_cards = sub.add_parser("bulk_cards")
    bulk_cards.add_argument("board_id")
    bulk_cards.add_argument("operations", help="JSON array of operations to apply atomically")
    bulk_cards.set_defaults(
        func=lambda args: client.bulk_cards(args.board_id, json.loads(args.operations))
    )

    get_card = sub.add_parser("get_card")
    get_card.add_argument("board_id")
    get_card.add_argument("card_id")
    get_card.set_defaults(func=lambda args: client.get_card(args.board_id, args.card_id))

    update_card = sub.add_parser("update_card")
    update_card.add_argument("board_id")
    update_card.add_argument("card_id")
    update_card.add_argument("--title", default=argparse.SUPPRESS)
    update_card.add_argument("--body", default=argparse.SUPPRESS)
    update_card.add_argument("--column-id", default=argparse.SUPPRESS)
    update_parent = update_card.add_mutually_exclusive_group()
    update_parent.add_argument("--parent-id", default=argparse.SUPPRESS)
    update_parent.add_argument(
        "--clear-parent",
        dest="parent_id",
        action="store_const",
        const=None,
        default=argparse.SUPPRESS,
    )
    update_card.add_argument("--position", type=int, default=argparse.SUPPRESS)
    update_card.add_argument("--priority", default=argparse.SUPPRESS)
    update_card.add_argument("--labels", nargs="*", default=argparse.SUPPRESS)
    update_card.add_argument("--external-id", default=argparse.SUPPRESS)
    update_card.add_argument("--metadata-json", type=_json_arg, default=argparse.SUPPRESS)
    update_card.add_argument("--semantics-json", type=_json_arg, default=argparse.SUPPRESS)
    update_card.set_defaults(
        func=lambda args: client.update_card(
            args.board_id,
            args.card_id,
            **_provided(args, {
                "title": "title", "body": "body", "column_id": "column_id",
                "parent_id": "parent_id", "position": "position", "priority": "priority",
                "labels": "labels", "external_id": "external_id",
                "metadata_json": "metadata", "semantics_json": "semantics",
            }),
        )
    )

    move_card = sub.add_parser("move_card")
    move_card.add_argument("board_id")
    move_card.add_argument("card_id")
    move_card.add_argument("column_id")
    move_card.add_argument("--position", type=int)
    move_card.set_defaults(func=lambda args: client.move_card(args.board_id, args.card_id, args.column_id, args.position))

    delete_card = sub.add_parser("delete_card")
    delete_card.add_argument("board_id")
    delete_card.add_argument("card_id")
    delete_card.set_defaults(func=lambda args: client.delete_card(args.board_id, args.card_id))

    add_session = sub.add_parser("add_session")
    add_session.add_argument("board_id")
    add_session.add_argument("card_id")
    add_session.add_argument("session_id")
    add_session.add_argument("--system", default="")
    add_session.add_argument("--action", default="")
    add_session.add_argument("--outcome")
    add_session.set_defaults(
        func=lambda args: client.add_session(
            args.board_id,
            args.card_id,
            args.session_id,
            system=args.system,
            action=args.action,
            outcome=args.outcome,
        )
    )

    get_events = sub.add_parser("get_events")
    get_events.add_argument("board_id")
    get_events.add_argument("--limit", type=int, default=100)
    get_events.set_defaults(func=lambda args: client.get_events(args.board_id, args.limit))

    get_card_history = sub.add_parser("get_card_history")
    get_card_history.add_argument("board_id")
    get_card_history.add_argument("card_id")
    get_card_history.add_argument("--at", default="", help="ISO 8601 timestamp for a point-in-time view")
    get_card_history.set_defaults(
        func=lambda args: client.get_card_history(args.board_id, args.card_id, at=args.at)
    )

    get_card_changes = sub.add_parser("get_card_changes")
    get_card_changes.add_argument("board_id")
    get_card_changes.add_argument("card_id")
    get_card_changes.add_argument("--provider", default="")
    get_card_changes.add_argument("--native-session-id", default="")
    get_card_changes.add_argument("--offset", type=int, default=0)
    get_card_changes.add_argument("--limit", type=int, default=200)
    get_card_changes.set_defaults(
        func=lambda args: client.get_card_changes(
            args.board_id,
            args.card_id,
            provider=args.provider,
            native_session_id=args.native_session_id,
            offset=args.offset,
            limit=args.limit,
        )
    )

    set_change_vote = sub.add_parser("set_change_vote")
    set_change_vote.add_argument("board_id")
    set_change_vote.add_argument("card_id")
    set_change_vote.add_argument("target_id")
    set_change_vote.add_argument("direction", type=int, choices=(-1, 1))
    set_change_vote.add_argument("--provider", required=True)
    set_change_vote.add_argument("--native-session-id", required=True)
    set_change_vote.add_argument("--reviewed-commit-sha", required=True)
    set_change_vote.set_defaults(
        func=lambda args: client.set_change_vote(
            args.board_id,
            args.card_id,
            args.target_id,
            args.direction,
            args.provider,
            args.native_session_id,
            args.reviewed_commit_sha,
        )
    )

    get_change_vote_audit = sub.add_parser("get_change_vote_audit")
    get_change_vote_audit.add_argument("board_id")
    get_change_vote_audit.add_argument("card_id")
    get_change_vote_audit.add_argument("target_id")
    get_change_vote_audit.add_argument("--offset", type=int, default=0)
    get_change_vote_audit.add_argument("--limit", type=int, default=200)
    get_change_vote_audit.set_defaults(
        func=lambda args: client.get_change_vote_audit(
            args.board_id,
            args.card_id,
            args.target_id,
            offset=args.offset,
            limit=args.limit,
        )
    )

    revert_card = sub.add_parser("revert_card")
    revert_card.add_argument("board_id")
    revert_card.add_argument("card_id")
    revert_card.add_argument("version", type=int)
    revert_card.set_defaults(
        func=lambda args: client.revert_card(args.board_id, args.card_id, args.version)
    )

    get_session_activity = sub.add_parser("get_session_activity")
    get_session_activity.add_argument("session_id")
    get_session_activity.add_argument("--board-id", default="")
    get_session_activity.add_argument("--limit", type=int, default=200)
    get_session_activity.set_defaults(
        func=lambda args: client.get_session_activity(
            args.session_id, board_id=args.board_id, limit=args.limit
        )
    )

    list_edges = sub.add_parser("list_edges")
    list_edges.add_argument("board_id")
    list_edges.add_argument("--card-id")
    list_edges.add_argument("--type")
    list_edges.set_defaults(
        func=lambda args: client.list_edges(args.board_id, card_id=args.card_id, type=args.type)
    )

    create_edge = sub.add_parser("create_edge")
    create_edge.add_argument("board_id")
    create_edge.add_argument("from_card_id")
    create_edge.add_argument("to_card_id")
    create_edge.add_argument(
        "--type",
        required=True,
        choices=get_args(RelationshipType),
    )
    create_edge.add_argument("--label", default="")
    create_edge.set_defaults(
        func=lambda args: client.create_edge(
            args.board_id, args.from_card_id, args.to_card_id, type=args.type, label=args.label,
        )
    )

    delete_edge = sub.add_parser("delete_edge")
    delete_edge.add_argument("board_id")
    delete_edge.add_argument("edge_id")
    delete_edge.set_defaults(func=lambda args: client.delete_edge(args.board_id, args.edge_id))

    return parser


def main() -> None:
    args = build_parser().parse_args()
    _print(args.func(args))


if __name__ == "__main__":
    main()
