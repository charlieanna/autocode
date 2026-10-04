"""Admission policy for existing recovery allowances and unchanged denial holds."""
try:
    from .autocode_util import snapshot
    from .autocode_permission_recovery import hold_message
except ImportError:
    from autocode_util import snapshot
    from autocode_permission_recovery import hold_message

GRANT_ADVICE = (
    "After fixing the cause, authorize more recoveries explicitly with "
    "--resume-paused --grant-recovery N.")
INFORM_ADVICE = (
    "After fixing the cause, send the AutoResolver request corrective information "
    "with --resolver-request ID --resolver-token TOKEN --resolver-response "
    "provide_information --resolver-message TEXT, then --resume-paused.")
ABANDON_THEN_RESUME = (
    "Set the uncertain attempt aside with --abandon-stage {attempt}, then --resume-paused. "
    "A plain resume will hold; do not replay the failed attempt automatically.")
BOUND_ADVICE = {
    'PAUSED_TIME_LIMIT': (
        "After fixing the cause, raise the bound and continue in the same command with "
        "--resume-paused --max-seconds N (a different N supersedes this request)."),
    'PAUSED_ITERATION_LIMIT': (
        "After fixing the cause, raise the bound and continue in the same command with "
        "--resume-paused --max-iterations N (a different N supersedes this request)."),
    'PAUSED_MILESTONE_TIME_LIMIT': (
        "After fixing the cause, raise the bound and continue in the same command with "
        "--resume-paused --max-milestone-seconds N (a different N supersedes this request)."),
}


def abandon_advice(attempt: str) -> str:
    return ABANDON_THEN_RESUME.format(attempt=attempt)


def advice(*, allow_grant, pause_status=None, attempt=None):
    """The recovery-exhaustion next step. Never names a command the CLI will refuse."""
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
            and snapshot(state["workspace"])["revision"] == context.get("source_revision")):
        return "PAUSED_REPEATED_FAILURE", hold_message(context)
    limit = state.get("settings", {}).get("limits", {}).get("no_progress_batches", 3)
    # A zero no-progress threshold does not disable the lifetime allowance.
    if count >= maximum or (limit and state.get("consecutive_timeout_recoveries", 0) >= limit):
        cause = context.get("timeout_reason") or context.get("instruction", "Inspect saved provider logs")
        return "PAUSED_TIMEOUT_RECOVERY", (
            f"Automatic recovery budget exhausted; no further provider will launch. Last cause: {cause}. "
            "AutoResolver retained the diagnosis and failure history; this is an operational "
            f"stop, not a request for approval. {advice(allow_grant=allow_grant)}")
    return None
