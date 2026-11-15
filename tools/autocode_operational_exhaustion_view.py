"""Report an authenticated internal exhaustion without asking for machinery advice.

This is a denial-only view of the saved decision. It neither retries an attempt
nor changes a selected model or limit. Explicit/protected budget boundaries keep
their existing controls, and a proven automatic reconciliation takes precedence.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_no_progress_recovery as no_progress
    from . import autocode_pause_authority as pause_authority
    from . import autocode_recovery_progress as recovery_progress
    from . import autocode_resolver_human as human
    from . import autocode_stop_explanations as stop_explanations
    from . import autocode_validation_exhaustion_recovery as validation
except ImportError:
    import autocode_no_progress_recovery as no_progress
    import autocode_pause_authority as pause_authority
    import autocode_recovery_progress as recovery_progress
    import autocode_resolver_human as human
    import autocode_stop_explanations as stop_explanations
    import autocode_validation_exhaustion_recovery as validation


# These boundaries represent saved spending or scheduling choices. Reporting an
# internal failure must not remove the person's existing ability to change them.
PROTECTED_BOUNDS = frozenset(
    {
        "PAUSED_BUDGET",
        "PAUSED_COST_LIMIT",
        "PAUSED_TIME_LIMIT",
        "PAUSED_ITERATION_LIMIT",
        "PAUSED_PLANNING_BUDGET",
        "PAUSED_MILESTONE_TIME_LIMIT",
        "PAUSED_MILESTONE_BUDGET",
        "PAUSED_MILESTONE_STALLED",
    }
)


def automatic_reconciliation_pending(state: RunState):
    """An already-proven internal continuation must run before a blocked report."""
    issued = state.get(human.PUBLIC)
    if state.get("status") != "WAITING_FOR_USER" or not isinstance(issued, dict):
        return False
    if issued.get("scope") != "operational_exhaustion":
        return False
    return bool(
        recovery_progress.routine_resume(state, issued)
        or validation.pending(state)
        or no_progress.pending_legacy(state)
    )


def presentation(state: RunState, *, reconciliation_checked=False):
    issued = human.retained(state)
    if not issued or issued.get("scope") != "operational_exhaustion":
        return None
    if not reconciliation_checked and automatic_reconciliation_pending(state):
        return None
    entry = (state.get("resolver") or {}).get("human_escalations", {}).get(issued["request_id"], {})
    proposal = (entry.get("identity") or {}).get("proposal") or {}
    origin = proposal.get("origin") or {}
    pause = origin.get("pause_status")
    budget = origin.get("budget") or {}
    if (
        not pause_authority.operational(pause)
        or pause in PROTECTED_BOUNDS
        or budget.get("category") in ("explicit_user_cap", "protected_saved_limit")
    ):
        return None
    # Reuse the shared plain-language cause, never the saved model's raw text.
    # These generic recovery statuses describe an active hold in the ordinary
    # stop table, but this authenticated request has already exhausted it.
    if pause == "PAUSED_TIMEOUT_RECOVERY":
        cause = "A model step timed out and automatic recovery ran out of safe attempts."
    elif pause in ("PAUSED_RESOLVER", "PAUSED_RESOLVER_OPERATIONAL"):
        cause = "Automatic recovery could not settle the recorded failure."
    else:
        cause = stop_explanations.TABLE.get(pause, ("", ""))[0]
    return {
        "category": "blocked",
        "title": "AutoCode could not continue this task",
        "what_happened": (
            f"{cause} AutoCode could not safely continue this task. It kept the saved work and failure evidence."
        ).strip(),
        "what_it_means": "The task is not complete. AutoCode will not repeat the step without a safe recovery.",
    }
