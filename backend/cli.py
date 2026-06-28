import argparse
import json

import client


def _json_arg(value: str):
    return json.loads(value)


def _print(result):
    print(json.dumps(result, indent=2, default=str))


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
    update_board.set_defaults(func=lambda args: client.update_board(args.board_id, args.name, args.description))

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
    list_cards.set_defaults(
        func=lambda args: client.list_cards(
            args.board_id,
            column_id=args.column_id,
            parent_id=args.parent_id,
            priority=args.priority,
            label=args.label,
        )
    )

    search_cards = sub.add_parser("search_cards")
    search_cards.add_argument("board_id")
    search_cards.add_argument("--query", default="")
    search_cards.add_argument("--priority")
    search_cards.add_argument("--label")
    search_cards.set_defaults(
        func=lambda args: client.search_cards(
            args.board_id,
            query=args.query,
            priority=args.priority,
            label=args.label,
        )
    )

    create_card = sub.add_parser("create_card")
    create_card.add_argument("board_id")
    create_card.add_argument("title")
    create_card.add_argument("column_id")
    create_card.add_argument("--body", default="")
    create_card.add_argument("--parent-id")
    create_card.add_argument("--priority", default="medium")
    create_card.add_argument("--labels", nargs="*")
    create_card.add_argument("--external-id", default="")
    create_card.add_argument("--metadata-json", type=_json_arg, default={})
    create_card.set_defaults(
        func=lambda args: client.create_card(
            args.board_id,
            args.title,
            args.column_id,
            body=args.body,
            parent_id=args.parent_id,
            priority=args.priority,
            labels=args.labels,
            external_id=args.external_id,
            metadata=args.metadata_json,
        )
    )

    get_card = sub.add_parser("get_card")
    get_card.add_argument("board_id")
    get_card.add_argument("card_id")
    get_card.set_defaults(func=lambda args: client.get_card(args.board_id, args.card_id))

    update_card = sub.add_parser("update_card")
    update_card.add_argument("board_id")
    update_card.add_argument("card_id")
    update_card.add_argument("--title")
    update_card.add_argument("--body")
    update_card.add_argument("--column-id")
    update_card.add_argument("--parent-id")
    update_card.add_argument("--position", type=int)
    update_card.add_argument("--priority")
    update_card.add_argument("--labels", nargs="*")
    update_card.add_argument("--external-id")
    update_card.add_argument("--metadata-json", type=_json_arg)
    update_card.set_defaults(
        func=lambda args: client.update_card(
            args.board_id,
            args.card_id,
            title=args.title,
            body=args.body,
            column_id=args.column_id,
            parent_id=args.parent_id,
            position=args.position,
            priority=args.priority,
            labels=args.labels,
            external_id=args.external_id,
            metadata=args.metadata_json,
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

    return parser


def main() -> None:
    args = build_parser().parse_args()
    _print(args.func(args))


if __name__ == "__main__":
    main()
