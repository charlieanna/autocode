"""One read-only internal-failure projection for the public run readers.

Domain facts use the pure runner-receipt projection directly. This view also
retains the existing authenticated provider-startup presentation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_no_progress_recovery as no_progress
    from . import autocode_operational_block_view as blocked
    from . import autocode_operational_disposition as disposition
    from . import autocode_operational_hold as hold
    from . import autocode_recovery_progress as recovery_progress
    from . import autocode_startup_timeout_view as startup
    from . import autocode_stop as stop
    from . import autocode_validation_exhaustion_recovery as validation
except ImportError:
    import autocode_no_progress_recovery as no_progress
    import autocode_operational_block_view as blocked
    import autocode_operational_disposition as disposition
    import autocode_operational_hold as hold
    import autocode_recovery_progress as recovery_progress
    import autocode_startup_timeout_view as startup
    import autocode_stop as stop
    import autocode_validation_exhaustion_recovery as validation

receipt = blocked.receipt


def presentation(state: RunState, *, include_hold=False):
    if stop.applied_stop(state) is not None:
        return None
    specific = blocked.presentation(state) or startup.presentation(state)
    if specific or not include_hold:
        return specific
    if not disposition.inspectable(state):
        return hold.presentation(state)
    check = state.get("active_runner_check")
    if state.get("status") == "PAUSED_VERIFICATION_UNCERTAIN" and isinstance(check, dict) and check.get("supervision"):
        return {
            "title": "AutoCode could not verify cleanup",
            "category": "blocked",
            "what_happened": (
                "AutoCode could not confirm that an internal verification command stopped safely. "
                "Saved work and evidence are retained; no result was accepted."
            ),
        }
    if recovery_progress.pending(state) or validation.pending(state) or no_progress.pending_legacy(state):
        return None
    return hold.presentation(state)
