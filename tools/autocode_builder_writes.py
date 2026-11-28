"""What a Builder attempt may have changed, checked as soon as its report is applied, before anything else reads it.

Two runs fix files the Builder may not change: a spec-first run fixes the Tester's acceptance tests
(autocode_acceptance_tests.check_untouched, #1200), and a Review only run lets the Builder write nothing but its
findings (autocode_review_only.check_writes). Each puts the files back and sends the Builder again at once, a
bounded number of times. Autopilot asks here once, so a third such rule joins this list rather than the controller.
Imports only lower-level modules.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_acceptance_tests as acceptance_tests
    from . import autocode_review_only as review_only
except ImportError:
    import autocode_acceptance_tests as acceptance_tests
    import autocode_review_only as review_only


def sent_back(state: RunState, record: dict, workspace) -> bool:
    """True when the runner put files back and the Builder goes again at once, its other work kept.

    Otherwise the attempt kept to its files, and a Review only run's findings are recorded for the chat's result."""
    if acceptance_tests.check_untouched(state, workspace):
        return True
    if review_only.check_writes(state, record, workspace):
        return True
    review_only.record_findings(state, workspace)
    return False
