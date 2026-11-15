"""Pure runner-owned refusal receipts shared by public views and domain facts.

This module performs no I/O or recovery and grants no command authority.
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from .autocode_run_state import RunState

METADATA = "metadata_custody"
WORKER_EVIDENCE = "worker_evidence"
ADMISSION = "metadata_admission"


class Policy(TypedDict):
    status: str
    operation: str
    preload: bool


POLICY: dict[str, Policy] = {
    METADATA: {"status": "PAUSED_PROCESS_CLEANUP", "operation": "metadata_check", "preload": True},
    WORKER_EVIDENCE: {"status": "PAUSED_ORCHESTRATOR_WORKER", "operation": "worker_evidence", "preload": True},
    ADMISSION: {"status": "PAUSED_METADATA_ADMISSION", "operation": "metadata_check", "preload": False},
}


def priority(scope):
    """An unadmitted attempt cannot hide an observed ownership/evidence denial."""
    return 2 if scope == WORKER_EVIDENCE else 0 if scope == ADMISSION else 1


_PRESENTATIONS = {
    "verification_preparation": {
        "category": "blocked",
        "title": "AutoCode is blocked on verification preparation",
        "what_happened": "Verification preparation failed before a provider started. Source binding, cleanup or ownership remains unconfirmed; retained workers and work are kept. AutoCode will not start another stage or ask you to decide its recovery.",
    },
    "model_listing": {
        "category": "blocked",
        "title": "AutoCode is blocked on its model listing",
        "what_happened": "AutoCode could not confirm that its startup check has stopped. It kept the saved work and will not start another step.",
    },
    METADATA: {
        "category": "blocked",
        "title": "AutoCode is blocked on an internal check",
        "what_happened": "AutoCode could not confirm that an earlier model-free check stopped safely. Saved work and evidence are kept. AutoCode will not start another step or ask you to decide its recovery.",
    },
    WORKER_EVIDENCE: {
        "category": "blocked",
        "title": "AutoCode is blocked on its worker evidence",
        "what_happened": "AutoCode could not authenticate a stopped worker's evidence. Saved work and usage are kept. No result or retry was accepted, and AutoCode will not ask you to decide its recovery.",
    },
    ADMISSION: {
        "category": "blocked",
        "title": "AutoCode could not start an internal check",
        "what_happened": "An internal check was not admitted within its existing allowance. Saved work and usage are kept. AutoCode has not retried the check or accepted a result, and will not ask you to decide its recovery.",
    },
}


def receipt(state: RunState, scope=None):
    """Read the latest runner receipt per scope; no files, locks or probes.

    A later authenticated decision for a scope retires its earlier denial. The
    legacy verification/listing readers keep their existing per-scope contract.
    """
    resolver = state.get("resolver") or {}
    if not isinstance(resolver, dict):
        return None
    records = resolver.get("operational_receipts") or {}
    identities = resolver.get("operational_diagnostics") or []
    if not isinstance(records, dict) or not isinstance(identities, list):
        return None
    seen = set()
    selected = None
    for identity in reversed(identities):
        row = records.get(identity) if isinstance(identity, str) else None
        if not isinstance(row, dict) or row.get("runner_owned") is not True or row.get("engine") != "runner":
            continue
        detail = row.get("receipt") or {}
        kind = detail.get("scope") if isinstance(detail, dict) else None
        if (
            not isinstance(kind, str)
            or kind not in _PRESENTATIONS
            or kind in seen
            or (scope is not None and kind != scope)
        ):
            continue
        seen.add(kind)
        decision = row.get("decision")
        # Corruption of a known runner hold cannot erase that hold.
        held = (
            kind in POLICY
            if not isinstance(decision, dict)
            else decision.get("action") == "blocked" or (kind in POLICY and decision.get("action") != "recovered")
        )
        if held:
            if scope is not None:
                return row
            if selected is None or priority(kind) > priority(selected["receipt"]["scope"]):
                selected = row
        if scope is not None:
            return None
    return selected


def presentation(state: RunState):
    """The single internal-failure projection used by every public reader."""
    if current := receipt(state):
        return deepcopy(_PRESENTATIONS[current["receipt"]["scope"]])
    # Preserve the already-recorded verification outcome and startup contract.
    if state.get("status") == "PAUSED_INTERVENTION":
        for row in state.get("applied_interventions") or []:
            if not isinstance(row, dict):
                continue
            outcome = row.get("outcome") or {}
            if (
                row.get("kind") == "stop"
                and row.get("actor") == "autocode"
                and outcome.get("result") == "could_not_finish"
                and (outcome.get("evidence") or {}).get("operation") == "verification_preparation"
            ):
                return {
                    "category": "stopped",
                    "title": "AutoCode could not finish this task",
                    "what_happened": outcome.get("reason"),
                }
    return None
