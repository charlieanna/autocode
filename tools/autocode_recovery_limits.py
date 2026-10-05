"""Admission policy for existing recovery allowances and unchanged denial holds."""
try:
    from .autocode_util import snapshot
    from . import autocode_recovery_accounting as accounting
    from .autocode_permission_recovery import hold_message
except ImportError:
    from autocode_util import snapshot
    import autocode_recovery_accounting as accounting
    from autocode_permission_recovery import hold_message

GRANT_ADVICE = (
    "After fixing the cause, authorize more recoveries explicitly with "
    "autocode resume --grant-recovery N (with --run-dir RUN outside the run's project).")
INFORM_ADVICE = (
    "After fixing the cause, send the AutoResolver request corrective information "
    "with --resolver-request ID --resolver-token TOKEN --resolver-response "
    "provide_information --resolver-message TEXT, then autocode resume.")
ABANDON_THEN_RESUME = (
    "Set the uncertain attempt aside with --abandon-stage {attempt}, then autocode resume. "
    "Resuming without setting it aside will hold; do not replay the failed attempt automatically.")
BOUND_ADVICE = {
    'PAUSED_TIME_LIMIT': (
        "To acknowledge this active-time pause, use autocode resume --max-seconds N "
        "with a total above elapsed active time, or 0 for no time cap. "
        "You may reassert an already saved total; unrelated settings do not acknowledge this pause."),
    'PAUSED_ITERATION_LIMIT': (
        "After fixing the cause, raise the bound and continue in the same command with "
        "autocode resume --max-iterations N (a different N supersedes this request)."),
    'PAUSED_MILESTONE_TIME_LIMIT': (
        "After fixing the cause, raise the bound and continue in the same command with "
        "autocode resume --max-milestone-seconds N (a different N supersedes this request)."),
}
# #448: information alone never admits a no-progress pause, so its advice must not end in a plain
# resume. It names the retained count, and the saved limit only when that limit is above the count:
# reasserting a limit at or below the count is refused (0 is offered as no cap either way).
NO_PROGRESS_ADVICE = (
    "To acknowledge this no-progress pause, use autocode resume --no-progress-limit N "
    "with N above the retained count of {count} unchanged implementation batches, or 0 for no cap.{saved} "
    "Information alone or unrelated settings do not acknowledge this pause.")
NO_PROGRESS_SAVED = " Reasserting the saved limit, {limit}, also acknowledges it."
# The reason the build loop raises when the count reaches its limit (autocode_build_loop and
# autopilot's before_code_stage). Other holds pause with the same status for their own reasons.
NO_PROGRESS_CAUSE = "Repeated unchanged implementation batches require review"
# Texts AutoResolver composes, such as the note a response leaves or a republished request, start
# with this. The runner's own reason for a pause never does.
RESOLVER_NOTE = "AutoResolver "


def abandon_advice(attempt: str) -> str:
    return ABANDON_THEN_RESUME.format(attempt=attempt)


def _no_progress_reasons(state, cause):
    """Texts that may give the runner's reason for a PAUSED_NO_PROGRESS hold, newest first."""
    yield cause
    yield state.get("stop_reason")
    escalations = [entry for entry in ((state.get("resolver") or {}).get("human_escalations") or {}).values()
                   if isinstance(entry, dict)]
    # Saved state sorts keys, so issue time, not the ledger's order, says which request came last.
    for entry in sorted(escalations, key=lambda entry: str(entry.get("issued_at") or ""), reverse=True):
        proposal = (entry.get("identity") or {}).get("proposal") or {}
        if (proposal.get("scope") != "operational_exhaustion"
                or (proposal.get("origin") or {}).get("pause_status") != "PAUSED_NO_PROGRESS"):
            return
        yield (proposal.get("request") or {}).get("discovered")


def _no_progress_count_and_limit(state):
    limit = ((state.get("settings") or {}).get("limits") or {}).get("no_progress_batches", 3)
    return state.get("no_progress_batches", 0), limit


def no_progress_bound_holds(state, cause=None) -> bool:
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


def no_progress_advice(state) -> str:
    """The command that acknowledges a pause no_progress_bound_holds attributes to the limit."""
    count, limit = _no_progress_count_and_limit(state)
    saved = NO_PROGRESS_SAVED.format(limit=limit) if 0 < limit and count < limit else ""
    return NO_PROGRESS_ADVICE.format(count=count, saved=saved)


def advice(*, allow_grant, pause_status=None, attempt=None, state=None, cause=None):
    """The recovery-exhaustion next step. Never names a command the CLI will refuse.

    A PAUSED_NO_PROGRESS stop names its bound only with ``state`` whose limit caused it
    (no_progress_bound_holds; ``cause`` is the error being published).
    """
    if allow_grant:
        return GRANT_ADVICE
    if pause_status == 'PAUSED_NO_PROGRESS' and no_progress_bound_holds(state or {}, cause):
        text = no_progress_advice(state)
        return text if not attempt else f"{text} {abandon_advice(attempt)}"
    if pause_status in BOUND_ADVICE:
        text = BOUND_ADVICE[pause_status]
        return text if not attempt else f"{text} {abandon_advice(attempt)}"
    if attempt:
        # #340: after provide_information a plain resume holds; the working step is abandon.
        return INFORM_ADVICE.removesuffix(", then autocode resume.") + ". " + abandon_advice(attempt)
    return INFORM_ADVICE


def stop_reason(state, count, maximum, *, allow_grant=True):
    context = state.get("recovery_context") or {}
    if (context.get("denied_operation") and context.get("repeat_count", 0) >= 2
            and snapshot(state["workspace"])["revision"] == context.get("source_revision")):
        return "PAUSED_REPEATED_FAILURE", hold_message(context)
    limit = state.get("settings", {}).get("limits", {}).get("no_progress_batches", 3)
    # A zero no-progress threshold does not disable the lifetime allowance.
    if count >= maximum or (limit and accounting.consecutive_timeouts(state) >= limit):
        cause = context.get("timeout_reason") or context.get("instruction", "Inspect saved provider logs")
        return "PAUSED_TIMEOUT_RECOVERY", (
            f"Automatic recovery budget exhausted; no further provider will launch. Last cause: {cause}. "
            "AutoResolver retained the diagnosis and failure history; this is an operational "
            f"stop, not a request for approval. {advice(allow_grant=allow_grant)}")
    return None
