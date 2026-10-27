"""Core-owned repository assignment; it is neither a human answer nor approval."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_changeset_store as store
    from . import autocode_workspace_scope_state as scope
except ImportError:
    import autocode_changeset_store as store
    import autocode_workspace_scope_state as scope


def current(state: RunState):
    run_dir = state.get("run_dir")
    if not run_dir:
        return None
    try:
        record = store.find(run_dir)
    except (OSError, ValueError, store.ChangesetError):
        return None
    if not record or not record.get("automatic_anchor"):
        return None
    receipt = (record.get("scope_assignments") or {}).get(str(Path(run_dir).resolve()))
    if not receipt or receipt.get("identity") != scope.identity(state):
        return None
    members = {row["target"]: row.get("run_dir") for row in store.members(record)}
    expected = {part["target"] for part in scope.current(state).get("external_parts") or []}
    if not expected or set(receipt.get("members") or {}) != expected:
        return None
    aliases = receipt.get("aliases") or {}
    if (
        not isinstance(aliases, dict)
        or set(aliases) - expected
        or any(not isinstance(v, str) for v in aliases.values())
    ):
        return None
    if any(
        not members.get(aliases.get(target, target)) or members[aliases.get(target, target)] != other
        for target, other in receipt["members"].items()
    ):
        return None
    return receipt
