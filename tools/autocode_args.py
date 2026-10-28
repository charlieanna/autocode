"""The autocode command line: every flag, and the checks between flags that argparse cannot express.

parse() builds the parser, parses the arguments, records which budget flags were given explicitly,
and stops with a usage error (exit 2) on an invalid combination. autocode._main_body keeps the
returned parser for the later errors that depend on the saved run.

An invocation that names no run and starts none (no task, no new-run input) acts on the saved run
autocode_run_finder chooses from the --workspace directory: ``autocode --status``, ``autocode``,
``autocode resume`` and the user actions work from the project or a task worktree. On a paused
or blocked run ``autocode resume`` also stands for --resume-paused (_acknowledges_pause).
``autocode status`` is ``autocode --status``. --run-dir without --workspace selects the run's
own checkout (a user's run; a parallel Builder's run keeps the usual workspace errors).
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import textwrap
from pathlib import Path

try:
    from . import autocode_acceptance_tests as acceptance_tests
    from . import autocode_cli_surface as cli_surface
    from . import autocode_compact_planning as compact_planning
    from . import autocode_conversation_args as conversation_args
    from . import autocode_design_args as design_args
    from . import autocode_goal_approval_args as goal_approval_args
    from . import autocode_job_args as job_args
    from . import autocode_package_scope as package_scope
    from . import autocode_project_config as project_config
    from . import autocode_related_profile as related_profile
    from . import autocode_resolver_human as resolver_human
    from . import autocode_review_args as review_args
    from . import autocode_role_transport as role_transport
    from . import autocode_run_finder as run_finder
    from . import autocode_skip_requirements as skip_requirements
    from . import autocode_subcommands as subcommands
    from . import autocode_task_input as task_input
    from . import autocode_utility_route as utility_route
    from . import autocode_verification_config as verification_config
    from . import autocode_workflows as workflows
    from . import autopilot
    from .autocode_configure import BUDGET_ARGUMENTS, DEFAULT_ROLE_MODELS, cost_cap
    from .autocode_control_tokens import TOKEN_ENV_VARS as TOKEN_ENV_VARS
    from .autocode_control_tokens import resolve_placeholders, validate_recovery_selector
except ImportError:
    import autocode_acceptance_tests as acceptance_tests
    import autocode_cli_surface as cli_surface
    import autocode_compact_planning as compact_planning
    import autocode_conversation_args as conversation_args
    import autocode_design_args as design_args
    import autocode_goal_approval_args as goal_approval_args
    import autocode_job_args as job_args
    import autocode_package_scope as package_scope
    import autocode_project_config as project_config
    import autocode_related_profile as related_profile
    import autocode_resolver_human as resolver_human
    import autocode_review_args as review_args
    import autocode_role_transport as role_transport
    import autocode_run_finder as run_finder
    import autocode_skip_requirements as skip_requirements
    import autocode_subcommands as subcommands
    import autocode_task_input as task_input
    import autocode_utility_route as utility_route
    import autocode_verification_config as verification_config
    import autocode_workflows as workflows
    import autopilot
    from autocode_configure import BUDGET_ARGUMENTS, DEFAULT_ROLE_MODELS, cost_cap
    from autocode_control_tokens import TOKEN_ENV_VARS as TOKEN_ENV_VARS
    from autocode_control_tokens import resolve_placeholders, validate_recovery_selector

# Inputs that only start a new run: with one of them and no task, nothing is looked up.
NEW_RUN_INPUTS = (
    "ui_run",
    "figma_file",
    "figma_additional_file",
    "figma_manifest",
    "figma_approved_scope",
    "figma_approved_scope_sha256",
    "figma_review",
    "in_place",
    "builder_strong_model",
    "role_transport",
    "conversation_handoff",
    "conversation_session",
    "test_root",
    "package",
    "new_run_dir",
    "replay_recording",
    "related_profile",
)
# The user actions that only read the run: they return before the run lock and save nothing, so
# with no unfinished run they may show a finished one. --show-goal is not one: it takes the lock,
# migrates and saves the run. --follow-up has its own rule (autocode_run_finder).
READ_ACTIONS = ("--status", "--explain", "--dry-run")
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
# Command words are never a one-word task; after `--` they remain task text.
COMMAND_WORDS = subcommands.COMMAND_WORDS
COMMAND_MARK = "\0command-word"


# Authorization tokens accept the value `-` to read the secret from the paired
# environment variable, keeping it out of argv: on Linux /proc/<pid>/cmdline is
# world-readable (0444) while /proc/<pid>/environ is owner-only (0400). A token
# given as a literal argv value still works; an ambient variable alone never
# acts as authorization because the option itself must be present.
def resolve_token_env_placeholders(args, parser, environ=os.environ) -> None:
    """Replace a `-` token value with the token from its environment variable."""
    if getattr(args, "resolver_token", None) == "-" and not (
        getattr(args, "answer", None) or getattr(args, "resolver_request", None) or getattr(args, "delegate", None)
    ):
        # The resolver token is only consumed by an answer or an operational response;
        # refuse the sentinel early instead of silently resolving a token nothing uses.
        parser.error("--resolver-token - requires --answer, --delegate or --resolver-request to name its consumer")
    resolve_placeholders(args, parser, environ)


def commands_help() -> str:
    """The commands handled before this parser runs (autocode_subcommands and COMMAND_WORDS), for --help."""
    words = subcommands.command_names()
    return textwrap.fill(
        f"Commands, typed first: autocode {' | '.join(words)}.",
        width=78,
        break_on_hyphens=False,
    )


def build_parser(unit, default_models) -> argparse.ArgumentParser:
    """default_models is the selected provider's DEFAULT_MODELS, shown in the role-model help text."""
    parser = cli_surface.CompactParser(
        # Python 3.14's argparse names the program from how Python started ("python -m unittest") when
        # no prog is given; the usage line keeps naming the command that was run, as before 3.14.
        prog=os.path.basename(sys.argv[0]) or "autocode",
        description=textwrap.fill(
            "Independent requirements gathering, planning, plan review, build, validation and completion ownership",
            width=78,
        ),
        epilog=commands_help(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--help-all", action=cli_surface.FullHelp, help="Show every accepted option")
    parser.add_argument(
        "task",
        nargs="?",
        help="Idea for the requirements gatherer, planner and plan reviewer to turn into an approvable build brief",
    )
    brief_input = parser.add_mutually_exclusive_group()
    brief_input.add_argument("--brief", help="Task text; use - to read literal UTF-8 text from stdin")
    brief_input.add_argument("--brief-file", type=Path, help="Read literal UTF-8 task text from this file")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="Project directory (default: current)")
    related_profile.add_arguments(parser)
    parser.add_argument(
        "--unit",
        choices=autopilot.UNITS,
        default=unit,
        help="Run only this unit, stopping before the next unit; default runs Autopilot",
    )
    parser.add_argument("--run-dir", type=Path, help="Existing run directory to resume")
    # Internal inputs used by the public replay driver and task-run interface.
    parser.add_argument("--new-run-dir", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--worker-receipt", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--replay-recording", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--replay-checks", choices=("fresh", "recorded"), default="fresh", help=argparse.SUPPRESS)
    conversation_args.add_arguments(parser)
    parser.add_argument(
        "--inspect-evidence",
        action="store_true",
        help="With --status, inspect current source and saved evidence without running checks",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="With --status, print one sentence (needs you, working, done or stopped) and the exact next "
        "command instead of the JSON payload",
    )
    parser.add_argument(
        "--evidence-provenance",
        choices=("fake", "live", "mixed", "unknown"),
        help="New run: disclose whether providers are scripted or live; names alone cannot attest this",
    )
    parser.add_argument(
        "--expected-recovery-token", help="Require this exact inspected pause before applying a recovery action"
    )
    parser.add_argument(
        "--expected-goal-token",
        help="Require this exact already-approved plan before continuing a dashboard Build request",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Use this checkout directly; otherwise new tasks get independent worktrees from HEAD",
    )
    parser.add_argument(
        "--workflow",
        choices=workflows.WORKFLOWS,
        help="Name the kind of job instead of having the recognizer read it from the request (a new run, or a saved run whose recognizer has not run yet)",
    )
    parser.add_argument(
        "--max-parallel-builders",
        type=int,
        help="Orchestrator concurrency for independent milestones (new joint runs: 2; 1 dispatches serially)",
    )
    parser.add_argument(
        "--builder-strong-model",
        help="New-run Builder escalation model after one ordinary retry (default openai/gpt-6-sol, xhigh, or [builder_retry] in the provider config); pinned routes never escalate",
    )
    parser.add_argument(
        "--retry-builder",
        action="append",
        default=[],
        metavar="MILESTONE_ID",
        help="Explicitly retry a stopped Builder after inspecting its retained work; requires --resume-paused",
    )
    parser.add_argument(
        "--figma-manifest",
        type=Path,
        help="New run: immutable multi-file/frame/state inventory with exported Figma references; any saved engine",
    )
    design_args.add_scope(parser)
    parser.add_argument(
        "--revise-figma-manifest",
        type=Path,
        help="Stopped run: propose complete updated references, preserving history and requiring plan review",
    )
    parser.add_argument("--expected-design-hash", help="Exact inspected reference hash for --revise-figma-manifest")
    parser.add_argument("--design-change-reason", help="Concrete reason for --revise-figma-manifest")
    parser.add_argument(
        "--task-preflight",
        type=Path,
        help="Operator prerequisite manifest for planning/build/validation; repair only at its reconciled pause with --resume-paused",
    )
    parser.add_argument("--figma-file", help="Figma Design URL to implement using the connected Codex plugin")
    design_args.add_additional_file(parser)
    parser.add_argument("--ui-run", type=Path, help="Accepted autocode-ui run to implement")
    parser.add_argument(
        "--figma-review",
        choices=["automatic", "human"],
        help="Visual review policy for new Figma runs (default: automatic)",
    )
    parser.add_argument(
        "--engine",
        choices=["codex", "opencode", "claude"],
        help="Select Codex, OpenCode or Claude Code (the built-in claude provider); resumes keep the saved engine",
    )
    role_transport.add_arguments(parser)
    parser.add_argument(
        "--allow-uncontained-tools",
        action="store_true",
        help="Built-in OpenCode runs: launch the Builder, Validator and other non-planning stages with "
        "OpenCode's own permission checks only, without the kernel tool boundary (macOS "
        "sandbox-exec or Linux bubblewrap, conformance-tested OpenCode). Saved with the run and recorded; "
        "new run or resume",
    )
    parser.add_argument(
        "--joint-planning",
        action="store_true",
        help="Separate requirements, planning, and independent review; default for new OpenCode runs, opt-in for Codex",
    )
    parser.add_argument(
        "--adaptive-planning",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="New runs plan adaptively by default when they use joint planning on the default flow: "
        "a Plan Reviewer with no blocking concern approves the draft, a large plan gets a third "
        "review call, and feedback on a shown plan goes to the Planner; Requirements always runs "
        "first (docs/adaptive-planning.md); --no-adaptive-planning opts out",
    )
    compact_planning.add_argument(parser)
    resolver_human.requirements_review.add_argument(parser)
    acceptance_tests.add_argument(parser)
    parser.add_argument(
        "--planning-v2",
        action="store_true",
        help="Opt in to transactional planning-v2 artifacts; never changes role models or the default planning flow",
    )
    parser.add_argument(
        "--glm-model",
        help="Planner model: OpenCode provider/model or native Codex GPT name; "
        "also the initial Requirements model unless --requirements-model is given",
    )
    parser.add_argument(
        "--single-model",
        help="Use one model for every role; permits same-model verification for single-subscription accounts",
    )
    parser.add_argument(
        "--auto-select",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="New run: choose each role's model from the tool's connected accounts (#1426): "
        "cheapest qualified route per role, checkers independent, per-role model flags still winning. "
        "Automatic by default, with fixed-route fallback when selection is unavailable; explicitly enabling it refuses instead. See "
        "`autocode models --selection` first",
    )
    parser.add_argument(
        "--glm-reasoning-effort", choices=EFFORTS, help="Override planner draft and revision reasoning effort"
    )
    parser.add_argument("--requirements-model", help="Independent requirements-gatherer model for the saved engine")
    parser.add_argument(
        "--requirements-reasoning-effort",
        choices=EFFORTS,
        help="Override independent requirements-gatherer reasoning effort",
    )
    parser.add_argument("--resolver-model", help="Override the saved Resolver model without changing its engine")
    parser.add_argument(
        "--resolver-reasoning-effort", choices=EFFORTS, help="Override the saved Resolver reasoning effort"
    )
    autopilot.stuck.add_arguments(parser)
    parser.add_argument(
        "--plan-reviewer-model", help="Override the independent plan-reviewer model for the saved engine"
    )
    parser.add_argument(
        "--plan-reviewer-reasoning-effort", choices=EFFORTS, help="Override independent plan-reviewer reasoning effort"
    )
    utility_route.add_arguments(parser)

    verification_config.add_arguments(parser)
    package_scope.add_arguments(parser)
    parser.add_argument(
        "--revise-protected-tests",
        type=Path,
        help="Explicit user revision JSON for the original test inventory and command; requires a reconciled validation pause",
    )
    parser.add_argument(
        "--base-patch",
        type=Path,
        help="Bug fixes: a patch that adds only instrumentation (a hook or variable the fix adds) to the "
        "original code, so a regression test using it can run and fail there; hash-pinned, no test "
        "files, must be contained in the final change; on a saved run use --resume-paused at a "
        "stop before the Validator",
    )

    parser.add_argument(
        "--max-iterations",
        type=int,
        help="Total iteration ceiling (new-run default: unlimited; resumes keep saved limits)",
    )
    parser.add_argument(
        "--unlimited-iterations",
        action="store_true",
        help="Remove only the iteration ceiling; other safety and usage limits remain",
    )
    for role, model in DEFAULT_ROLE_MODELS.items():
        label = {"astra": "plan reviewer", "terra": "builder", "sol": "validator", "completion": "completion owner"}[
            role
        ]
        parser.add_argument(
            f"--{role}-model",
            help=f"Override the {label} model (joint default: "
            f"{default_models[role]}; "
            f"Codex-only default: {model}; resumes keep the saved model)",
        )
    parser.add_argument(
        "--astra-provider",
        help="Codex model_provider override for the Plan Reviewer (flag keeps the legacy Astra name)",
    )
    parser.add_argument(
        "--terra-provider",
        help="Codex model_provider override for the Builder (e.g. ZAI); default is the local Codex login",
    )
    parser.add_argument(
        "--sol-provider",
        help="Codex model_provider override for the Validator (e.g. ZAI); default is the local Codex login",
    )
    parser.add_argument(
        "--completion-provider",
        help="Codex model_provider override for the completion owner; default is the local Codex login",
    )
    parser.add_argument("--reasoning-effort", choices=EFFORTS, help="Reasoning effort for roles without a role flag")
    parser.add_argument(
        "--astra-reasoning-effort", choices=EFFORTS, help="Override reasoning effort for plan review only"
    )
    parser.add_argument(
        "--terra-reasoning-effort", choices=EFFORTS, help="Override reasoning effort for the Builder only"
    )
    parser.add_argument(
        "--sol-reasoning-effort", choices=EFFORTS, help="Override reasoning effort for the Validator only"
    )
    parser.add_argument(
        "--completion-reasoning-effort", choices=EFFORTS, help="Override reasoning effort for the completion owner only"
    )
    parser.add_argument(
        "--pin-model-role",
        action="append",
        choices=tuple(DEFAULT_ROLE_MODELS),
        default=[],
        help="Keep this role's selected model and reasoning effort instead of escalating it automatically",
    )
    parser.add_argument(
        "--tool-output-mode",
        choices=("raw", "conservative"),
        help="Display mode for AutoCode capture/output tools; saved across resume",
    )
    parser.add_argument(
        "--headroom",
        choices=["off", "on"],
        default=None,
        help="Off by default; on fails closed until compatibility is verified",
    )
    parser.add_argument("--dry-run", action="store_true", help="Read-only preview of the saved run; launches nothing")
    parser.add_argument(
        "--verbose",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Stream each stage's live model activity (tools started/finished, new provider text) to stderr "
        "(default: on; --no-verbose or AUTOCODE_VERBOSE=0 turns it off)",
    )
    parser.add_argument("--migrate-only", action="store_true", help="Run the opt-in legacy run migration and stop")
    parser.add_argument("--status", action="store_true", help="Print the saved run's status as JSON; launches nothing")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the run's final status as the same JSON payload --status prints, instead of the summary text",
    )
    parser.add_argument(
        "--explain",
        action="store_true",
        help="Print a plain-English explanation of why the run stopped and what each offered command does (#715)",
    )
    parser.add_argument("--pause-after-stage", action="store_true", help="Stop at the next saved stage boundary")
    parser.add_argument(
        "--chat",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Converse with the planner/reviewer and approve the brief here (default: on in an interactive terminal)",
    )
    parser.add_argument("--context-soft-tokens", type=int, help="Input tokens after which a stage compacts its context")
    parser.add_argument("--rotate-after-input-tokens", type=int, help="0 disables checkpointed session rotation")
    parser.add_argument(
        "--legacy-iteration-ceiling", type=int, help="Iteration ceiling when --max-iterations is absent"
    )
    parser.add_argument(
        "--max-seconds", type=int, help="Total active provider time for the run (new-run default: 43200; 0 disables)"
    )
    parser.add_argument("--max-cost-usd", type=cost_cap, help="Pause when the run's spend reaches N USD (0: no cap)")
    parser.add_argument(
        "--milestone-checkpoints",
        action="store_true",
        help="Enable enforced Builder/Validator/review milestone checkpoints on a saved run; new runs enable them by default",
    )
    parser.add_argument(
        "--request-milestone-checkpoints",
        action="store_true",
        help="Queue a boundary pause and milestone configuration for an active saved run; never launches or stops workers",
    )
    parser.add_argument(
        "--max-milestone-seconds",
        type=int,
        help="Active-time budget per milestone; with --resume-paused this also resets spent time (default: 5400; 0 disables)",
    )
    parser.add_argument(
        "--max-milestone-replans",
        type=int,
        help="Maximum changed-approach replans per milestone (saved default: 1; 0 means unbounded)",
    )
    parser.add_argument(
        "--max-milestone-stalled-reviews",
        type=int,
        help="Reviews without progress before replanning (saved default: 3; 0 disables)",
    )
    parser.add_argument(
        "--max-stage-seconds",
        type=int,
        help="Hard runtime limit for one provider stage (new-run default: 3600; 0 disables; saved limits persist)",
    )
    parser.add_argument(
        "--max-idle-seconds",
        type=int,
        help="Maximum provider inactivity outside a running tool (default: 300; 0 disables)",
    )
    parser.add_argument(
        "--max-tool-seconds",
        type=int,
        help="Maximum time for a running tool or unreported descendant-tool interval (default: 1800; 0 disables)",
    )
    parser.add_argument(
        "--autoresolver-managed-limits",
        action="store_true",
        help="Delegate finite CLI safety limits to bounded AutoResolver recovery; never changes billing/model routes",
    )
    parser.add_argument(
        "--no-progress-limit", type=int, help="Pause after this many unchanged batches (new-run default: 3)"
    )
    parser.add_argument(
        "--max-findings-per-task",
        type=int,
        help="Reject a REWORK task that bundles more than this many open findings (default: unlimited; 0 disables)",
    )
    parser.add_argument(
        "--reconcile-only",
        metavar="TOKEN",
        help="Classify an unfinished checkpoint after confirmed supervisor loss without launching a provider; "
        "`-` reads AUTOCODE_RECONCILE_ONLY_TOKEN",
    )
    parser.add_argument(
        "--resume-paused",
        action="store_true",
        help="Acknowledge a saved pause; uncertain stages still require reconciliation",
    )
    parser.add_argument("--resolver-request", help="Exact AutoResolver request ID for an operational response")
    parser.add_argument(
        "--resolver-token",
        help="Exact current AutoResolver token for a human response; `-` reads AUTOCODE_RESOLVER_TOKEN "
        "so the secret stays out of argv and shell history",
    )
    parser.add_argument(
        "--resolver-response",
        choices=("provide_information", "leave_paused"),
        help="Respond to AutoResolver without authorizing execution or increasing limits",
    )
    parser.add_argument("--resolver-message", default="", help="Corrective information for AutoResolver")
    parser.add_argument(
        "--job-retry-token",
        help="Exact retry_job token for a stopped workflow job; requires --resume-paused --retry-failed-stage, "
        "or --answer route-ROLE=MODEL to name the model of a job stopped on quota or a content-filter refusal; "
        "`-` reads AUTOCODE_JOB_RETRY_TOKEN so the secret stays out of argv and shell history",
    )
    parser.add_argument(
        "--retry-failed-stage",
        action="store_true",
        help="Authorize one fresh attempt for the recorded unchanged repeated failure after inspecting it; requires --resume-paused",
    )
    parser.add_argument(
        "--diagnose-failed-stage",
        action="store_true",
        help="For a recorded repeated Builder report failure, admit one bounded "
        "read-only model diagnosis instead of a blind retry; requires --resume-paused; "
        "cannot combine with --retry-failed-stage",
    )
    parser.add_argument(
        "--grant-recovery",
        type=int,
        metavar="N",
        help="With --resume-paused, authorize N more automatic timeout recoveries for a run paused at PAUSED_TIMEOUT_RECOVERY; the recovery history stays intact",
    )
    skip_requirements.add_arguments(parser)
    parser.add_argument(
        "--plan-review-full",
        action="store_true",
        help="Keep full-depth plan review for this run: the allowance stays per planning cycle even for a small clear brief",
    )
    parser.add_argument(
        "--planning-review-call-limit",
        type=int,
        metavar="N",
        help="Save a review allowance at a planning-budget pause; 0 disables the cap persistently and is also allowed at a reconciled stopped checkpoint; no agent launched",
    )
    parser.add_argument(
        "--retry-report",
        metavar="ATTEMPT_ID",
        help="With --resume-paused, retry an exact exhausted format-failed report as fresh independent validation",
    )
    parser.add_argument(
        "--accept-transport-change",
        action="store_true",
        help="With --resume-paused, accept the current validated OpenCode configuration at a clean transport-change pause",
    )
    parser.add_argument(
        "--accept-source-edit",
        action="store_true",
        help="With --resume-paused, hand a paused repair the source edited while it was stopped; "
        "the contract, task, budget, proof and evidence pins stay",
    )
    parser.add_argument(
        "--abandon-stage",
        metavar="ATTEMPT_ID",
        help="Set aside exactly this stopped uncertain attempt, preserving edits and logs; no agent is launched",
    )
    parser.add_argument(
        "--recover-job-report",
        metavar="TOKEN",
        help="Adopt the exact inspected owner-lost Investigator file report from --status; no model launches and "
        "the provider exit stays unknown; `-` reads AUTOCODE_RECOVER_JOB_REPORT so the secret stays out of argv",
    )
    parser.add_argument(
        "--export-repair",
        action="store_true",
        help="Write the dispatched bounded repair as handoff/repair-spec.{json,md} beside the run so any "
        "executor can implement it; run state is untouched",
    )
    parser.add_argument(
        "--submit-repair",
        metavar="DIR",
        help="Adopt a completed external implementation delivered as a checkout of this repository at the "
        "dispatched base; re-runs the static gates and the regression proof, then advances or rejects the "
        "milestone with the failing criterion",
    )
    parser.add_argument(
        "--bind-dependency", help="Register an authorized prerequisite delivery from a JSON specification"
    )
    parser.add_argument(
        "--receive-dependency", help="Record a verified registered delivery manifest; never approves a plan"
    )
    parser.add_argument(
        "--show-goal", action="store_true", help="Display the exact contract revision and approval token"
    )
    parser.add_argument(
        "--answer", action="append", default=[], metavar="QUESTION_ID=TEXT", help="Answer an open question (repeatable)"
    )
    parser.add_argument(
        "--feedback",
        metavar="TEXT",
        help="Send brief feedback to the Requirements Gatherer; never approves implementation",
    )
    parser.add_argument(
        "--follow-up",
        metavar="TEXT",
        help="Say the next thing to a finished run: it recognizes the new job and continues in the same run directory, carrying a review's findings forward",
    )
    parser.add_argument(
        "--delegate",
        action="append",
        default=[],
        metavar="QUESTION_ID",
        help="Explicitly accept the proposed default and delegate this decision",
    )
    parser.add_argument(
        "--delegate-all",
        action="store_true",
        help="Delegate every currently pending question marked delegable with a proposed default; "
        "never grants approval and invalidates any existing one",
    )
    parser.add_argument(
        "--reject-assumption",
        action="append",
        default=[],
        metavar="ASSUMPTION_ID",
        help="Reject a structured assumption from the current requirements handoff; "
        "never grants approval and invalidates any existing one",
    )
    goal_approval_args.add_arguments(parser)
    parser.add_argument("--edit-goal", type=Path, help="Load a revised contract body JSON; invalidates approval")
    parser.add_argument(
        "--goal-file", type=Path, metavar="PATH", help="Load a reviewed `autocode plan --export` file as the next plan"
    )
    review_args.add_arguments(parser)
    parser.add_argument(
        "--close-finding",
        action="append",
        default=[],
        metavar="FINDING_ID",
        help="Close an open reviewer finding as your own decision (repeatable), for example a "
        "duplicate of a problem already settled; needs --close-reason; launches no agent",
    )
    parser.add_argument(
        "--close-reason", metavar="TEXT", help="Why the findings named by --close-finding no longer apply"
    )
    parser.add_argument(
        "--accept-completion",
        action="store_true",
        help="Operator-accept completion after the runner itself verifies every gate; use when the model's completion report cannot be produced",
    )
    return parser


def user_actions(args) -> dict[str, bool]:
    """Each user action flag and whether this invocation gives it; at most one may be given."""
    return {
        "--status": bool(args.status),
        "--explain": bool(args.explain),
        "--dry-run": bool(args.dry_run),
        "--migrate-only": bool(args.migrate_only),
        "--reconcile-only": getattr(args, "reconcile_only", None) is not None,
        "--show-goal": bool(args.show_goal),
        "--answer/--delegate": bool(args.answer or args.delegate),
        "--delegate-all": bool(args.delegate_all),
        "--reject-assumption": bool(args.reject_assumption),
        "--approve-goal": bool(args.approve_goal),
        "--edit-goal": bool(args.edit_goal),
        "--goal-file": bool(args.goal_file),
        "--approve-review": bool(args.approve_review),
        "--reject-review": bool(args.reject_review),
        "--reconcile-review": bool(args.reconcile_review),
        "--feedback": args.feedback is not None,
        "--follow-up": args.follow_up is not None,
        "--accept-completion": bool(args.accept_completion),
        "--abandon-stage": args.abandon_stage is not None,
        "--recover-job-report": args.recover_job_report is not None,
        "--export-repair": bool(args.export_repair),
        "--submit-repair": args.submit_repair is not None,
        "--close-finding": bool(args.close_finding),
        "--request-milestone-checkpoints": bool(args.request_milestone_checkpoints),
        "--planning-review-call-limit": args.planning_review_call_limit is not None,
        "--bind-dependency": bool(args.bind_dependency),
        "--receive-dependency": bool(args.receive_dependency),
    }


def parse(unit, argv, default_models):
    """Parse argv (sys.argv[1:]) and return (args, parser); an invalid combination exits via parser.error."""
    argv = list(argv)
    # `resume` continues a saved run; the read commands become their corresponding flags.
    if resume_only := argv[:1] == ["resume"]:
        argv = argv[1:]
    elif argv and argv[0] in COMMAND_WORDS:
        argv = [f"--{argv[0]}", *argv[1:]]
    parser = build_parser(unit, default_models)
    if typo := subcommands.task_typed_as_option(argv):
        parser.error(typo)  # `autocode --task "..."` is --task-preflight to argparse
    args = parser.parse_args(argv)
    # The word may also follow options (`autocode --no-chat resume`): argparse reads it as the task.
    at = _command_word_at(parser, argv, args.task) if args.task in COMMAND_WORDS else None
    if at is not None:
        resume_only = resume_only or args.task == "resume"
        argv = argv[:at] + ([] if args.task == "resume" else [f"--{args.task}"]) + argv[at + 1 :]
        args = parser.parse_args(argv)
    if typo := subcommands.option_typed_as_task(parser, argv, args.task):
        parser.error(typo)  # `autocode approve-goal` is not a task
    if (args.brief is not None or args.brief_file is not None) and (args.run_dir or resume_only):
        parser.error("--brief and --brief-file start a new run; they cannot accompany a saved run or resume")
    if args.reconcile_only is not None:
        allowed = {"--reconcile-only", "--run-dir", "--workspace", "--json", "--no-chat"}
        if (
            resume_only
            or not args.run_dir
            or args.task
            or any(word.startswith("--") and word.split("=", 1)[0] not in allowed for word in argv)
        ):
            parser.error("--reconcile-only requires a saved run and cannot combine with other actions or settings")
    related_profile.validate_arguments(args, parser)
    try:
        args.task = task_input.load(args.task, brief=args.brief, brief_file=args.brief_file, stdin=sys.stdin)
    except (OSError, UnicodeError, ValueError) as error:
        parser.error(f"Cannot read brief: {error}")
    resolve_token_env_placeholders(args, parser)
    explicit, rest = set(), []
    budget_flags = {flag for flags in BUDGET_ARGUMENTS.values() for flag in flags}
    skip_value = False
    for index, argument in enumerate(argv):
        if argument == "--":
            rest += argv[index:]
            break
        if skip_value:
            skip_value = False
            continue
        dest = None
        if argument.startswith("--"):
            option = argument.split("=", 1)[0]
            # Preserve argparse's supported unambiguous abbreviations too.
            exact = parser._option_string_actions.get(option)
            matched = (
                {exact.dest}
                if exact
                else {action.dest for name, action in parser._option_string_actions.items() if name.startswith(option)}
            )
            if len(matched) == 1:
                (dest,) = matched
                explicit.add(dest)
        if dest in ("workspace", "run_dir", "unit"):
            # Left out of the flags a refusal repeats: its commands name the run and the unit themselves.
            skip_value = "=" not in argument
            continue
        rest.append(argument)
    if unit and args.unit != unit:
        parser.error(f"This entry point runs only {unit}")
    notice = _find_run(parser, args, explicit, resume_only, shlex.join(["resume", *rest] if resume_only else rest))
    explicit |= project_config.for_new_run(parser, args, explicit)  # the project's settings (#707)
    role_transport.engine_provider(args, parser)  # Claude Code facade (#717); codex is an engine (#2330)
    args._explicit_budget_flags = explicit & budget_flags
    args._resume_command = resume_only  # typed as `autocode resume`: who resumed records the word (#722)
    if resume_only and _acknowledges_pause(args):
        args.resume_paused = True
    conversation_args.validate(args, parser)
    if args.new_run_dir and (args.run_dir or not args.in_place or args.status or args.explain or args.dry_run):
        parser.error("--new-run-dir requires a fresh --in-place task invocation")
    if args.replay_checks == "recorded" and not args.replay_recording:
        parser.error("--replay-checks recorded requires --replay-recording")
    if args.summary and not args.status:
        parser.error("--summary requires --status")
    if args.inspect_evidence and (not args.run_dir or not args.status):
        parser.error("--inspect-evidence requires --run-dir and --status")
    validate_recovery_selector(args, parser)
    goal_approval_args.validate(args, parser)
    if args.max_parallel_builders is not None and args.max_parallel_builders < 1:
        parser.error("--max-parallel-builders must be positive")
    if args.retry_builder:
        _requires_resume(parser, args, "--retry-builder")
    if args.recover_job_report and any(
        (
            args.resume_paused,
            args.retry_failed_stage,
            args.retry_report,
            args.diagnose_failed_stage,
            args.grant_recovery is not None,
        )
    ):
        parser.error("--recover-job-report adopts an inspected report on its own; resume or retry separately")
    if args.unlimited_iterations and (args.max_iterations is not None or args.legacy_iteration_ceiling is not None):
        parser.error("--unlimited-iterations cannot be combined with an explicit iteration ceiling")
    if args.accept_transport_change:
        _requires_resume(parser, args, "--accept-transport-change")
    if args.accept_source_edit:
        _requires_resume(parser, args, "--accept-source-edit")
        if any(
            (
                args.retry_failed_stage,
                args.diagnose_failed_stage,
                args.grant_recovery is not None,
                args.retry_builder,
                args.retry_report,
                args.abandon_stage,
            )
        ):
            parser.error("--accept-source-edit is the response to a source-only stale repair; resume with it alone")
    if args.allow_uncontained_tools and (args.status or args.explain or args.dry_run):
        parser.error(
            "--allow-uncontained-tools is saved with the run; it cannot be combined with --status, --explain or --dry-run"
        )
    if args.retry_report:
        _requires_resume(parser, args, "--retry-report")
    job_args.validate(args, parser, explicit)
    if args.retry_failed_stage:
        _requires_resume(parser, args, "--retry-failed-stage")
    if args.diagnose_failed_stage:
        _requires_resume(parser, args, "--diagnose-failed-stage")
    if args.diagnose_failed_stage and args.retry_failed_stage:
        parser.error(
            "--diagnose-failed-stage and --retry-failed-stage are alternative responses to the same pause; use one"
        )
    if args.grant_recovery is not None:
        _requires_resume(parser, args, "--grant-recovery")
    if args.grant_recovery is not None and args.grant_recovery < 1:
        parser.error("--grant-recovery needs a positive number of recoveries")
    if args.resolver_response and not (args.run_dir and args.resolver_request and args.resolver_token):
        parser.error(
            "--resolver-response requires --run-dir, --resolver-request and --resolver-token"
            if not args.run_dir
            else "--resolver-response requires --resolver-request and --resolver-token"
        )
    if args.resolver_response and any(
        (
            args.answer,
            args.delegate,
            args.approve_goal,
            args.approve_review,
            args.reject_review,
            args.feedback is not None,
            args.retry_failed_stage,
            args.grant_recovery is not None,
            args.resume_paused,
        )
    ):
        parser.error("A resolver response cannot be combined with approval, feedback or execution authorization")
    if (
        args.planning_review_call_limit is not None
        and args.planning_review_call_limit != 0
        and args.planning_review_call_limit < 2
    ):
        parser.error("--planning-review-call-limit must be 0 (unlimited) or at least 2")
    if args.unit in ("autocode", "autoreview", "autoresolver") and not args.run_dir:
        parser.error("Build and review units require an existing --run-dir with an approved plan")
    if args.chat is None:
        args.chat = sys.stdin.isatty() and sys.stdout.isatty()
    if args.verbose is not None:
        try:
            from . import autocode_verbose as verbose
        except ImportError:
            import autocode_verbose as verbose
        if args.verbose:
            verbose.enable()
        else:
            verbose.disable()
    for flag in (
        "max_iterations",
        "legacy_iteration_ceiling",
        "max_seconds",
        "max_stage_seconds",
        "max_idle_seconds",
        "max_tool_seconds",
        "no_progress_limit",
        "max_milestone_seconds",
        "max_milestone_replans",
        "max_milestone_stalled_reviews",
        "max_findings_per_task",
    ):
        if getattr(args, flag) is not None and getattr(args, flag) < 0:
            parser.error(f"--{flag.replace('_', '-')} must be nonnegative")
    actions = user_actions(args)
    if args.close_finding and not args.run_dir:
        parser.error("--close-finding requires --run-dir")
    if len(given := [flag for flag, present in actions.items() if present]) > 1:
        parser.error(f"Choose one action per invocation: {' and '.join(given)} were given together")
    if args.retry_builder and any(actions.values()):
        parser.error("--retry-builder is a resume action; do not combine it with another action")
    if (args.delegate_all or args.reject_assumption) and not args.review_token:
        parser.error("--delegate-all and --reject-assumption require --review-token with the displayed goal token")
    review_args.validate(args, parser)
    if not args.run_dir and any(present for flag, present in actions.items() if flag not in READ_ACTIONS):
        parser.error("User actions require an existing --run-dir")
    if notice:
        # stderr: --status and --dry-run print exactly one JSON object on stdout.
        print(notice, file=sys.stderr, flush=True)
    return args, parser


def _command_word_at(parser, argv, word):
    """Where argparse reads ``word`` as the task among the options of argv, else None.

    After `--` it stays task text. The same word may also be an option's value (--feedback resume):
    mark each occurrence in turn until argparse reads the mark as the task. A word in place of a
    word parses alike. An argv argparse refuses exits via parser.error.
    """
    words = argv[: argv.index("--")] if "--" in argv else argv
    if word not in words or parser.parse_args(argv).task != word:
        return None
    return next(
        index
        for index, item in enumerate(words)
        if item == word and parser.parse_args([*argv[:index], COMMAND_MARK, *argv[index + 1 :]]).task == COMMAND_MARK
    )


def is_resume_command(argv) -> bool:
    """Whether autocode reads argv as `autocode resume`: the command word, not task text or an option's value.

    For a caller that must recognize the word without acting on the rest (autocode_unattended): on a
    paused or blocked run the word stands for --resume-paused (_acknowledges_pause). An argv
    argparse refuses exits via parser.error, as autocode itself would.
    """
    argv = list(argv)
    if argv[:1] == ["resume"]:
        return True
    return _command_word_at(build_parser(None, DEFAULT_ROLE_MODELS), argv, "resume") is not None


def _acknowledges_pause(args):
    """Whether `autocode resume` stands for --resume-paused: the run is paused or blocked
    (run_finder.resume_acknowledges) and nothing else is asked.

    Typing the command is the explicit acknowledgement --resume-paused records, with the same
    effects: no new budget or --grant-recovery allowance, but the per-cycle report-repair and
    resolver attempt counts restart and an invalid-output pause gets its one fresh attempt. A plain
    `autocode` still only shows the pause, and a finished or running run is left to the usual
    relaunch. Recovery companions also acknowledge a verified operational pause published as
    WAITING_FOR_USER; bare resume leaves that request alone. A design conflict waits for the
    user to edit the design: resume shows it.
    """
    if args.resume_paused or not args.run_dir or args.resolver_response or any(user_actions(args).values()):
        return False
    try:
        state = json.loads((Path(args.run_dir) / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    status = str(state.get("status", "")) if isinstance(state, dict) else ""
    if status == "WAITING_FOR_USER" and any(
        (
            args.retry_builder,
            args.retry_failed_stage,
            args.retry_report,
            args.diagnose_failed_stage,
            args.grant_recovery is not None,
            args.accept_transport_change,
            args.accept_source_edit,
            args.expected_recovery_token is not None,
            args._explicit_budget_flags,
        )
    ):
        # Publication changes the status, not the underlying pause. Verify its receipt rather
        # than treating a request's scope label as authority; locked recovery still checks it.
        issued = resolver_human.current(state)
        if issued and issued["scope"] == "operational_exhaustion":
            proposal = state["resolver"]["human_escalations"][issued["request_id"]]["identity"]["proposal"]
            status = str(proposal["origin"].get("pause_status", ""))
    return run_finder.resume_acknowledges(status)


def _requires_resume(parser, args, flag):
    """Refuse a resume companion without --resume-paused; name --run-dir only when no run was named or found."""
    if not args.run_dir:
        parser.error(f"{flag} requires --run-dir and --resume-paused")
    if not args.resume_paused:
        parser.error(f"{flag} requires --resume-paused")


def _find_run(parser, args, explicit, resume_only, flags):
    """Choose the saved run an invocation that names none means; return the notice to print, or None.

    explicit holds the destinations typed on the command line (--workspace defaults to the
    current directory, so its value alone cannot tell). flags is the rest of the command line
    without --workspace, --run-dir and --unit, for the commands a refusal lists; those name the
    unit (typed, or the entry point's) themselves.
    """
    new_run = [f"--{name.replace('_', '-')}" for name in NEW_RUN_INPUTS if getattr(args, name)]
    if args.explain and (args.run_dir is not None or args.task is None) and (args.task is not None or new_run):
        parser.error("--explain reads a saved run; do not provide a new task or new-run options")
    if resume_only and args.task is not None:
        parser.error('autocode resume continues a saved run; start a new task with autocode "TASK"')
    if resume_only and new_run:
        parser.error(f"autocode resume continues a saved run; {new_run[0]} only applies to a new one")
    if args.run_dir is not None:
        if "workspace" not in explicit:
            # The run's own checkout, so the run can be named from any directory.
            checkout = run_finder.checkout_of(args.run_dir)
            if checkout is not None:
                args.workspace = checkout
        return None
    given = [flag for flag, present in user_actions(args).items() if present]
    if args.task is not None or new_run or len(given) > 1:
        return None
    action = (
        "follow_up"
        if given == ["--follow-up"]
        else "read"
        if given and given[0] in READ_ACTIONS
        else "act"
        if given or args.resolver_response
        else "advance"
    )
    try:
        run = run_finder.choose(args.workspace, action, flags, args.unit)
    except run_finder.RunNotFound as error:
        # Not parser.error: its usage text would bury the runs and commands the message lists.
        parser.exit(2, f"autocode: {error}\n")
    args.run_dir, args.workspace = run.run_dir, run.workspace
    return f"Using the saved run {run.run_dir} ({run.status}, {run.reason}); add --run-dir to choose another."
