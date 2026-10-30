"""Plain-English run and pause explanations (#715, #1269).

RELIABILITY.md item 4 asks to distinguish a user decision from an operational
failure and to say what a retry will do. `--status` shows the state and the
next command; this table is the words a person who did not watch the run
would use. No model call. A state without an entry fails
``tests/test_stop_explanations.py``.

The same text is what the status view's ``explanation`` field carries, so the
dashboard reads it from the view without a change of its own.
"""

from __future__ import annotations

try:
    from . import autocode_run_status as run_status
except ImportError:
    import autocode_run_status as run_status

# state -> (what it means, what the offered command does). Short enough to read
# aloud. "Yours" = a person's decision; "the environment's" = provider/host;
# "the model's" = a stage's report; "AutoCode's" = a runner gate.
COMPLETION = (
    "The saved run records completion; this status alone does not establish that the current checkout still "
    "matches its verification evidence.",
    "Use --status to inspect current evidence, or send a follow-up for another request; completion needs no recovery.",
)
TABLE: dict[str, tuple[str, str]] = {
    "TASK_COMPLETE": COMPLETION,
    "COMPLETE": COMPLETION,
    "RUNNING": (
        "Work is in progress; no new decision is needed while the controller is active.",
        "Use --status to follow progress and inspect controller liveness.",
    ),
    # A person decides
    "WAITING_FOR_USER": (
        "AutoCode needs a person: a question is open and nothing else can proceed.",
        "--answer or --feedback replies; nothing is overwritten until you do.",
    ),
    "AWAITING_GOAL_APPROVAL": (
        "The plan is ready and waiting for a person to approve it.",
        "--approve-goal accepts the plan; --feedback sends it back for a change.",
    ),
    "BLOCKED_HUMAN": (
        "A person's decision is required; the run stopped rather than guess.",
        "--resume-paused continues after you resolve the stop reason.",
    ),
    "PAUSED_REQUESTED": ("A person asked the run to pause.", "--resume-paused continues when you are ready."),
    "PAUSED_UNANSWERED_QUESTION": (
        "A question is still open; the run will not proceed past it.",
        "--answer or --delegate replies to the question.",
    ),
    "PAUSED_GOAL_UNAPPROVED": ("Work cannot start until the plan is approved.", "--approve-goal accepts the plan."),
    "PAUSED_CHANGESET_APPROVAL": (
        "This run's plan is approved, but its change set has not released every member for execution.",
        "Inspect changeset status and approve the current set, or explicitly approve this run alone.",
    ),
    "PAUSED_SCOPE_DECISION": (
        "Work in another repository still needs a person's decision about the current scope.",
        "Answer the current scope question; the revised plan still needs explicit approval.",
    ),
    "PAUSED_WORKFLOW_APPROVAL": (
        "A workflow change needs a person's approval before it is applied.",
        "--approve-goal or --feedback records the decision.",
    ),
    "PAUSED_CRITERIA_CHANGE": (
        "Someone changed the acceptance criteria; the run needs that change acknowledged.",
        "--feedback records the decision and the plan is revised.",
    ),
    "PAUSED_INTERFACE_CHANGE": (
        "An interface change needs a person's decision before work continues.",
        "--feedback or --answer records the decision.",
    ),
    "PAUSED_MILESTONE_HUMAN_REVIEW": (
        "A milestone needs human review before it is accepted.",
        "--approve-review accepts the milestone evidence.",
    ),
    "PAUSED_INTERVENTION": (
        "A person intervened; the run is waiting for that to be resolved.",
        "--resume-paused continues after the intervention is recorded.",
    ),
    "PAUSED_INTERVENTION_ACK": (
        "An intervention is recorded and waiting to be acknowledged.",
        "--resume-paused acknowledges and continues.",
    ),
    "PAUSED_INTERVENTION_PENDING": (
        "An intervention is pending; the run will not advance past it.",
        "--resume-paused or --feedback resolves it.",
    ),
    "PAUSED_PERMISSION": (
        "A command needs permission a person has not granted.",
        "--resume-paused or a permission answer grants it; nothing runs until then.",
    ),
    "PAUSED_PERMISSION_LOOP": (
        "Repeated permission answers did not change the source, contract or validation evidence.",
        "Inspect and repair the saved blocker, then use --feedback to explain the correction or new scope. "
        "Plain resume cannot launch another model call.",
    ),
    "PAUSED_PERMISSION_RECONCILIATION": (
        "Saved permissions do not match what the run needs.",
        "--resume-paused re-checks; a person may need to grant access.",
    ),
    "PAUSED_DESIGN_INPUT": (
        "A design input is needed before work can continue.",
        "--feedback or --answer supplies the missing input.",
    ),
    "PAUSED_DESIGN_INPUT_CHANGED": (
        "A design input changed after work started; the run needs that acknowledged.",
        "--feedback records the decision.",
    ),
    "PAUSED_DESIGN_CONFLICT": (
        "The approved design conflicts with the code; a person must decide.",
        "--feedback records the decision.",
    ),
    "PAUSED_DESIGN_REFERENCE": (
        "A design reference is missing or stale.",
        "--feedback or --answer supplies the reference.",
    ),
    # Environment / provider
    "PAUSED_BUDGET": (
        "A budget ran out (time, iterations or cost); this is an accounting stop, not a wrong result.",
        "--resume-paused --max-iterations N (or the matching budget flag) raises the bound and continues.",
    ),
    "PAUSED_COST_LIMIT": (
        "The run's spend reached the cost cap you set, or the next attempt is projected past it from the job's last; "
        "no stage launches past it.",
        "--resume-paused --max-cost-usd N with a higher cap (or 0 for none) continues; AutoCode never raises it.",
    ),
    "PAUSED_TIME_LIMIT": (
        "The run's active time (its model attempts' durations added together, not wall-clock time) reached its "
        "limit, or an attempt on a stronger route is projected past the limit you set.",
        "--resume-paused with a higher --max-seconds continues.",
    ),
    "PAUSED_STAGE_TIMEOUT": (
        "One stage exceeded its time limit.",
        "--resume-paused retries the stage; --max-stage-seconds raises the cap.",
    ),
    "PAUSED_ITERATION_LIMIT": (
        "The run used its iteration allowance.",
        "--resume-paused --max-iterations N continues.",
    ),
    "PAUSED_MILESTONE_BUDGET": (
        "A milestone's time budget is exhausted, or a stronger Builder attempt is projected past the budget you set.",
        "--resume-paused --max-milestone-seconds N continues.",
    ),
    "PAUSED_MILESTONE_TIME_LIMIT": (
        "A milestone hit its time limit.",
        "--resume-paused with a higher milestone limit continues.",
    ),
    "PAUSED_PLANNING_BUDGET": (
        "The planning budget is exhausted.",
        "--feedback or --resume-paused continues with the next planning step.",
    ),
    "PAUSED_PLANNING_ROUTE": (
        "A v2 planning stage has no saved route for its required role; AutoCode will not substitute another role.",
        "--resume-paused re-checks the saved routes. Start a new run with --joint-planning --planning-v2 to configure the required roles.",
    ),
    "PAUSED_PROGRESSIVE_BUDGET": (
        "A progressive run's budget is exhausted.",
        "--resume-paused raises the bound and continues.",
    ),
    "PAUSED_RATE_LIMIT": (
        "The provider rate-limited the run; this is an operational pause, not a product failure.",
        "--resume-paused retries; no work is lost.",
    ),
    "PAUSED_QUOTA": (
        "The provider quota is exhausted for this role's model.",
        "--answer route-ROLE=MODEL names another model, then --resume-paused.",
    ),
    "PAUSED_CONTENT_FILTER": (
        "The provider's content filter refused a response.",
        "--answer route-ROLE=MODEL continues on another model.",
    ),
    "PAUSED_AUTH": ("The provider needs authentication.", "Fix the provider login, then --resume-paused."),
    "PAUSED_PROVIDER_TIMEOUT": (
        "The provider timed out; the attempt is set aside.",
        "--resume-paused retries the stage.",
    ),
    "PAUSED_PROVIDER_UNCERTAIN": (
        "The provider result is uncertain (interrupted or incomplete).",
        "--resume-paused retries; the uncertain attempt is not treated as proof.",
    ),
    "PAUSED_PROVIDER_CAPACITY": ("The provider is at capacity.", "--resume-paused retries when capacity frees."),
    "PAUSED_OUTPUT_CAP": (
        "A stage exceeded its output-token cap.",
        "Answer the stopped role's model question with --answer route-ROLE=MODEL, then --resume-paused.",
    ),
    "PAUSED_CONTEXT_WINDOW": (
        "A stage's request no longer fit its model's context window, or the provider compacted the conversation; "
        "the response is not accepted.",
        "Answer the stopped role's model question with --answer route-ROLE=MODEL naming a larger-window model, "
        "then --resume-paused; or set the attempt aside and give the job a smaller task.",
    ),
    "PAUSED_TOOL_CONTAINMENT": (
        "The tool sandbox is not available on this host; a setup limit, not a product defect.",
        "Use a supported host or provider, then --resume-paused.",
    ),  # Except a private .autocode store's stop: STORE_CAUSES.
    "PAUSED_OPENCODE_SNAPSHOT": (
        "An OpenCode snapshot is missing or unreadable.",
        "--resume-paused re-checks; if it persists, the provider setup needs attention.",
    ),
    "PAUSED_TRANSPORT_CHANGED": (
        "The provider transport changed under the run.",
        "--accept-transport-change records it, then --resume-paused.",
    ),
    "PAUSED_TRANSPORT_MIGRATION": (
        "A transport migration is in progress.",
        "--resume-paused continues after the migration settles.",
    ),
    "PAUSED_TRANSPORT_UNVERIFIED": (
        "The transport identity is unverified.",
        "--resume-paused re-checks; a person may need to confirm the provider.",
    ),
    "PAUSED_BILLING_ROUTE": (
        "The billing or model route needs a decision.",
        "--answer route-ROLE=MODEL names the route.",
    ),
    "PAUSED_EFFORT_UNSUPPORTED": (
        "The reasoning effort you chose for a role is one its model does not offer.",
        "--answer effort-ROLE=EFFORT with one of the levels the model declares, then --resume-paused.",
    ),
    "PAUSED_CROSS_MODEL": (
        "The cross-model rule rejected a route.",
        "--answer route-ROLE=MODEL with a model that satisfies the rule.",
    ),
    "PAUSED_REGISTRY": (
        "The run registry is unavailable or unreadable.",
        "--resume-paused re-checks; if it persists, the host needs attention.",
    ),
    "PAUSED_WORKSPACE_BUSY": (
        "Another run holds the workspace.",
        "Wait for it to finish, or use --run-dir to pick another run.",
    ),
    "PAUSED_RUN_BUSY": ("The run directory is busy.", "--resume-paused retries; do not run two agents on one run."),
    "PAUSED_OWNERSHIP": (
        "Ownership of the run or workspace is uncertain.",
        "--resume-paused re-checks; a person may need to confirm ownership.",
    ),
    # AutoCode's own gates
    "PAUSED_INVALID_OUTPUT": (
        "A stage returned output the runner could not accept as a report.",
        "--resume-paused retries the stage; --abandon-stage ATTEMPT sets the bad attempt aside.",
    ),
    "PAUSED_REPEATED_FAILURE": (
        "The same failure repeated; AutoCode stopped instead of spending more budget.",
        "--resume-paused retries after a change, or --feedback gives a new direction.",
    ),
    "PAUSED_NO_PROGRESS": (
        "Several attempts made no progress; AutoCode stopped to avoid wasting budget.",
        "--feedback gives a new direction; --resume-paused retries after a change.",
    ),
    "PAUSED_COMPLETION_GATE": (
        "Completion was refused: required evidence is missing, stale or failed.",
        "The stop reason names the gap; supply the evidence, then --accept-completion, or --resume-paused for a fresh review.",
    ),
    "PAUSED_ACCEPTANCE_TESTS": (
        "Before any building, the Tester found the approved plan and the code disagree: a case already works or cannot be tested.",
        "--feedback revises the plan; the Tester runs again on the newly approved plan.",
    ),
    "PAUSED_COMPLETION_REVIEW": (
        "The Completion Reviewer asked for a decision before the run can end.",
        "--feedback or --accept-completion answers.",
    ),
    "PAUSED_STALE_DIAGNOSIS": (
        "The draft conflicts with its accepted diagnosis, and the saved correction or evidence does not "
        "currently authorize a refresh. The original proof cases remain required; implementation is held.",
        "--resume-paused rechecks the saved feedback, draft and diagnosis evidence. Only an eligible correction "
        "starts a fresh investigation; otherwise the run stays paused. The refreshed plan still needs approval.",
    ),
    "PAUSED_STALE_VALIDATION": (
        "The saved validation belongs to another source or goal revision.",
        "A fresh validation on the current source is required; --resume-paused starts it.",
    ),
    "PAUSED_STALE_HANDOFF": (
        "A handoff no longer matches the current work; it cannot be trusted.",
        "--resume-paused re-checks; the stale handoff is discarded.",
    ),
    "PAUSED_STALE_REPORT_ROUTE": (
        "A report is routed to a stage that no longer owns it.",
        "--resume-paused re-routes; --abandon-stage ATTEMPT sets the attempt aside.",
    ),
    "PAUSED_STALE_GOAL": (
        "The saved goal no longer matches the current task.",
        "--edit-goal updates it, then --resume-paused.",
    ),
    "PAUSED_STALE_TASK": (
        "The saved task no longer matches the current contract.",
        "A new task or --edit-goal is required.",
    ),
    "PAUSED_REPORT_REPAIR_INPUT": (
        "A report repair has no usable input to repair from.",
        "--abandon-stage ATTEMPT sets the attempt aside, then --resume-paused.",
    ),
    "PAUSED_REPORT_REPAIR_LIMIT": (
        "Report repair used its allowance without producing an accepted report.",
        "--abandon-stage ATTEMPT, then --resume-paused for a fresh attempt.",
    ),
    "PAUSED_INVALID_PREDECESSOR": (
        "A stage ran before its predecessor finished correctly.",
        "--resume-paused re-runs in order.",
    ),
    "PAUSED_JOB_FAILURE": (
        "A workflow job failed and needs a retry or a different model.",
        "--resume-paused --retry-failed-stage --job-retry-token - (reads AUTOCODE_JOB_RETRY_TOKEN) retries it.",
    ),
    "PAUSED_TASK_PREFLIGHT": (
        "The task did not pass preflight (paths, scope or identity).",
        "--feedback or --edit-goal corrects the task.",
    ),
    "PAUSED_ASSIGNMENT_SCOPE": (
        "The assignment is outside the approved scope.",
        "--feedback narrows or re-approves the scope.",
    ),
    "PAUSED_DISCOVERY_WRITE": (
        "Discovery wrote where it should not have.",
        "--resume-paused re-checks; the stray write is refused.",
    ),
    "PAUSED_PROCESS_CLEANUP": (
        "A child process did not clean up; AutoCode is waiting to confirm it is gone.",
        "--resume-paused re-checks; --abandon-stage ATTEMPT if it is stuck.",
    ),
    "PAUSED_PROCESS_CHECK": ("A process check is uncertain.", "--resume-paused re-checks."),
    "PAUSED_CONTROLLER_EXIT": (
        "The controller ended before advancing the saved running checkpoint; saved work is retained.",
        "--resume-paused continues from the last saved step after ownership is checked.",
    ),
    "PAUSED_INTERRUPTED": (
        "The run was interrupted; saved work is intact.",
        "--resume-paused continues from the last saved step.",
    ),
    "PAUSED_SOURCE_UNAVAILABLE": (
        "Git could not inventory the workspace source; no new source evidence was accepted.",
        "Fix repository access or ownership, then --resume-paused re-checks the source before proceeding.",
    ),
    "PAUSED_UNCERTAIN_STAGE": (
        "A stage ended with an uncertain result; it is not treated as proof.",
        "--resume-paused retries the stage.",
    ),
    "PAUSED_VERIFICATION_UNCERTAIN": (
        "Command cleanup is unverified; a new launch stays blocked until it is.",
        "--resume-paused re-checks; the uncertain attempt is set aside.",
    ),
    "PAUSED_VERIFICATION_PREPARATION": (
        "Preparing the verification source copy exceeded its finite allowance before the model started.",
        "--resume-paused retries preparation; source checks and the existing run budgets still apply.",
    ),
    "PAUSED_REVIEWER_FALLBACK": (
        "The reviewer route fell back; the run needs a decision on the next review.",
        "--resume-paused or --feedback records the decision.",
    ),
    "PAUSED_VISUAL_EVIDENCE": (
        "Visual evidence is missing, stale or outside the accepted capture.",
        "Run a fresh visual-capture, then --resume-paused.",
    ),
    "PAUSED_JOURNEY_UNVERIFIED": ("A journey's evidence is unverified.", "Supply the evidence, then --resume-paused."),
    "PAUSED_SKELETON_UNVERIFIED": ("The walking skeleton is unverified.", "Supply the evidence, then --resume-paused."),
    "PAUSED_LEGACY_COMPLETION_UNVERIFIED": (
        "A legacy completion claim has no current verification.",
        "Supply the evidence, then --accept-completion.",
    ),
    "PAUSED_MILESTONE_EVIDENCE": (
        "A milestone's evidence is missing or stale.",
        "Supply the evidence, then --resume-paused.",
    ),
    "PAUSED_MILESTONE_REPLAN": (
        "A milestone needs a replan; the run stopped rather than invent one.",
        "--feedback or --edit-goal records the new plan.",
    ),
    "PAUSED_MILESTONE_STALLED": (
        "A milestone made no progress across several reviews and used its replan allowance.",
        "--resume-paused --max-milestone-replans N raises the allowance (0 means unbounded).",
    ),
    "PAUSED_MILESTONE_TASK": (
        "A milestone's task is missing or invalid.",
        "--edit-goal corrects it, then --resume-paused.",
    ),
    "PAUSED_COMPONENT_PLAN": (
        "A component's plan is missing or unapproved.",
        "--approve-goal accepts it, or --feedback revises it.",
    ),
    "PAUSED_INHERITANCE": (
        "Inherited state could not be reconciled.",
        "--resume-paused re-checks; a person may need to confirm the inheritance.",
    ),
    "PAUSED_INTEGRATION_CHECK": (
        "An integration check failed.",
        "--resume-paused re-runs after the integration is fixed.",
    ),
    "PAUSED_INTEGRATION_DIRTY": (
        "The integration worktree is dirty.",
        "Clean or commit the integration worktree, then --resume-paused.",
    ),
    "PAUSED_MERGE_CONFLICT": ("A merge conflict needs a person.", "Resolve the conflict, then --resume-paused."),
    "PAUSED_ORCHESTRATOR_DRIFT": (
        "The orchestrator's view of the work drifted from the runner's.",
        "--resume-paused reconciles.",
    ),
    "PAUSED_ORCHESTRATOR_GIT": (
        "An orchestrator Git operation failed.",
        "--resume-paused retries; --abandon-stage ATTEMPT if it is stuck.",
    ),
    "PAUSED_ORCHESTRATOR_OWNERSHIP": (
        "Orchestrator ownership of a workstream is uncertain.",
        "--resume-paused re-checks.",
    ),
    "PAUSED_ORCHESTRATOR_WORKER": (
        "An orchestrator worker is unfinished.",
        "--resume-paused --retry-builder M retries that member.",
    ),
    "PAUSED_ORCHESTRATOR_WORKERS": (
        "Orchestrator workers are unfinished.",
        "--resume-paused --retry-builder M retries the named member.",
    ),
    "PAUSED_BUILDER_CLASSIFICATION": (
        "A Builder attempt needs classification before it can be retried.",
        "Inspect the current status: it offers a bound read-only Investigator retry when eligible, or a plan correction. Plain resume does not release the classification hold.",
    ),
    "PAUSED_BUILDER_OPERATIONAL": (
        "A Builder attempt stopped on an operational cause.",
        "--resume-paused retries; the cause is named in the stop reason.",
    ),
    "PAUSED_BUILDER_RETRY_LIMIT": (
        "A Builder used its retry allowance.",
        "--feedback a new direction, or --resume-paused after a change.",
    ),
    "PAUSED_RESOLVER": ("A Resolver decision is pending.", "--resume-paused continues after the decision is recorded."),
    "PAUSED_RESOLVER_OPERATIONAL": (
        "An operational pause is held until its own authority releases it.",
        "--resume-paused --retry-builder M or the named command in the stop reason.",
    ),
    "PAUSED_RESOLVER_STATE": ("Resolver state is inconsistent.", "--resume-paused re-checks."),
    "PAUSED_TIMEOUT_RECOVERY": ("A timeout recovery is in progress.", "--resume-paused continues after recovery."),
    "PAUSED_APPROVAL_DEFERRED": (
        "An approval was deferred; the run waits for it.",
        "--approve-goal or --feedback records the decision.",
    ),
    "PAUSED_WORKFLOW_CONFLICT": ("Two workflow policies conflict.", "--feedback records which policy wins."),
    "PAUSED_WORKFLOW_ROLLBACK": (
        "A workflow rollback is in progress.",
        "--resume-paused continues after the rollback settles.",
    ),
    "PAUSED_PROGRESSIVE_AUTHORITY": (
        "A progressive run's authority is missing or stale.",
        "--resume-paused re-checks the authority.",
    ),
    "PAUSED_PROGRESSIVE_TRANSITION": (
        "A progressive transition is pending.",
        "--resume-paused continues the transition.",
    ),
    "PAUSED_USAGE_UNKNOWN": (
        "Usage is unknown for an attempt; it is not counted as zero.",
        "--resume-paused continues; usage stays unknown until reported.",
    ),
    "PAUSED_STAGE_ABANDONED": (
        "A stage attempt was set aside as uncertain; it is not treated as proof.",
        "--resume-paused starts a fresh attempt; the abandoned one stays in the history.",
    ),
    "PAUSED_TOOL": (
        "A tool call failed or was refused.",
        "--resume-paused retries the stage; --abandon-stage ATTEMPT sets the attempt aside.",
    ),
    "PAUSED_METADATA": (
        "Run metadata is missing or unreadable.",
        "--resume-paused re-checks; if it persists, the run directory needs attention.",
    ),
    "PAUSED_FUTURE_REASON": (
        "A stop reason the current code does not recognise.",
        "Inspect the stop reason; --resume-paused continues if it is safe.",
    ),
    run_status.PAUSE_PHASE: (
        "Generic paused or blocked; the stop reason names the specific cause.",
        "Resolve the stop reason, then --resume-paused.",
    ),
    "PAUSED_UNSUPPORTED_CHECKPOINT": (
        "A checkpoint uses an unsupported shape.",
        "--resume-paused re-checks; the checkpoint may need to be re-recorded.",
    ),
    "RESOLVER_PENDING": (
        "A Resolver decision is pending; the run will not proceed past it.",
        "--resume-paused continues after the decision is recorded.",
    ),
    "WAITING_FOR_DEPENDENCY": (
        "The run waits for another run's accepted delivery; nothing is wrong with this run.",
        "`autocode wait --run-dir <producer>` returns when that producer needs you or ends; the delivery itself arrives from the dependency worker.",
    ),
}

