"""Runner-owned operational denials, shared by Core and its read-only views.

Receipts retain a refusal, never a command result or retry grant. Status readers
only project saved facts. Only advancement may authenticate the original
metadata family's cleanup; a missing journal is not that authentication.

There is no background re-entry: an independently authorized Core advancement
is required to examine a later cleanup proof. Legacy untyped pauses likewise
need a fresh producer observation; their prose does not acquire a new type.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_metadata_custody as custody
    from . import autocode_operational_block_view as block_view
    from . import autocode_run_status as run_status
    from . import autocode_util as util
except ImportError:
    import autocode_metadata_custody as custody
    import autocode_operational_block_view as block_view
    import autocode_run_status as run_status
    import autocode_util as util

METADATA = block_view.METADATA
WORKER_EVIDENCE = block_view.WORKER_EVIDENCE
STATUS = "PAUSED_PROCESS_CLEANUP"
receipt = block_view.receipt
presentation = block_view.presentation


class BlockedOperation(util.Paused):
    """A persisted denial or an authenticated worker relay, never a retry."""

    def __init__(self, record, *, persisted=False, worker=None):
        scope = record["receipt"]["scope"]
        super().__init__(
            "PAUSED_ORCHESTRATOR_WORKER" if scope == WORKER_EVIDENCE else STATUS,
            record.get("summary") or custody.BLOCKED,
        )
        self.record = deepcopy(record)
        self.persisted = persisted
        self.worker = deepcopy(worker)


class WorkerEvidenceUncertain(util.Paused):
    """The parent could not authenticate a relay; the claim grants nothing."""

    def __init__(self, worker):
        super().__init__(
            "PAUSED_ORCHESTRATOR_WORKER",
            "AutoCode could not authenticate the stopped worker's evidence; work and usage are retained.",
        )
        self.worker = deepcopy(worker)


def error(value):
    """Keep typed provenance through ordinary Core exception wrappers."""
    seen = set()
    pending = [value]
    while pending and len(seen) < 32:
        current = pending.pop()
        if not isinstance(current, BaseException) or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, (custody.CustodyUncertain, BlockedOperation, WorkerEvidenceUncertain)):
            return current
        pending.extend((current.__context__, current.__cause__))
    return None


def raise_if_blocked(value):
    if found := error(value):
        raise found


def _binding(state: RunState, run_dir, workspace):
    return {
        "workspace": str(Path(workspace).resolve()),
        "run_dir": str(Path(run_dir).resolve()),
        "task_id": state.get("task_id"),
        "task": util.digest(state.get("current_task")),
        "contract_hash": util.digest(state.get("goal_contract")),
        "settings_hash": util.digest(state.get("settings")),
        "next_stage": state.get("next_stage"),
    }


def _append(state: RunState, record):
    resolver = state.setdefault("resolver", {})
    identities = resolver.setdefault("operational_diagnostics", [])
    records = resolver.setdefault("operational_receipts", {})
    # A local diagnostic ordinal, not a model-citable or replay approval token.
    # Saved family nonces only locate original kernel evidence. A recorded
    # model answer cannot rebind them or discharge this denial.
    identity = "operational-block-" + str(len(identities) + 1)
    ordinal = len(identities) + 1
    while identity in records:
        ordinal += 1
        identity = "operational-block-" + str(ordinal)
    record["receipt"]["id"] = identity
    records[identity] = deepcopy(record)
    identities.append(identity)
    return records[identity]


def record(state: RunState, value, *, run_dir, workspace):
    """Retain the actual typed refusal before generic model-output recovery."""
    found = error(value)
    if found is None:
        return False
    if isinstance(found, BlockedOperation) and found.persisted:
        return True
    scope = WORKER_EVIDENCE if isinstance(found, WorkerEvidenceUncertain) else METADATA
    reason = str(found) if scope == WORKER_EVIDENCE else custody.BLOCKED
    evidence = {
        "operation": "worker_evidence" if scope == WORKER_EVIDENCE else "metadata_check",
        "binding": _binding(state, run_dir, workspace),
    }
    if scope == METADATA:
        binding = (
            found.binding
            if isinstance(found, custody.CustodyUncertain)
            else ((found.record.get("receipt") or {}).get("evidence") or {}).get("custody_binding")
        )
        evidence["custody_binding"] = deepcopy(binding)
    elif isinstance(found, WorkerEvidenceUncertain):
        evidence["worker"] = deepcopy(found.worker)
    if isinstance(found, BlockedOperation) and found.worker is not None:
        evidence["worker"] = deepcopy(found.worker)
    previous = receipt(state, scope)
    if previous is None or previous["receipt"].get("evidence") != evidence:
        _append(
            state,
            {
                "stage": "resolver",
                "role": "resolver",
                "engine": "runner",
                "runner_owned": True,
                "iteration": state.get("iteration"),
                "finished_at": util.now(),
                "exit_code": 0,
                "runner_calls": 0,
                "summary": reason,
                "decision": {"action": "blocked", "rationale": reason},
                "receipt": {"scope": scope, "callbacks_used": [], "evidence": evidence},
            },
        )
    run_status.pause(state, "PAUSED_ORCHESTRATOR_WORKER" if scope == WORKER_EVIDENCE else STATUS, reason)
    return True


def guard(runtime, state: RunState, run_dir, workspace):
    """Advancement only: no person control or missing file clears a denial.

    Custody authenticates the exact original private pending/archive and every
    retained kernel receipt. The nonce is a locator, never evidence by itself.
    This changes no active result, model route, budget, approval or retry bound.
    """
    if rejected := receipt(state, WORKER_EVIDENCE):
        raise BlockedOperation(rejected, persisted=True)
    current = receipt(state, METADATA)
    if current is None:
        return
    if current["receipt"]["scope"] == METADATA:
        evidence = current["receipt"].get("evidence") or {}
        binding = evidence.get("custody_binding") if isinstance(evidence, dict) else None
        if custody.recover(binding):
            recovered = deepcopy(current)
            recovered["finished_at"] = util.now()
            recovered["decision"] = {
                "action": "recovered",
                "rationale": "The original metadata family's cleanup was authenticated. Existing task gates still apply.",
            }
            recovered["receipt"]["recovered_receipt_id"] = current["receipt"]["id"]
            _append(state, recovered)
            runtime.write_json(Path(run_dir) / "state.json", state)
            return
    raise BlockedOperation(current, persisted=True)


def worker_payload(state: RunState, directory, workspace):
    current = receipt(state, METADATA)
    if current is None:
        return None
    evidence = current["receipt"].get("evidence")
    decision = current.get("decision")
    if (
        not isinstance(evidence, dict)
        or not isinstance(decision, dict)
        or decision.get("action") != "blocked"
        or evidence.get("binding") != _binding(state, directory, workspace)
    ):
        return None
    return {
        "schema": 1,
        "receipt_id": current["receipt"]["id"],
        "receipt_sha256": util.digest(current),
        "run_dir": str(Path(directory).resolve()),
        "workspace": str(Path(workspace).resolve()),
    }


def worker_refusal(batch, row, *, source_head=None, usage_unconfirmed=False):
    return WorkerEvidenceUncertain(
        {
            "run_dir": str(Path(row["run_dir"]).resolve()),
            "workspace": str(Path(row["workspace"]).resolve()),
            "parent_batch": batch.get("id"),
            "task_id": row["task"].get("id"),
            "source_head_matches": None if source_head is None else source_head == batch.get("base_commit"),
            "relay_authenticated": False,
            "usage_unconfirmed": usage_unconfirmed,
        }
    )


def worker_error(state: RunState, batch, row, result, child, *, run_dir, source_head):
    """Authenticate a denial from the existing owned worker assignment.

    A worker's current task/source and parent identity must still match. This
    relays only the hold: neither its changes nor a model result are accepted.
    """
    payload = result.get("operational_block")
    if payload is None:
        return None
    worker_state = cast("RunState", child) if isinstance(child, dict) else None
    current = receipt(worker_state, METADATA) if worker_state is not None else None
    valid = (
        isinstance(payload, dict)
        and set(payload) == {"schema", "receipt_id", "receipt_sha256", "run_dir", "workspace"}
        and all(isinstance(payload[key], str) for key in ("receipt_id", "receipt_sha256", "run_dir", "workspace"))
        and len(json.dumps(payload).encode()) <= 16384
        and type(payload.get("schema")) is int
        and payload["schema"] == 1
        and isinstance(child, dict)
        and current is not None
        and worker_state is not None
        and payload == worker_payload(worker_state, row["run_dir"], row["workspace"])
        and child.get("parent_run") == str(Path(run_dir).resolve())
        and child.get("parent_batch") == batch.get("id")
        and child.get("workspace") == row["workspace"]
        and child.get("run_dir", row["run_dir"]) == row["run_dir"]
        and child.get("current_task") == row["task"]
        and (child.get("goal_contract") or {}).get("hash") == batch.get("contract_hash")
        and (state.get("goal_contract") or {}).get("hash") == batch.get("contract_hash")
        and row["task"].get("source_revision") == (batch.get("baseline") or {}).get("revision")
        and source_head == batch.get("base_commit")
    )
    if not valid:
        raise worker_refusal(batch, row, source_head=source_head)
    return BlockedOperation(current, worker={**payload, "parent_batch": batch["id"], "task_id": row["task"]["id"]})
