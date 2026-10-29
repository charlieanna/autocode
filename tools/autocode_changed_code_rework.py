"""Return exact changed-line obligations to the existing bounded repair path.

Two direct Builder repairs precede the existing failure Investigator. Decisions
are retained in regression_proofs beside their source and accepted review; no
new retry allowance, route, approval or person question is manufactured here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_builder_failure as builder_failure
    from . import autocode_run_status as run_status
    from . import autocode_util as util
except ImportError:
    import autocode_builder_failure as builder_failure
    import autocode_run_status as run_status
    import autocode_util as util

REPAIR_ROUNDS = 2


def route(state: RunState, record, *, investigation_enabled):
    """Consume only a reconciled independent review of this exact measurement."""
    actual = record.get("original_stage") or record.get("stage")
    if (actual, record.get("role")) not in (("sol", "sol"), ("astra_checkpoint", "astra")):
        return False
    proof = state.get("regression_proof") or {}
    assessment = proof.get("changed_code_evidence") or {}
    if not assessment or assessment.get("status") == "PASS":
        return False
    if assessment.get("source_revision") != record.get("source_revision"):
        raise ValueError("Changed-line repair must refer to the independent review's exact source")
    history = state.setdefault("regression_proofs", [])
    task = state.get("current_task") or {}
    contract = state.get("goal_contract") or {}
    binding = {
        "task_id": task.get("id"),
        "contract_hash": contract.get("hash"),
        "contract_revision": contract.get("revision"),
    }
    previous = [
        row
        for row in history
        if row.get("kind") == "changed_code_rework" and all(row.get(k) == v for k, v in binding.items())
    ]
    if any(row.get("review_output") == record.get("output") for row in previous):
        return True  # Reconciliation must never charge or queue this accepted report twice.
    reason = assessment["reason"]
    event = {
        "kind": "changed_code_rework",
        **binding,
        "source_revision": record["source_revision"],
        "review_output": record["output"],
        "assessment": assessment,
        "at": util.now(),
        "action": "builder" if len(previous) < REPAIR_ROUNDS else "investigator",
    }
    history.append(event)
    run_status.running(
        state,
        "EXECUTING",
        next_stage="terra",
        next_action=(
            reason + ". Add meaningful tests for these exact lines, or retain a precise reason "
            "for the independent Tester to check. Preserve the approved product scope and existing tests; "
            "do not install a coverage dependency or ask the person to resolve test machinery."
        ),
    )
    if event["action"] == "builder":
        state["iteration"] += 1
    else:
        evidence = builder_failure.evidence(state, record, diagnosis={"changed_code_evidence": assessment})
        builder_failure.queue(
            state,
            evidence,
            reason,
            enabled=investigation_enabled,
            completed_record=record,
        )
    return True


def diagnosed(state: RunState, evidence, diagnosis, investigator_record, action):
    """Carry a verified diagnosis into the exact Builder repair it classifies."""
    if action not in ("retry", "escalate"):
        return
    for row in reversed(state.get("regression_proofs") or []):
        if row.get("kind") != "changed_code_rework" or row.get("action") != "investigator":
            continue
        if row.get("review_output") != evidence["record"].get("output") or row.get(
            "source_revision"
        ) != investigator_record.get("source_revision"):
            continue
        row["investigation"] = {
            "diagnosis": diagnosis["diagnosis"],
            "guidance": diagnosis["guidance"],
            "evidence_refs": diagnosis["evidence_refs"],
            "output": investigator_record["output"],
            "sha256": util.file_hash(investigator_record["output"]),
        }
        state["next_action"] += "\nInvestigator: " + diagnosis["guidance"]
        state["iteration"] += 1
        return


def guidance(state: RunState, current_revision):
    """Display only a still-bound, pinned independent investigation."""
    task, contract = state.get("current_task") or {}, state.get("goal_contract") or {}
    source = (state.get("regression_proof") or {}).get("source_revision")
    if source != current_revision:
        return None
    for row in reversed(state.get("regression_proofs") or []):
        if row.get("kind") != "changed_code_rework" or not row.get("investigation"):
            continue
        if (row.get("task_id"), row.get("contract_hash"), row.get("contract_revision"), row.get("source_revision")) != (
            task.get("id"),
            contract.get("hash"),
            contract.get("revision"),
            source,
        ):
            continue
        saved = row["investigation"]
        try:
            if util.file_hash(saved["output"]) == saved["sha256"]:
                return saved
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return None


def queued(state: RunState, record):
    """A nested checkpoint cannot replace the repair that its own Tester queued."""
    return any(
        row.get("kind") == "changed_code_rework"
        and row.get("review_output") == record.get("output")
        and row.get("source_revision") == record.get("source_revision")
        for row in state.get("regression_proofs") or []
    )