# PAUSED_TOOL_CONTAINMENT stops about a private .autocode store, which a missing sandbox does not
# cause (#1250): a phrase of the stop reason autocode_tool_containment writes -> (means, does).
STORE_CAUSES: tuple[tuple[str, tuple[str, str]], ...] = (
    (
        "made after this launch's boundary was prepared",
        (
            "A private .autocode store appeared after this planning launch's tool boundary was prepared (for "
            "example, another AutoCode command in the same checkout made it), so that boundary did not deny "
            "reading it and no provider started. The sandbox and the setup are not the cause.",
            "--resume-paused prepares the boundary again, and the new boundary denies that store; nothing needs "
            "to change first.",
        ),
    ),
    (
        "replace it with a real directory",
        (
            "A private .autocode store that a planning launch must keep unreadable is a symlink or not a "
            "directory, which its tool boundary cannot deny, so no planning provider starts in this workspace. "
            "The sandbox is not the cause.",
            "Replace the path the stop reason names with a real directory, or remove it, then --resume-paused.",
        ),
    ),
)


# PAUSED_PROCESS_CLEANUP reasons that are the host's admission refusal, not a child process (the reason
# autocode_supervision raises before any provider starts; autocode doctor's session row names the same fix).
HOST_CAUSES = (
    (
        "refuses CAP_SYS_PTRACE or CAP_SYS_ADMIN",
        (
            "No provider was launched: this shell holds CAP_SYS_ADMIN or CAP_SYS_PTRACE (root in most containers, "
            "VMs and CI images), which provider session ownership refuses. No process is stuck; the saved run is "
            "intact. `autocode doctor` reports this as its session row.",
            "Run AutoCode as an ordinary user, or drop the two capabilities for every command: "
            "setpriv --bounding-set=-sys_admin,-sys_ptrace autocode ... , then --resume-paused{recovery}.",
        ),
    ),
    (
        "requires Linux native x86_64 or aarch64",
        (
            "No provider was launched: this host cannot own provider sessions (supported: Linux native x86_64 "
            "or aarch64, and macOS). No process is stuck; the saved run is intact.",
            "On a supported host (docs/install.md), --resume-paused{recovery}.",
        ),
    ),
)


