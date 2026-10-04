"""The autocode command line: every flag, and the checks between flags that argparse cannot express.

parse() builds the parser, parses the arguments, records which budget flags were given explicitly,
and stops with a usage error (exit 2) on an invalid combination. autocode._main_body keeps the
returned parser for the later errors that depend on the saved run.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

try:
    from . import autocode_workflows as workflows, autopilot
    from .autocode_configure import BUDGET_ARGUMENTS, DEFAULT_ROLE_MODELS
except ImportError:
    import autocode_workflows as workflows, autopilot
    from autocode_configure import BUDGET_ARGUMENTS, DEFAULT_ROLE_MODELS


def build_parser(unit, default_models) -> argparse.ArgumentParser:
    """default_models is the selected provider's DEFAULT_MODELS, shown in the role-model help text."""
    parser = argparse.ArgumentParser(description="Independent requirements gathering, planning, plan review, build, validation and completion ownership")
    parser.add_argument("task", nargs="?", help="Idea for the requirements gatherer, planner and plan reviewer to turn into an approvable build brief")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--unit", choices=autopilot.UNITS, default=unit,
                        help="Run only this unit, stopping before the next unit; default runs Autopilot")
    parser.add_argument("--run-dir", type=Path, help="Existing run directory to resume")
    parser.add_argument("--conversation-handoff", type=Path,
                        help="Validated conversation receipt to attach when creating a task; never grants approval")
    parser.add_argument("--inspect-evidence", action="store_true",
                        help="With --status, inspect current source and saved evidence without running checks")
    parser.add_argument("--expected-recovery-token",
                        help="Require this exact inspected pause before applying a recovery action")
    parser.add_argument("--expected-goal-token",
                        help="Require this exact already-approved plan before continuing a dashboard Build request")
    parser.add_argument("--in-place", action="store_true", help="Use this checkout directly; otherwise new tasks get independent worktrees from HEAD")
    parser.add_argument("--workflow", choices=workflows.WORKFLOWS, help="Name the kind of job instead of having the recognizer read it from the request (a new run, or a saved run whose recognizer has not run yet)")
    parser.add_argument("--max-parallel-builders", type=int,
                        help="Orchestrator concurrency for independent milestones (new joint runs: 2; 1 dispatches serially)")
    parser.add_argument('--builder-strong-model', help='New-run Builder escalation model after one ordinary retry (default openai/gpt-6-sol, xhigh); pinned routes never escalate')
    parser.add_argument("--retry-builder", action="append", default=[], metavar="MILESTONE_ID",
                        help="Explicitly retry a stopped Builder after inspecting its retained work; requires --resume-paused")
    parser.add_argument("--figma-manifest", type=Path,
                        help="New run: immutable multi-file/frame/state inventory with exported Figma references; any saved engine")
    parser.add_argument("--task-preflight", type=Path,
                        help="Operator prerequisite manifest for planning/build/validation; repair only at its reconciled pause with --resume-paused")
    parser.add_argument("--figma-file", help="Figma Design URL to implement using the connected Codex plugin")
    parser.add_argument("--ui-run", type=Path, help="Accepted autocode-ui run to implement")
    parser.add_argument("--figma-review", choices=["automatic", "human"], help="Visual review policy for new Figma runs (default: automatic)")
    parser.add_argument("--engine", choices=["codex", "opencode"],
                        help="Select Codex or OpenCode; resumes keep the saved engine")
    parser.add_argument("--provider", default=None,
                        help="Tool that runs each role for a new run. Default: AUTOCODE_PROVIDER, then default_provider in "
                             "~/.config/autocode/config.toml, then opencode. Other names load ~/.config/autocode/providers/<name>.toml")
    parser.add_argument("--joint-planning", action="store_true",
                        help="Separate requirements, planning, and independent review; default for new OpenCode/GoCode runs, opt-in for Codex")
    parser.add_argument("--adaptive-planning", action=argparse.BooleanOptionalAction, default=None,
                        help="New runs plan adaptively by default when they use joint planning on the default flow: "
                             "skip requirements for a clear build request and let a Plan Reviewer with no blocking "
                             "concern approve the draft (docs/adaptive-planning.md); --no-adaptive-planning opts out")
    parser.add_argument('--planning-v2', action='store_true',
                        help='Opt in to transactional planning-v2 artifacts; never changes role models or the default planning flow')
    parser.add_argument("--glm-model", help="Planner model: OpenCode provider/model or native Codex GPT name")
    parser.add_argument("--glm-reasoning-effort", choices=["low", "medium", "high", "xhigh", "max"],
                        help="Override planner draft and revision reasoning effort")
    parser.add_argument("--requirements-model", help="Independent requirements-gatherer model for the saved engine")
    parser.add_argument("--requirements-reasoning-effort", choices=["low", "medium", "high", "xhigh", "max"],
                        help="Override independent requirements-gatherer reasoning effort")
    parser.add_argument("--resolver-model", help="Override the saved Resolver model without changing its engine")
    parser.add_argument("--resolver-reasoning-effort", choices=["low", "medium", "high", "xhigh", "max"],
                        help="Override the saved Resolver reasoning effort")
    autopilot.stuck.add_arguments(parser)
    parser.add_argument("--plan-reviewer-model",
                        help="Override the independent plan-reviewer model for the saved engine")
    parser.add_argument("--plan-reviewer-reasoning-effort", choices=["low", "medium", "high", "xhigh", "max"],
                        help="Override independent plan-reviewer reasoning effort")
    parser.add_argument("--test-command", help="Shell command for the project's test suite (default: detected); "
                        "repair a saved command at a reconciled pause with --resume-paused")
    parser.add_argument("--revise-protected-tests", type=Path,
                        help="Explicit user revision JSON for the original test inventory and command; requires a reconciled validation pause")
    parser.add_argument("--regression-command", help="Shell command for new or changed regression tests (default: derived); "
                        "repair a saved command at a reconciled pause with --resume-paused")
    parser.add_argument("--max-iterations", type=int, help="Total iteration ceiling (new-run default: unlimited; resumes keep saved limits)")
    parser.add_argument('--unlimited-iterations',action='store_true',help='Remove only the iteration ceiling; other safety and usage limits remain')
    for role, model in DEFAULT_ROLE_MODELS.items():
        label = {"astra": "plan reviewer", "terra": "builder", "sol": "validator",
                 "completion": "completion owner"}[role]
        parser.add_argument(f"--{role}-model",
                            help=f"Override the {label} model (joint default: "
                                 f"{default_models[role]}; "
                                 f"Codex-only default: {model}; resumes keep the saved model)")
    parser.add_argument("--astra-provider", help="Codex model_provider override for the Plan Reviewer (flag keeps the legacy Astra name)")
    parser.add_argument("--terra-provider", help="Codex model_provider override for the Builder (e.g. ZAI); default is the local Codex login")
    parser.add_argument("--sol-provider", help="Codex model_provider override for the Validator (e.g. ZAI); default is the local Codex login")
    parser.add_argument("--completion-provider", help="Codex model_provider override for the completion owner; default is the local Codex login")
    parser.add_argument("--reasoning-effort", choices=["low","medium","high","xhigh","max"])
    parser.add_argument("--astra-reasoning-effort", choices=["low","medium","high","xhigh","max"],
                        help="Override reasoning effort for plan review only")
    parser.add_argument("--terra-reasoning-effort", choices=["low","medium","high","xhigh","max"],
                        help="Override reasoning effort for the Builder only")
    parser.add_argument("--sol-reasoning-effort", choices=["low","medium","high","xhigh","max"],
                        help="Override reasoning effort for the Validator only")
    parser.add_argument("--completion-reasoning-effort", choices=["low","medium","high","xhigh","max"],
                        help="Override reasoning effort for the completion owner only")
    parser.add_argument("--pin-model-role", action="append", choices=tuple(DEFAULT_ROLE_MODELS), default=[],
                        help="Keep this role's selected model and reasoning effort instead of escalating it automatically")
    parser.add_argument("--tool-output-mode", choices=("raw", "conservative"), help="Display mode for AutoCode capture/output tools; saved across resume")
    parser.add_argument("--headroom", choices=["off","on"], default=None,
                        help="Off by default; on fails closed until compatibility is verified")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true",
                        help="Stream each stage's live model activity (tools started/finished, new provider text) to stderr")
    parser.add_argument("--migrate-only", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--pause-after-stage", action="store_true")
    parser.add_argument("--chat", action=argparse.BooleanOptionalAction, default=None,
                        help="Converse with the planner/reviewer and approve the brief here (default: on in an interactive terminal)")
    parser.add_argument("--context-soft-tokens", type=int)
    parser.add_argument("--rotate-after-input-tokens", type=int, help="0 disables checkpointed session rotation")
    parser.add_argument("--legacy-iteration-ceiling", type=int)
    parser.add_argument("--max-seconds", type=int, help="Total active provider time for the run (new-run default: 43200; 0 disables)")
    parser.add_argument("--milestone-checkpoints", action="store_true",
                        help="Enable enforced Builder/Validator/review milestone checkpoints on a saved run; new runs enable them by default")
    parser.add_argument("--request-milestone-checkpoints", action="store_true",
                        help="Queue a boundary pause and milestone configuration for an active saved run; never launches or stops workers")
    parser.add_argument("--max-milestone-seconds", type=int,
                        help="Active-time budget per milestone; with --resume-paused this also resets spent time (default: 5400; 0 disables)")
    parser.add_argument("--max-milestone-replans", type=int,
                        help="Maximum changed-approach replans per milestone (saved default: 1; 0 means unbounded)")
    parser.add_argument("--max-milestone-stalled-reviews", type=int,
                        help="Reviews without progress before replanning (saved default: 3; 0 disables)")
    parser.add_argument("--max-stage-seconds", type=int,
                        help="Hard runtime limit for one provider stage (new-run default: 3600; 0 disables; saved limits persist)")
    parser.add_argument("--max-idle-seconds", type=int,
                        help="Maximum provider inactivity outside a running tool (default: 300; 0 disables)")
    parser.add_argument("--max-tool-seconds", type=int,
                        help="Maximum time for a running tool or unreported descendant-tool interval (default: 1800; 0 disables)")
    parser.add_argument('--autoresolver-managed-limits', action='store_true',
                        help='Delegate finite CLI safety limits to bounded AutoResolver recovery; never changes billing/model routes')
    parser.add_argument("--no-progress-limit", type=int, help="Pause after this many unchanged batches (new-run default: 3)")
    parser.add_argument("--max-findings-per-task", type=int,
                        help="Reject a REWORK task that bundles more than this many open findings (default: unlimited; 0 disables)")
    parser.add_argument("--resume-paused", action="store_true", help="Acknowledge a saved pause; uncertain stages still require reconciliation")
    parser.add_argument('--resolver-request', help='Exact AutoResolver request ID for an operational response')
    parser.add_argument('--resolver-token', help='Exact current AutoResolver token for a human response')
    parser.add_argument('--resolver-response', choices=('provide_information', 'leave_paused'),
                        help='Respond to AutoResolver without authorizing execution or increasing limits')
    parser.add_argument('--resolver-message', default='', help='Corrective information for AutoResolver')
    parser.add_argument("--job-retry-token", help="Exact retry_job token for a stopped workflow job; requires --resume-paused --retry-failed-stage")
    parser.add_argument("--retry-failed-stage", action="store_true",
                        help="Authorize one fresh attempt for the recorded unchanged repeated failure after inspecting it; requires --resume-paused")
    parser.add_argument("--diagnose-failed-stage", action="store_true",
                        help="For a recorded repeated Builder report failure, admit one bounded "
                             "read-only model diagnosis instead of a blind retry; requires --resume-paused; "
                             "cannot combine with --retry-failed-stage")
    parser.add_argument("--grant-recovery", type=int, metavar="N",
                        help="With --resume-paused, authorize N more automatic timeout recoveries for a run paused at PAUSED_TIMEOUT_RECOVERY; the recovery history stays intact")
    parser.add_argument("--planning-review-call-limit", type=int, metavar="N",
                        help="Save a review allowance at a planning-budget pause; 0 disables the cap persistently and is also allowed at a reconciled stopped checkpoint; no agent launched")
    parser.add_argument("--retry-report", metavar="ATTEMPT_ID",
                        help="With --resume-paused, retry an exact exhausted format-failed report as fresh independent validation")
    parser.add_argument("--accept-transport-change", action="store_true",
                        help="With --resume-paused, accept the current validated OpenCode configuration at a clean transport-change pause")
    parser.add_argument("--abandon-stage", metavar="ATTEMPT_ID",
                        help="Set aside exactly this stopped uncertain attempt, preserving edits and logs; no agent is launched")
    parser.add_argument("--bind-dependency", help="Register an authorized prerequisite delivery from a JSON specification")
    parser.add_argument("--receive-dependency", help="Record a verified registered delivery manifest; never approves a plan")
    parser.add_argument("--show-goal", action="store_true", help="Display the exact contract revision and approval token")
    parser.add_argument("--answer", action="append", default=[], metavar="QUESTION_ID=TEXT")
    parser.add_argument("--feedback", metavar="TEXT", help="Send brief feedback to the Requirements Gatherer; never approves implementation")
    parser.add_argument("--follow-up", metavar="TEXT", help="Say the next thing to a finished run: it recognizes the new job and continues in the same run directory, carrying a review's findings forward")
    parser.add_argument("--delegate", action="append", default=[], metavar="QUESTION_ID",
                        help="Explicitly accept the proposed default and delegate this decision")
    parser.add_argument("--delegate-all", action="store_true",
                        help="Delegate every currently pending question marked delegable with a proposed default; "
                             "never grants approval and invalidates any existing one")
    parser.add_argument("--reject-assumption", action="append", default=[], metavar="ASSUMPTION_ID",
                        help="Reject a structured assumption from the current requirements handoff; "
                             "never grants approval and invalidates any existing one")
    parser.add_argument("--approve-goal", metavar="TOKEN", help="Approve exactly a previously displayed revision")
    parser.add_argument("--edit-goal", type=Path, help="Load a revised contract body JSON; invalidates approval")
    parser.add_argument("--approve-review", action="append", default=[], metavar="CRITERION_ID")
    parser.add_argument("--reconcile-review", metavar="CRITERION_ID=ANSWER_ID",
                        help="Bind an authenticated legacy acceptance to current validated evidence without a new approval")
    parser.add_argument("--accept-completion", action="store_true",
                        help="Operator-accept completion after the runner itself verifies every gate; use when the model's completion report cannot be produced")
    parser.add_argument("--review-token", help="Exact displayed contract/artifact/validation token; "
                        "also required by --delegate-all and --reject-assumption")
    return parser


def parse(unit, argv, default_models):
    """Parse argv (sys.argv[1:]) and return (args, parser); an invalid combination exits via parser.error."""
    parser = build_parser(unit, default_models)
    args = parser.parse_args(argv)
    args._explicit_budget_flags = set()
    budget_flags = {flag for flags in BUDGET_ARGUMENTS.values() for flag in flags}
    for argument in argv:
        if argument == '--':
            break
        if argument.startswith('--'):
            option = argument.split('=', 1)[0]
            # Preserve argparse's supported unambiguous abbreviations too.
            exact = parser._option_string_actions.get(option)
            matched = ({exact.dest} if exact else {action.dest for name, action in parser._option_string_actions.items()
                                                  if name.startswith(option)})
            if len(matched) == 1:
                args._explicit_budget_flags.update(matched & budget_flags)
    if args.inspect_evidence and (not args.run_dir or not args.status):
        parser.error("--inspect-evidence requires --run-dir and --status")
    if args.max_parallel_builders is not None and args.max_parallel_builders < 1:
        parser.error('--max-parallel-builders must be positive')
    if args.expected_recovery_token is not None and (not args.run_dir or not (args.resume_paused or args.abandon_stage)):
        parser.error('--expected-recovery-token requires a saved run and an explicit resume or abandon action')
    if args.retry_builder and (not args.run_dir or not args.resume_paused):
        parser.error('--retry-builder requires --run-dir and --resume-paused')
    if args.unlimited_iterations and (args.max_iterations is not None or args.legacy_iteration_ceiling is not None):
        parser.error('--unlimited-iterations cannot be combined with an explicit iteration ceiling')
    if args.accept_transport_change and (not args.run_dir or not args.resume_paused):
        parser.error("--accept-transport-change requires --run-dir and --resume-paused")
    if args.retry_report and (not args.run_dir or not args.resume_paused):
        parser.error("--retry-report requires --run-dir and --resume-paused")
    if args.job_retry_token and not (args.retry_failed_stage and args.resume_paused and args.run_dir):
        parser.error("--job-retry-token requires --run-dir --resume-paused --retry-failed-stage")
    if args.retry_failed_stage and (not args.run_dir or not args.resume_paused):
        parser.error("--retry-failed-stage requires --run-dir and --resume-paused")
    if args.diagnose_failed_stage and (not args.run_dir or not args.resume_paused):
        parser.error("--diagnose-failed-stage requires --run-dir and --resume-paused")
    if args.diagnose_failed_stage and args.retry_failed_stage:
        parser.error("--diagnose-failed-stage and --retry-failed-stage are alternative responses to the same pause; use one")
    if args.grant_recovery is not None and (not args.run_dir or not args.resume_paused):
        parser.error("--grant-recovery requires --run-dir and --resume-paused")
    if args.grant_recovery is not None and args.grant_recovery < 1:
        parser.error("--grant-recovery needs a positive number of recoveries")
    if args.resolver_response and not (args.run_dir and args.resolver_request and args.resolver_token):
        parser.error('--resolver-response requires --run-dir, --resolver-request and --resolver-token')
    if args.resolver_response and any((args.answer, args.delegate, args.approve_goal, args.approve_review,
                                      args.feedback is not None, args.retry_failed_stage, args.grant_recovery is not None,
                                      args.resume_paused)):
        parser.error('A resolver response cannot be combined with approval, feedback or execution authorization')
    if args.planning_review_call_limit is not None and args.planning_review_call_limit != 0 and args.planning_review_call_limit < 2:
        parser.error("--planning-review-call-limit must be 0 (unlimited) or at least 2")
    if unit and args.unit != unit:
        parser.error(f"This entry point runs only {unit}")
    if args.unit in ("autocode", "autoreview", "autoresolver") and not args.run_dir:
        parser.error("Build and review units require an existing --run-dir with an approved plan")
    if args.chat is None:
        args.chat = sys.stdin.isatty() and sys.stdout.isatty()
    if args.verbose:
        try:
            from . import autocode_verbose as verbose
        except ImportError:
            import autocode_verbose as verbose
        verbose.enable()
    for flag in ("max_iterations", "legacy_iteration_ceiling", "max_seconds", "max_stage_seconds", "max_idle_seconds", "max_tool_seconds", "no_progress_limit", "max_milestone_seconds", "max_milestone_replans", "max_milestone_stalled_reviews", "max_findings_per_task"):
        if getattr(args, flag) is not None and getattr(args, flag) < 0:
            parser.error(f"--{flag.replace('_', '-')} must be nonnegative")
    actions = [args.status, args.dry_run, args.migrate_only, args.show_goal,
               bool(args.answer or args.delegate), bool(args.delegate_all), bool(args.reject_assumption),
               bool(args.approve_goal), bool(args.edit_goal),
               bool(args.approve_review), bool(args.reconcile_review),
               args.feedback is not None, args.follow_up is not None, args.accept_completion, args.abandon_stage is not None,
               args.request_milestone_checkpoints, args.planning_review_call_limit is not None,
               args.bind_dependency, args.receive_dependency]
    if sum(bool(a) for a in actions) > 1:
        parser.error("Choose one action per invocation; answering and approving are separate events")
    if args.retry_builder and any(actions):
        parser.error("--retry-builder is a resume action; do not combine it with another action")
    if (args.delegate_all or args.reject_assumption) and not args.review_token:
        parser.error("--delegate-all and --reject-assumption require --review-token with the displayed goal token")
    if args.review_token and not (args.approve_review or args.reconcile_review
                                  or args.delegate_all or args.reject_assumption):
        parser.error("--review-token requires --approve-review, --reconcile-review, --delegate-all "
                     "or --reject-assumption")
    if args.reconcile_review and not args.review_token:
        parser.error("--reconcile-review requires --review-token")
    if not args.run_dir and any(actions[2:]):
        parser.error("User actions require an existing --run-dir")
    return args, parser
