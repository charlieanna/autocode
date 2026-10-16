"""The dashboard's only raw read of a run's saved state (state.json).

The dashboard is a client of the core: detail panes read the public status
view (``status_view``, from ``autocode --status``) instead. The run list still
shows saved fields the view does not carry, so those reads stay here, in one
place, until they move to the view (#730).
"""

import json
from pathlib import Path

NAME = "state.json"


def path(run) -> Path:
    return Path(run) / NAME


def read(run) -> dict:
    """The run's saved state; OSError or ValueError when it is unreadable or not an object."""
    value = json.loads(path(run).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("State is not an object")
    return value
