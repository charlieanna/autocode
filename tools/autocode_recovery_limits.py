"""Admission policy for existing recovery allowances and unchanged denial holds."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_recovery_accounting as accounting
    from . import autocode_recovery_context as recovery_context
    from . import autocode_report_repair_queue as report_repair_queue
    from .autocode_permission_recovery import hold_message
    from .autocode_source_scope import snapshot
except ImportError:
    import autocode_recovery_accounting as accounting
    import autocode_recovery_context as recovery_context
    import autocode_report_repair_queue as report_repair_queue
    from autocode_permission_recovery import hold_message
    from autocode_source_scope import snapshot

GRANT_ADVICE = (
    "After fixing the cause, authorize more recoveries explicitly with "
    "autocode resume --grant-recovery N (with --run-dir RUN outside the run's project)."
)
INFORM_ADVICE = (
    "After fixing the cause, send the AutoResolver request corrective information "
    "with --resolver-request ID --resolver-token - (reads AUTOCODE_RESOLVER_TOKEN) --resolver-response "
    "provide_information --resolver-message TEXT, then autocode resume."
)
ABANDON_THEN_RESUME = (
    "Set the uncertain attempt aside with --abandon-stage {attempt}, then autocode resume. "
    "Resuming without setting it aside will hold; do not replay the failed attempt automatically."
)
# A published AutoResolver request a person must answer (a blocker, or exhausted operational
# recovery) takes this response and refuses --answer, which is for requirements questions. The
# status view names it as the need's action (#675: a live run's view said `answer`, the CLI said
# "use --resolver-response", and the person had to read the code to find the form).
RESPONSE_COMMAND = "--resolver-request ID --resolver-token - (reads AUTOCODE_RESOLVER_TOKEN) --resolver-response provide_information --resolver-message TEXT"
# Information on a blocker is retained, never applied to the contract. A decision that changes the
# approved contract (a miscounted criterion the Resolver proved contradictory, #675) has its own path,
# and the hold after the response must name it or a plain resume holds forever without saying why.
CONTRACT_DECISION_ADVICE = (
    "A decision that changes the approved contract takes effect through --edit-goal body.json "
    "(the goal_contract.body shape from state) followed by --approve-goal; plan feedback "
    "(--feedback) carries the correction to a fresh draft needing the same fresh approval; "
    "information alone changes nothing."
)
BOUND_ADVICE = {
    # #1815: the planning-review total is acknowledged by an allowance or by direction for the Planner.
    "PAUSED_PLANNING_BUDGET": (
        "To allow more plan reviews, use autocode --resume-paused --planning-review-call-limit N "
        "(more than the calls used, or 0 for no limit); or give the Planner direction with --feedback TEXT."
    ),
    "PAUSED_TIME_LIMIT": (
        "To acknowledge this active-time pause, use autocode resume --max-seconds N "
        "with a total above elapsed active time (and at least the total the stop names for an attempt on a "
        "stronger route), or 0 for no time cap. "
        "You may reassert an already saved total; unrelated settings do not acknowledge this pause."
    ),
    "PAUSED_COST_LIMIT": (
        "To acknowledge this cost pause, use autocode resume --max-cost-usd N "
        "with a cap above the spend so far (and at least the cap the stop names for a projected attempt), "
        "or 0 for no cost cap. AutoCode never raises this cap itself; "
        "you may reassert an already saved cap that is still above the spend; unrelated settings do not "
        "acknowledge this pause."
    ),
    "PAUSED_ITERATION_LIMIT": (
        "After fixing the cause, raise the bound and continue in the same command with "
        "autocode resume --max-iterations N (a different N supersedes this request)."
    ),
    "PAUSED_MILESTONE_TIME_LIMIT": (
        "After fixing the cause, raise the bound and continue in the same command with "
        "autocode resume --max-milestone-seconds N (a different N supersedes this request)."
    ),
    "PAUSED_MILESTONE_BUDGET": (
        "To acknowledge this milestone-budget pause, use autocode resume --max-milestone-seconds N with a "
        "budget other than the saved one (and at least the total the stop names for a stronger Builder attempt), "
        "or 0 for no milestone limit. Information alone or unrelated settings do not acknowledge this pause."
    ),
}
# A progressive run's slice budget pauses with the same status (autocode_progressive_state.check_local_budget),
# naming the ceilings it spent: `local_seconds`, the slice's, set by --max-milestone-seconds, and `run_seconds`, the
# run's, set by --max-seconds. A resume does not restart the slice's count (autocode_action_recovery), and only
# --max-milestone-seconds acknowledges the pause (autocode_pause_authority.PAUSE_BUDGET_KIND). advice() gives that
# stop this text in place of BOUND_ADVICE's, which names neither (slice_budget_governs).
SLICE_BUDGET_ADVICE = (
    "To acknowledge this progressive slice-budget pause, use autocode resume --max-milestone-seconds N with N above "
    "the slice's counted time, which a resume does not restart, or 0 for no slice limit. When the stop names "
    "run_seconds, the run's own ceiling is spent too: add --max-seconds M above the run's counted time (0 for no "
    "limit) to that command. Information alone or other settings do not acknowledge this pause."
)
# #448: information alone never admits a no-progress pause, so its advice must not end in a plain
# resume. It names the retained count, and the saved limit only when that limit is above the count:
# reasserting a limit at or below the count is refused (0 is offered as no cap either way).
NO_PROGRESS_ADVICE = (
    "To acknowledge this no-progress pause, use autocode resume --no-progress-limit N "
    "with N above the retained count of {count} unchanged implementation batches, or 0 for no cap.{saved} "
    "Information alone or unrelated settings do not acknowledge this pause."
)
NO_PROGRESS_SAVED = " Reasserting the saved limit, {limit}, also acknowledges it."
# The reason the build loop raises when the count reaches its limit (autocode_build_loop and
# autopilot's before_code_stage). Other holds pause with the same status for their own reasons.
NO_PROGRESS_CAUSE = "Repeated unchanged implementation batches require review"
# Texts AutoResolver composes, such as the note a response leaves or a republished request, start
# with this. The runner's own reason for a pause never does.
RESOLVER_NOTE = "AutoResolver "


def abandon_advice(attempt: str) -> str:
    return ABANDON_THEN_RESUME.format(attempt=attempt)


# A recovery novelty hold (autocode_resolver_recovery) names this as the one attempt it admits after
# inspection; authorize_retry checks the same held request.
SCOPED_RETRY = "--resume-paused --retry-failed-stage"
UNSETTLED = (
    "active_stage",
    "active_runner_check",
    "uncertain_artifacts",
    "orchestration_batch",
    "pending_report_repair",
)


def held_request(state: RunState, stage) -> dict:
    """The request a recovery dispatch of ``stage`` is admitted against: the Resolver's or the
    diagnosis request, or the repair plan or direct assignment that owns the current task."""
    if stage == "astra_resolve":
        return state.get("resolution_request") or {}
    if stage == "astra_diagnose":
        return state.get("diagnosis_request") or {}
    task_id = (state.get("current_task") or {}).get("id")
    plan = state.get("repair_plan") or {}
    if any(row.get("id") == task_id for row in plan.get("tasks", [])):
        return plan
    direct = next(
        (row for row in reversed(state.get("direct_rework_assignments", [])) if row.get("assigned_task_id") == task_id),
        None,
    )
    if direct:
        return next(
            (row for row in reversed(state.get("stages", [])) if row.get("output") == direct.get("source_output")), {}
        )
    return {}


UNSETTLED_REFUSAL = "Reconcile owned active or uncertain workers before authorizing recovery"

# The saved state that refuses a fresh execution retry, and the reason its own CLI gives. One predicate
# for the gate (autocode_stage_recovery.authorize_failure_retry) and every projection of it, so a pause
# cannot name a retry the person is then refused (#1897). Narrower than UNSETTLED, which also blocks a
# novelty hold's scoped retry: an active runner check or a building batch does not refuse this one.
RETRY_UNRECONCILED = ("active_stage", "uncertain_artifacts", "pending_report_repair")
RETRY_UNRECONCILED_REFUSAL = "Reconcile the active attempt or pending report repair before authorizing a retry"


def repeated_failure_retry_refusal(state: RunState) -> str | None:
    """Why ``--retry-failed-stage`` is refused for this saved state right now, else None."""
    return RETRY_UNRECONCILED_REFUSAL if any(state.get(key) for key in RETRY_UNRECONCILED) else None


HOLD_STATUSES = ("PAUSED_NO_PROGRESS", "WAITING_FOR_USER", "RESOLVER_PENDING")


def published_request(state: RunState) -> dict | None:
    """The published AutoResolver request whose ledger entry is still pending, else None.

    The status view's reading of the request. The CLI passes its receipt-verified request
    (``autocode_resolver_human.current``) to ``scoped_retry_refusal`` instead; this module cannot
    import that one without a cycle.
    """
    # autocode_goals.RESOLVER_REQUEST_KEY; this policy imports no contract module.
    public = state.get("resolver_human_request")
    if not isinstance(public, dict):
        return None
    entry = ((state.get("resolver") or {}).get("human_escalations") or {}).get(public.get("request_id"))
    return public if isinstance(entry, dict) and entry.get("status") == "pending" else None


def scoped_retry_refusal(state: RunState, public: dict | None) -> str | None:
    """Why the saved state cannot take an explicit scoped retry of its novelty hold now, else None.

    The one admission rule for ``--resume-paused --retry-failed-stage`` that reads only saved state:
    ``autocode_resolver_recovery.authorize_retry`` raises these reasons, and the status view names
    the retry only when this returns None, or shows the reason instead (#1770). ``public`` is the
    current published AutoResolver request, or None. The CLI separately rechecks the hold's source,
    settings and scope binding, which needs the run's files.
    """
    if any(state.get(key) for key in UNSETTLED):
        return UNSETTLED_REFUSAL
    if state.get("status") not in HOLD_STATUSES:
        return "Scoped recovery retry requires its no-progress hold"
    if public:
        entry = ((state.get("resolver") or {}).get("human_escalations") or {}).get(public.get("request_id")) or {}
        origin = ((entry.get("identity") or {}).get("proposal") or {}).get("origin") or {}
        if public.get("scope") != "operational_exhaustion" or origin.get("pause_status") != "PAUSED_NO_PROGRESS":
            return "A different human decision cannot grant a recovery retry"
    # autocode_goals.RESOLVER_PROPOSAL_KEY: AutoResolver's request before it is published.
    pending = state.get("resolver_human_proposal")
    if pending and (
        pending.get("scope") != "operational_exhaustion"
        or (pending.get("origin") or {}).get("pause_status") != "PAUSED_NO_PROGRESS"
        or (pending.get("origin") or {}).get("stage") != state.get("next_stage")
    ):
        return "A different pending decision cannot grant a recovery retry"
    if not (public or pending) and (
        state.get("status") != "PAUSED_NO_PROGRESS"
        or state.get("pending_questions")
        or (state.get("user_request") or {}).get("kind") not in (None, "none")
    ):
        return "A different pending decision cannot grant a recovery retry"
    return None


def scoped_retry(state: RunState) -> str | None:
    """``SCOPED_RETRY`` while a novelty hold on the next stage waits for one and the CLI would admit it."""
    if not held_request(state, state.get("next_stage")).get("novelty_hold"):
        return None
    return None if scoped_retry_refusal(state, published_request(state)) else SCOPED_RETRY


def scoped_retry_unavailable(state: RunState) -> str | None:
    """Why a novelty hold's one scoped retry cannot be given yet, else None (no hold, or it can)."""
    if not held_request(state, state.get("next_stage")).get("novelty_hold"):
        return None
    return scoped_retry_refusal(state, published_request(state))


def _no_progress_reasons(state: RunState, cause):
    """Texts that may give the runner's reason for a PAUSED_NO_PROGRESS hold, newest first."""
    yield cause
    yield state.get("stop_reason")
    escalations = [
        entry
        for entry in ((state.get("resolver") or {}).get("human_escalations") or {}).values()
        if isinstance(entry, dict)
    ]
    # Saved state sorts keys, so issue time, not the ledger's order, says which request came last.
    for entry in sorted(escalations, key=lambda entry: str(entry.get("issued_at") or ""), reverse=True):
        proposal = (entry.get("identity") or {}).get("proposal") or {}
        if (
            proposal.get("scope") != "operational_exhaustion"
            or (proposal.get("origin") or {}).get("pause_status") != "PAUSED_NO_PROGRESS"
        ):
            return
        yield (proposal.get("request") or {}).get("discovered")


def _no_progress_count_and_limit(state: RunState):
    limit = ((state.get("settings") or {}).get("limits") or {}).get("no_progress_batches", 3)
    return state.get("no_progress_batches", 0), limit


def no_progress_bound_holds(state: RunState, cause=None) -> bool:
    """True when the unchanged-batch limit, not another hold, paused this run as PAUSED_NO_PROGRESS.

    Other holds share the status (a recovery novelty hold, owned workers to reconcile) and name their
    own action, so only this one is advised to use --no-progress-limit. The reason is the first text
    AutoResolver did not compose: ``cause`` (the error being published), the stop reason, then the
    pause's earlier requests. After a response and a republished request, only the first request still
    names it. The count can be below a limit saved after the pause; reasserting that limit admits it.
    """
    count, limit = _no_progress_count_and_limit(state)
    if not (isinstance(limit, int) and isinstance(count, int)):
        return False
    for text in _no_progress_reasons(state, cause):
        if isinstance(text, str) and text and not text.startswith(RESOLVER_NOTE):
            return text.startswith(NO_PROGRESS_CAUSE)
    return False


def no_progress_advice(state: RunState) -> str:
    """The command that acknowledges a pause no_progress_bound_holds attributes to the limit."""
    count, limit = _no_progress_count_and_limit(state)
    saved = NO_PROGRESS_SAVED.format(limit=limit) if limit > 0 and count < limit else ""
    return NO_PROGRESS_ADVICE.format(count=count, saved=saved)


def slice_budget_governs(state: RunState | None) -> bool:
    """Whether a progressive run's slice budget, not the milestone budget, governs ``state``'s
    PAUSED_MILESTONE_BUDGET: the condition autocode_action_recovery and autocode_climb_cap read."""
    record = state.get("progressive") if state else None
    return isinstance(record, dict) and bool(record.get("budget"))


def advice(*, allow_grant, pause_status=None, attempt=None, state: RunState | None = None, cause=None):
    """The recovery-exhaustion next step. Never names a command the CLI will refuse.

    A PAUSED_NO_PROGRESS stop names its bound only with ``state`` whose limit caused it
    (no_progress_bound_holds; ``cause`` is the error being published). A PAUSED_MILESTONE_BUDGET stop
    under a progressive run's slice budget (slice_budget_governs) names that budget's controls.
    """
    if allow_grant:
        return GRANT_ADVICE
    if pause_status == "PAUSED_NO_PROGRESS" and no_progress_bound_holds(state or {}, cause):
        text = no_progress_advice(cast("RunState", state))
        return text if not attempt else f"{text} {abandon_advice(attempt)}"
    if pause_status in BOUND_ADVICE:
        slice_stop = pause_status == "PAUSED_MILESTONE_BUDGET" and slice_budget_governs(state)
        text = SLICE_BUDGET_ADVICE if slice_stop else BOUND_ADVICE[pause_status]
        return text if not attempt else f"{text} {abandon_advice(attempt)}"
    if attempt:
        # #340: after provide_information a plain resume holds; the working step is abandon.
        return INFORM_ADVICE.removesuffix(", then autocode resume.") + ". " + abandon_advice(attempt)
    return INFORM_ADVICE


def stop_reason(state: RunState, count, maximum, *, allow_grant=True):
    context = state.get("recovery_context") or {}
    if (
        context.get("denied_operation")
        and context.get("repeat_count", 0) >= 2
        and snapshot(state["workspace"], state)["revision"] == context.get("source_revision")
    ):
        return "PAUSED_REPEATED_FAILURE", hold_message(context)
    limit = state.get("settings", {}).get("limits", {}).get("no_progress_batches", 3)
    # A zero no-progress threshold does not disable the lifetime allowance.
    if count >= maximum or (limit and accounting.consecutive_timeouts(state) >= limit):
        cause = recovery_context.exhaustion_cause(state)
        return "PAUSED_TIMEOUT_RECOVERY", (
            f"Automatic recovery budget exhausted; no further provider will launch. Last cause: {cause}. "
            "AutoResolver retained the diagnosis and failure history; this is an operational "
            f"stop, not a request for approval. {advice(allow_grant=allow_grant)}"
        )
    return None


def repeated_failure_resume_guard(state: RunState, *, authorization=None, repeated_failure, current_revision, paused):
    """A restart or plain resume cannot erase an unchanged repeated failure.

    A recognized recovery path that reroutes the run changes the saved status
    before this guard runs. An operator who has inspected the failure can
    authorize one fresh attempt with --retry-failed-stage.
    """
    if state.get("status") != "PAUSED_REPEATED_FAILURE":
        return
    pending = state.get("pending_report_repair") or {}
    if report_repair_queue.pending_repair_dispatchable(state):
        # This resume dispatches that bounded report-only repair instead of restarting the failed
        # attempt, and the retry gate refuses --retry-failed-stage until it is reconciled (#1899).
        return
    record = pending.get("original") or next(
        (row for row in reversed(state.get("stages", [])) if row.get("failure_key")), None
    )
    if not record:
        return
    repeated = repeated_failure(state, record)
    if repeated and current_revision() == record.get("source_revision"):
        if (
            authorization
            and not authorization.get("consumed")
            and authorization.get("failure_key") == record.get("failure_key")
            and authorization.get("identity") == repeated["identity"]
            and authorization.get("count") == repeated["count"]
            and authorization.get("source_revision") == record.get("source_revision")
        ):
            authorization["consumed"] = True
            return
        raise paused(
            "PAUSED_REPEATED_FAILURE",
            f"Unchanged {record.get('original_stage') or record['stage']} artifact failed "
            f"{repeated.get('streak', repeated['count'])} consecutive times with the same {repeated['identity']['error_class']}; "
            "inspect failure_history and fix the cause before resuming, or authorize "
            "one inspected retry with --retry-failed-stage.",
        )
