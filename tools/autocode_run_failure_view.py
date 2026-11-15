"""One person-facing failure decision shared by the public status projections."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_model_listing_failure as model_listing
    from . import autocode_operational_exhaustion_view as operational
    from . import autocode_startup_timeout_view as startup
    from . import autocode_verification_failure as verification
except ImportError:
    import autocode_model_listing_failure as model_listing
    import autocode_operational_exhaustion_view as operational
    import autocode_startup_timeout_view as startup
    import autocode_verification_failure as verification


def presentation(state: RunState):
    failure = verification.presentation(state) or model_listing.presentation(state)
    if failure or operational.automatic_reconciliation_pending(state):
        return failure
    return startup.presentation(state) or operational.presentation(state, reconciliation_checked=True)
