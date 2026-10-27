"""Explain verified orchestration membership without granting task authority."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_changeset_store as store
except ImportError:
    import autocode_changeset_store as store


def context(state: RunState):
    """Only a bound automatic member gets the coordinator's proof responsibilities."""
    if not state.get("run_dir") or not state.get("workspace"):
        return None
    try:
        record = store.find(state["run_dir"])
        if not record or not record.get("automatic_anchor"):
            return None
        member = next(
            (
                row
                for row in store.members(record)
                if row.get("run_dir") == str(Path(state["run_dir"]).resolve())
                and row.get("workspace") == str(Path(state["workspace"]).resolve())
            ),
            None,
        )
    except (OSError, ValueError, store.ChangesetError):
        return None
    if member is None:
        return None
    return {
        "member": member["target"],
        "members": [row["target"] for row in store.members(record)],
        "instruction": (
            "This is one member of an automatic coordinated bug fix. Own only this repository's source changes "
            "and executable local regressions. Retain the original whole-bug outcome and report external owners. "
            "After every member has an approved plan and passes its own independent checks, the core coordinator "
            "creates a SEPARATE isolated integration workspace containing the actual code of every member. "
            "Its joint task derives and implements the real cross-repository regression and executable suite; "
            "the independent proof runner requires fail-before/pass-after against the original and composed code. "
            "Do not ask the person where that joint test lives or to supply its command. "
            "A local test double may verify this member's boundary, but never discharges the real joint obligation. "
            "Distinguish local acceptance evidence from the coordinator's later joint evidence; do not claim "
            "the whole bug is fixed on local tests alone or require an isolated member to own the joint workspace. "
            "The coordinator publishes linked PRs only after ALL member gates and independent joint proof pass. "
            "This is orchestration context, not a user requirement, scope waiver, approval or permission to edit "
            "other members, bypass a protected criterion, commit, push or create a PR yourself. "
            "Existing protected criteria still require their normal revision basis."
        ),
    }
