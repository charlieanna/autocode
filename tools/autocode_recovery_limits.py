"""Admission policy for existing recovery allowances and unchanged denial holds."""
try:
    from .autocode_util import snapshot
    from .autocode_permission_recovery import hold_message
except ImportError:
    from autocode_util import snapshot
    from autocode_permission_recovery import hold_message


def stop_reason(state, count, maximum):
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
            "stop, not a request for approval. After fixing the cause, authorize more recoveries "
            "explicitly with --resume-paused --grant-recovery N.")
    return None
