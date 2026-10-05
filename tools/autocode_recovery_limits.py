"""Admission policy for existing recovery allowances and unchanged denial holds.

A held external_directory denial (autocode_permission_recovery.holds) stops every launch except the
one fresh attempt an operator authorized past it in this invocation (autocode_failure_retry); the next
denial holds again.
"""
try:
    from .autocode_util import snapshot
    from . import autocode_failure_retry as failure_retry
    from . import autocode_recovery_accounting as accounting
    from . import autocode_permission_recovery as permission_recovery
except ImportError:
    from autocode_util import snapshot
    import autocode_failure_retry as failure_retry
    import autocode_recovery_accounting as accounting
    import autocode_permission_recovery as permission_recovery

GRANT_ADVICE = (
    "After fixing the cause, authorize more recoveries explicitly with "
    "--resume-paused --grant-recovery N.")
# #301: at a denial hold that failure_retry.target accepts, information followed by a resume holds by
# design; the one action that moves the run is an explicit, single fresh attempt.
RETRY_ADVICE = (
    "After inspecting the cause, authorize exactly one fresh attempt with --resume-paused "
    "--retry-failed-stage. Corrective information sent first with --resolver-request ID "
    "--resolver-token TOKEN --resolver-response provide_information --resolver-message TEXT "
    "reaches that attempt. No count or limit is reset, so a repeat stops again.")
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
    if (not failure_retry.lifts(state) and permission_recovery.holds(
            context, state.get("stages") or [], lambda: snapshot(state["workspace"])["revision"])):
        return "PAUSED_REPEATED_FAILURE", permission_recovery.stop_message(context)
    if accounting.exhausted(state, count, maximum):
        cause = context.get("timeout_reason") or context.get("instruction", "Inspect saved provider logs")
        return "PAUSED_TIMEOUT_RECOVERY", (
            f"Automatic recovery budget exhausted; no further provider will launch. Last cause: {cause}. "
            "AutoResolver retained the diagnosis and failure history; this is an operational "
            f"stop, not a request for approval. {advice(allow_grant=allow_grant)}")
    return None
