"""Typed human decisions for exact core-named commands; no grants or mutation.

Only the finite total-active-time recovery is described here. Other placeholders
remain unsupported until their own core contract is specified.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

MAX_INTEGER = 9007199254740991  # Exact in the Python and JavaScript clients.
TOTAL_TIME_ACTION = "--resume-paused --max-seconds N"


def project(state: RunState, need, resume_flags):
    """Describe one explicit finite total, never infer a value from human prose."""
    if (
        state.get("status") != "PAUSED_TIME_LIMIT"
        or not need
        or need.get("kind") != "resume"
        or need.get("new_run_required")
        or need.get("edit_required")
        or (need.get("action") or resume_flags) != TOTAL_TIME_ACTION
    ):
        return None
    current = (state.get("settings") or {}).get("limits", {}).get("max_seconds")
    elapsed = state.get("active_seconds")
    if type(elapsed) not in (int, float):
        return None
    elapsed = cast(int | float, elapsed)
    if (
        type(current) is not int
        or not 0 < current < MAX_INTEGER
        or not 0 <= elapsed < MAX_INTEGER
        or not math.isfinite(elapsed)
    ):
        return None
    return [
        {
            "id": "max_seconds",
            "kind": "integer",
            "flag": "--max-seconds",
            "placeholder": "N",
            # Executable and the owned --run-dir/--workspace pairs removed.
            "arg_index": 2,
            "label": "Total active time (seconds)",
            "current": current,
            "elapsed": elapsed,
            "minimum": math.floor(max(current, elapsed)) + 1,
            "maximum": MAX_INTEGER,
        }
    ]
