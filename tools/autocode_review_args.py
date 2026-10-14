"""Human artifact-review options and their argument-level dependencies."""


def add_arguments(parser):
    parser.add_argument("--approve-review", action="append", default=[], metavar="CRITERION_ID")
    parser.add_argument(
        "--reconcile-review",
        metavar="CRITERION_ID=ANSWER_ID",
        help="Bind an authenticated legacy acceptance to current validated evidence without a new approval",
    )
    parser.add_argument(
        "--review-token",
        help="Exact displayed contract/artifact/validation token; "
        "also required by --delegate-all and --reject-assumption; "
        "`-` reads AUTOCODE_REVIEW_TOKEN so the secret stays out of argv and shell history",
    )
    parser.add_argument("--reject-review", metavar="CRITERION_ID", help="Reject one pending artifact criterion")
    parser.add_argument("--review-note", metavar="TEXT", help="Required correction note for --reject-review")


def validate(args, parser):
    if args.review_token and not (
        args.approve_review
        or args.reject_review
        or args.reconcile_review
        or args.delegate_all
        or args.reject_assumption
        or args.program_guidance is not None
    ):
        parser.error(
            "--review-token requires --approve-review, --reject-review, --reconcile-review, --delegate-all, "
            "--reject-assumption or --program-guidance"
        )
    if args.reconcile_review and not args.review_token:
        parser.error("--reconcile-review requires --review-token")
    if args.reject_review and (not args.review_token or not (args.review_note or "").strip()):
        parser.error("--reject-review requires --review-token and a nonempty --review-note")
    if args.review_note is not None and not args.reject_review:
        parser.error("--review-note requires --reject-review")