READY = (
    "Nothing runs it: the controller exited on purpose after a saved answer, approval or follow-up, and the run "
    "moves only when someone runs the next command.",
    "The next command (--status names it) continues the run from its saved checkpoint.",
)


def explain(status: str, *, stop_reason: str = "", needs=None, held_cause: str = "", ready: bool = False) -> dict:
    """Three short paragraphs for a person who did not watch the run (#715).

    No model call. ``needs`` is the status view's ``needs`` block so the same
    text the view shows is what ``autocode explain`` prints. ``held_cause`` is the cause a pause
    still holds for when the stop reason only records that a person answered its request
    (autocode_pause_authority.answered_cause). ``ready`` is the controller inspection's ``ready``
    kind: a RUNNING checkpoint no controller runs, which the table's "in progress" words would have
    the person wait for (#2393).
    """
    stop_reason = stop_reason.strip().rstrip(".")
    held_cause = held_cause.strip().rstrip(".")
    if status == "PAUSED_INTERVENTION" and stop_reason.startswith(run_status.ENDED_PREFIX):
        # AutoCode ended the run itself with a recorded outcome (autocode_stop.end, #2485): a finished,
        # failed run, not a person's intervention, and no command continues it.
        happened = f"The run ended. {stop_reason}."
        means = "AutoCode could not finish this task on its own and recorded why; no stage runs again in this run."
        does = "Nothing continues this run. Its saved plan, changes and evidence stay for inspection; new work is a new run."
        return {
            "what_happened": happened,
            "what_it_means": means,
            "what_the_command_does": does,
            "explanation": f"{happened} {means} {does}",
        }
    entry = READY if status == "RUNNING" and ready else TABLE.get(status)
    means, does = (
        entry
        if entry
        else (f"AutoCode stopped at {status!r}.", "Inspect the stop reason and resume when it is resolved.")
    )
    if (
        status == "PAUSED_PROCESS_CLEANUP"
        and isinstance(needs, dict)
        and needs.get("native_cleanup") is True
        and needs.get("recovery_hint")
    ):
        # The native request predicate has already selected inspection-only or
        # conditional abandonment advice. Do not contradict it with generic
        # resume guidance; this text grants no original-owner authority.
        means = "Native planning cleanup requires original-owner verification before another stage can run."
        does = needs["recovery_hint"]
    if status == "PAUSED_PROCESS_CLEANUP":
        attempt = (needs or {}).get("abandon_stage") if isinstance(needs, dict) else None
        # A run saved before #2296 still holds the attempt that never launched as uncertain.
        recovery = (
            f"; this run recorded the attempt that never launched, so first --abandon-stage {attempt}"
            if attempt
            else ""
        )
        for phrase, (host_means, host_does) in HOST_CAUSES:
            if phrase in (stop_reason or ""):
                means, does = host_means, host_does.format(recovery=recovery)
                break
    if status == "PAUSED_TOOL_CONTAINMENT":
        # Every other cause keeps the table's text.
        for phrase, (store_means, store_does) in STORE_CAUSES:
            if phrase in (stop_reason or ""):
                means, does = store_means, store_does
                break
    owner = (
        "a person"
        if status
        in (
            "WAITING_FOR_USER",
            "AWAITING_GOAL_APPROVAL",
            "BLOCKED_HUMAN",
            "PAUSED_REQUESTED",
            "PAUSED_UNANSWERED_QUESTION",
            "PAUSED_GOAL_UNAPPROVED",
            "PAUSED_WORKFLOW_APPROVAL",
            "PAUSED_CRITERIA_CHANGE",
            "PAUSED_INTERFACE_CHANGE",
            "PAUSED_MILESTONE_HUMAN_REVIEW",
            "PAUSED_INTERVENTION",
            "PAUSED_INTERVENTION_ACK",
            "PAUSED_INTERVENTION_PENDING",
            "PAUSED_PERMISSION",
            "PAUSED_PERMISSION_RECONCILIATION",
            "PAUSED_DESIGN_INPUT",
            "PAUSED_DESIGN_INPUT_CHANGED",
            "PAUSED_DESIGN_CONFLICT",
            "PAUSED_DESIGN_REFERENCE",
            "PAUSED_MERGE_CONFLICT",
            "PAUSED_APPROVAL_DEFERRED",
            "PAUSED_COST_LIMIT",
        )
        else "AutoCode"
        if status.startswith("PAUSED_")
        else "the environment"
    )
    if status in ("TASK_COMPLETE", "COMPLETE"):
        happened = f"The run recorded {status}." + (f" Completion note: {stop_reason}." if stop_reason else "")
    elif status == "RUNNING":
        happened = "The run is RUNNING." + (" It is saved at a checkpoint with no controller running." if ready else "")
    else:
        happened = (
            f"The run stopped at {status}. "
            + (f"The stop reason is: {stop_reason}. " if stop_reason else "")
            + (f"The run is still held for: {held_cause}. " if held_cause else "")
            + f"This is {owner}'s decision, not a wrong deliverable."
        )
    return {
        "what_happened": happened.strip(),
        "what_it_means": means,
        "what_the_command_does": does,
        "explanation": (happened.strip() + " " + means + " " + does).strip(),
    }
