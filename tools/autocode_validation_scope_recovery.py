"""Retire an obsolete no-progress question caused by a validation-only write handoff.

The correction only queues scope review. It grants no Builder attempt, approval,
evidence acceptance or counter credit; earlier implementation failures stay counted.
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_contract_identity as contracts
    from . import autocode_resolver_human as human
    from . import autocode_run_status as run_status
    from . import autocode_util as util
    from . import autocode_validation_recovery as validation_evidence
except ImportError:
    import autocode_contract_identity as contracts
    import autocode_resolver_human as human
    import autocode_run_status as run_status
    import autocode_util as util
    import autocode_validation_recovery as validation_evidence


NOTICE = "AutoCode will return the retained validation-only handoff for scope review; its limits remain unchanged."
REASON = (
    "The retained changed-line repair targeted Builder under a validation-only task. "
    "Review the unresolved evidence within the approved plan before assigning any correction. "
    "The read-only assignment, outstanding milestones, finding owners and no-progress count remain intact; "
    "a scope change needs the existing exact-plan review and approval."
)


def pending(state: RunState):
    """Recognize a sealed, still-bound historical misroute without reading files."""
    task, contract = state.get("current_task") or {}, state.get("goal_contract") or {}
    if (
        state.get("status") != "WAITING_FOR_USER"
        or state.get("next_stage") != "terra"
        or task.get("kind") != "validate"
        or not task.get("id")
        or state.get("settings", {}).get("milestone_checkpoints", {}).get("enabled") is not True
        or not contract
        or not contracts.approved(state)
        or any(
            state.get(key)
            for key in ("active_stage", "active_runner_check", "pending_report_repair", "uncertain_artifacts")
        )
    ):
        return None
    issued = human.retained(state)
    if not issued or issued["scope"] != "operational_exhaustion" or issued["request"].get("kind") != "blocker":
        return None
    identity = state["resolver"]["human_escalations"][issued["request_id"]]["identity"]
    bound = identity.get("binding") or {}
    count = state.get("no_progress_batches")
    limit = state.get("settings", {}).get("limits", {}).get("no_progress_batches")
    if (
        identity["proposal"].get("origin", {}).get("pause_status") != "PAUSED_NO_PROGRESS"
        or bound.get("current_task") != util.digest(task)
        or bound.get("settings") != util.digest(state.get("settings", {}))
        or bound.get("validation") != util.digest(state.get("validation", {}))
        or any(bound.get(key) != state.get(key) for key in ("workspace", "run_dir", "task_id", "conversation_id"))
        or any(
            (bound.get("contract") or {}).get(key) != contract.get(key)
            for key in ("hash", "revision", "approval_event")
        )
        or (task.get("contract_hash"), task.get("contract_revision")) != (contract["hash"], contract["revision"])
        or (bound.get("accounting") or {}).get("no_progress_batches") != state.get("no_progress_batches")
        or type(count) is not int
        or type(limit) is not int
        or not 0 < limit <= count
    ):
        return None
    proof, validation = state.get("regression_proof") or {}, state.get("validation") or {}
    assessment = proof.get("changed_code_evidence") or {}
    rows = state.get("regression_proofs") or []
    repair = rows[-1] if rows else {}
    revision = validation.get("source_revision")
    if (
        not revision
        or (bound.get("observed_source") or {}).get("revision") != revision
        or proof.get("source_revision") != revision
        or proof.get("verdict") != "PASS"
        or assessment.get("source_revision") != revision
        or assessment.get("status") != "UNVERIFIED"
        or repair.get("kind") != "changed_code_rework"
        or repair.get("action") != "builder"
        or repair.get("assessment") != assessment
        or repair.get("source_revision") != revision
        or repair.get("review_output") != validation.get("output")
        or repair.get("task_id") != task["id"]
        or any(repair.get("contract_" + key) != contract[key] for key in ("hash", "revision"))
        or validation.get("reviewer_role") != "sol"
        or validation.get("output") not in (validation.get("evidence_hashes") or {})
    ):
        return None
    accepted = any(
        row.get("output") == validation["output"]
        and (row.get("original_stage") or row.get("stage")) == "sol"
        and row.get("role") == "sol"
        and row.get("exit_code") == 0
        and not row.get("rejected")
        and not row.get("changed_files")
        and row.get("source_revision") == revision
        and row.get("task_id") == task["id"]
        and row.get("contract_hash") == contract["hash"]
        for row in state.get("stages") or []
    )
    return issued if accepted else None


def settle(state: RunState, revision, *, at):
    """Under the run lock, retire only this proven misroute and queue review."""
    issued = pending(state)
    if (
        not issued
        or any(human.pending_interruptions(state.get("run_dir")).values())
        or not validation_evidence.current(state, {"source_revision": revision})
    ):
        return False
    previous = state["regression_proofs"][-1]
    if not human.supersede_operational(state, REASON):
        return False
    event = deepcopy(previous)
    event.update(action="reviewer", at=at, scope_reason=REASON)
    event.pop("round", None)
    event.pop("round_limit", None)
    state["regression_proofs"].append(event)
    state.setdefault("user_events", []).append(
        {
            "kind": "validation_scope_recovery",
            "actor": "runner",
            "at": event["at"],
            "request_id": issued["request_id"],
            "review_output": previous["review_output"],
            "source_revision": revision,
            "reason": REASON,
        }
    )
    run_status.running(state, "EXECUTING", next_stage="astra_review", next_action=REASON)
    return True
