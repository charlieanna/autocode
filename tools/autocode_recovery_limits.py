"""Admission policy for existing recovery allowances and unchanged denial holds.

An unchanged denial holds every launch until the operator authorizes one fresh attempt past that
exact denial (autocode_operational_retry); the next denial holds again.
"""
try:
    from .autocode_util import snapshot
    from . import autocode_operational_retry as operational_retry
    from . import autocode_recovery_accounting as accounting
    from .autocode_permission_recovery import hold_message
except ImportError:
    from autocode_util import snapshot
    import autocode_operational_retry as operational_retry
    import autocode_recovery_accounting as accounting
    from autocode_permission_recovery import hold_message

GRANT_ADVICE = (
    "After fixing the cause, authorize more recoveries explicitly with "
    "--resume-paused --grant-recovery N.")
# #301: at a hold that operational_retry.target accepts, information followed by a resume holds by
# design; the one action that moves the run is an explicit, single fresh attempt.
RETRY_ADVICE = (
    "After inspecting the cause, authorize exactly one fresh attempt with --resume-paused "
    "--retry-failed-stage; corrective information sent first with --resolver-response "
    "provide_information reaches that attempt. Counts and limits stay as they are, so a repeat stops again.")
INFORM_ADVICE = (
    "After fixing the cause, send the AutoResolver request corrective information "
    "with --resolver-request ID --resolver-token TOKEN --resolver-response "
    "provide_information --resolver-message TEXT, then --resume-paused.")
ABANDON_THEN_RESUME = (
    "Set the uncertain attempt aside with --abandon-stage {attempt}, then --resume-paused. "
    "A plain resume will hold; do not replay the failed attempt automatically.")
BOUND_ADVICE = {
    'PAUSED_TIME_LIMIT': (
        "To acknowledge this active-time pause, use --resume-paused --max-seconds N "
        "with a total above elapsed active time, or 0 for no time cap. "
        "You may reassert an already saved total; unrelated settings do not acknowledge this pause."),
    'PAUSED_ITERATION_LIMIT': (
        "After fixing the cause, raise the bound and continue in the same command with "
        "--resume-paused --max-iterations N (a different N supersedes this request)."),
    'PAUSED_MILESTONE_TIME_LIMIT': (
        "After fixing the cause, raise the bound and continue in the same command with "
        "--resume-paused --max-milestone-seconds N (a different N supersedes this request)."),
}


def abandon_advice(attempt: str) -> str:
    return ABANDON_THEN_RESUME.format(attempt=attempt)


def advice(*, allow_grant, pause_status=None, attempt=None, allow_retry=False):
    """The recovery-exhaustion next step. Never names a command the CLI will refuse."""
    if allow_retry:
        return RETRY_ADVICE
    if allow_grant:
        return GRANT_ADVICE
    if pause_status in BOUND_ADVICE:
        text = BOUND_ADVICE[pause_status]
        return text if not attempt else f"{text} {abandon_advice(attempt)}"
    if attempt:
        # #340: after provide_information a plain resume holds; the working step is abandon.
        return (INFORM_ADVICE + " " + abandon_advice(attempt))
    return INFORM_ADVICE


def stop_reason(state, count, maximum, *, allow_grant=True):
    context = state.get("recovery_context") or {}
    if (context.get("denied_operation") and context.get("repeat_count", 0) >= 2
            and snapshot(state["workspace"])["revision"] == context.get("source_revision")
            and not operational_retry.lifts_permission_hold(state, context)):
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
