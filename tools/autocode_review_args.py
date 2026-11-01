"""Human artifact-review and approval options and their argument-level dependencies."""

try:
    from . import autocode_approval_provenance as approval_provenance
    from . import autocode_review_routing as review_routing
except ImportError:
    import autocode_approval_provenance as approval_provenance
    import autocode_review_routing as review_routing


def add_arguments(parser):
    review_routing.add_argument(parser)
    parser.add_argument(
        "--approve-review",
        action="append",
        default=[],
        metavar="CRITERION_ID",
        help="Record a human-review acceptance of this criterion (repeatable); needs --review-token",
    )
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
    parser.add_argument(
        "--as",
        dest="claimed_as",
        metavar="NAME",
        help="With --approve-goal, --approve-review, --accept-completion, --edit-goal, --goal-file or a resume on a "
        "shared machine: the name of the person approving, recorded beside the Git identity as their claim, never "
        "verified; a resume records it only when it releases a pause",
    )


def validate(args, parser):
    if args.review_token and not (
        args.approve_review
        or args.reject_review
        or args.reconcile_review
        or args.delegate_all
        or args.reject_assumption
    ):
        parser.error(
            "--review-token requires --approve-review, --reject-review, --reconcile-review, --delegate-all "
            "or --reject-assumption"
        )
    if args.reconcile_review and not args.review_token:
        parser.error("--reconcile-review requires --review-token")
    if args.reject_review and (not args.review_token or not (args.review_note or "").strip()):
        parser.error("--reject-review requires --review-token and a nonempty --review-note")
    if args.review_note is not None and not args.reject_review:
        parser.error("--review-note requires --reject-review")
    if args.claimed_as is not None and not (
        args.approve_goal
        or args.approve_review
        or args.accept_completion
        or args.edit_goal
        or args.goal_file
        or args.resume_paused
    ):
        parser.error(
            "--as names who approves; it requires --approve-goal, --approve-review, --accept-completion, "
            "--edit-goal, --goal-file or a resume (--resume-paused, or autocode resume at a pause)"
        )
    refusal = approval_provenance.claim_refusal(args.claimed_as)
    if refusal:
        parser.error(refusal)
