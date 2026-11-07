"""CLI arguments for exact individual or coordinated plan approval."""


def add_arguments(parser):
    parser.add_argument(
        "--approve-goal",
        metavar="TOKEN",
        help="Approve exactly a previously displayed revision; `-` reads AUTOCODE_APPROVE_GOAL_TOKEN "
        "so the secret stays out of argv and shell history",
    )
    parser.add_argument(
        "--changeset-token",
        metavar="TOKEN",
        help="Bind --approve-goal to the change set's prepared approval; `-` reads AUTOCODE_CHANGESET_TOKEN",
    )


def validate(args, parser):
    if args.changeset_token and (not args.approve_goal or not args.run_dir):
        parser.error("--changeset-token requires --approve-goal on a saved run")
