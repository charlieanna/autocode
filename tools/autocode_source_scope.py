"""Source selection from the sealed, explicitly approved run contract.

All milestones stay in scope, including completed ones: changing a previously
accepted ignored deliverable must invalidate later completion and recovery.
No Builder report, current-task suggestion, or ambient workspace file grants
additional source scope.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_contract_identity as contracts
    from . import autocode_source_snapshot as source
except ImportError:
    import autocode_contract_identity as contracts
    import autocode_source_snapshot as source


def declared_paths(body):
    """Canonical declaration only; the caller must establish approval separately."""
    rows = [*body.get("milestones", []), body.get("initial_task") or {}]
    return source.literal_paths([path for row in rows for path in row.get("affected_paths", [])])


def paths(state: RunState):
    try:
        authorized = contracts.approved(state)
    except (KeyError, TypeError, AttributeError):
        authorized = False
    if not authorized:
        return []
    return declared_paths(cast(dict, state["goal_contract"])["body"])


def snapshot(workspace, state: RunState, *, base_snapshot=None):
    return source.snapshot(workspace, paths=paths(state), base_snapshot=base_snapshot)
