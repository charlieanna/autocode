"""Route a refused stage report that a report repair cannot settle (autocode.reject_completed_stage).

Each route below takes a refusal only when it is exactly its case and leaves the state as it was otherwise;
the first that takes it wins. A refusal none of them takes goes on to the report-repair queue and, past that,
the existing pause.

- autocode_rewording_question: the person is asked to choose a rewording of the approved requirements;
- autocode_superseded_correction: a plan revision naming the original tests a fix replaces;
- autocode_planned_check_rework: the Builder, when the approved plan's check cannot find a name it gives.
- autocode_reported_check_rerun: the reviewer stage itself again, when a check it cited does not reproduce.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_planned_check_rework as planned_check_rework
    from . import autocode_reported_check_rerun as reported_check_rerun
    from . import autocode_rewording_question as rewording
    from . import autocode_superseded_correction as replanned
except ImportError:
    import autocode_planned_check_rework as planned_check_rework
    import autocode_reported_check_rerun as reported_check_rerun
    import autocode_rewording_question as rewording
    import autocode_superseded_correction as replanned


def reroute(state: RunState, record, error, ask) -> bool:
    """True when a route took the refusal; ``ask`` queues a person's question (resolver_human.queue)."""
    return bool(
        rewording.ask(state, record, error, ask)
        or replanned.send_back(state, record, error)
        or planned_check_rework.send_back(state, record, error)
        or reported_check_rerun.run_again(state, record, error)
    )
