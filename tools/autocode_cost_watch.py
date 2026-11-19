"""Whether a running stage has taken the run to the person's cost cap, read while it runs.

The cap (#721) was checked only between stages, so one stage could run well past it: in the live
VS Code test on 2026-11-18 a run capped at $6 paused at $8.30, its Builder alone costing about $2.40.
The stage supervisor (autocode_process.wait_for_stage, ``cost_reached``) now asks this checker while
the provider runs. Its answer adds the stage's usage so far, from its own event file, to the run's
known spend, prices it the way a finished stage is priced (autocode_cost.spent over the stage's
record), and names the cap once the total reaches it. The supervisor then ends the stage as the
person's Stop now does: its partial work is kept, the run pauses PAUSED_COST_LIMIT at the next
boundary, and raising the cap runs the stage again.

An unpriced stage (a model without a rate and no reported cost) cannot trip the cap, as at the
boundary. The checker reads only the run's saved state and this stage's own event file.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_cost as cost
    from . import autocode_support as support
    from . import autocode_util as util
except ImportError:
    import autocode_cost as cost
    import autocode_support as support
    import autocode_util as util


def reason(spent, limit) -> str:
    """The stop reason the stage record and the chat show."""
    return f"Stopped at your ${limit:g} cost cap: the run had spent about ${spent:.2f} with this stage's work so far"


def checker(state: RunState, record: dict, events):
    """A no-argument callable for the supervisor, or None when the run has no cap.

    The run's spend before this stage is read once, when the stage starts; the stage's own spend is
    read from ``events`` at each call.
    """
    limit = cost.cap(state.get("settings"))
    if limit is None:
        return None
    before = cost.spent(state)
    settings = state.get("settings")

    def reached():
        path = Path(events)
        if not path.is_file():
            return None
        running = {**record, "metrics": support.event_metrics(path), "finished_at": util.now()}
        total = before + cost.spent({"settings": settings, "stages": [running]})
        return reason(total, limit) if total >= limit else None

    return reached
