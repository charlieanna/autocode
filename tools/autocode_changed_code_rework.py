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
# The Tester's rejected reasons the Builder sees; each names the test its line still needs.
REJECTIONS_SHOWN = 20


def _contains(parent, path):
    parent = parent.rstrip("/")
    return bool(parent) and (path == parent or path.startswith(parent + "/"))


def _scope_conflict(task, contract, rejected):
    """Detect a batch member whose tests the narrowed assignment cannot repair.

    Compare approved ownership areas, not the product file directly with test-only
    task paths. This only requests review; no model citation grants write scope.
    Legacy tasks without those declarations retain their existing bounded route.
    """
    members, assigned = task.get("milestone_ids") or [], task.get("affected_paths") or []
    if len(members) < 2 or not assigned:
        return None
    for milestone in contract.get("body", {}).get("milestones", []):
        areas = milestone.get("affected_paths") or []
        if milestone["id"] not in members or any(
            _contains(area, path) or _contains(path, area) for area in areas for path in assigned
        ):
            continue
        for gap in rejected:
            if any(_contains(area, gap["path"]) for area in areas):
                return (
                    f"Rejected test gap {gap['path']}:{gap['line']} belongs to batch milestone "
                    f"{milestone['id']}, whose ownership areas are outside the current assignment. "
                    "Review and reassign the correction within the approved plan before retrying the Builder."
                )
    return None


def route(state: RunState, record, *, investigation_enabled, rejected=()):
    """Consume only a reconciled independent review of this exact measurement."""
    actual = record.get("original_stage") or record.get("stage")
    if (actual, record.get("role")) not in (("sol", "sol"), ("astra_checkpoint", "astra")):
        return False
    proof = state.get("regression_proof") or {}
    # Missing/failing ordinary proof remains owned by its existing failure
    # workflow. Its measurements did not run, so treating their absence as a
    # separate line-evidence defect misdiagnoses the failure and changes routing.
    if proof.get("verdict") != "PASS":
        return False
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
    scope_reason = _scope_conflict(task, contract, rejected)
    builder_rounds = sum(row.get("action") == "builder" for row in previous)
    event = {
        "kind": "changed_code_rework",
        **binding,
        "source_revision": record["source_revision"],
        "review_output": record["output"],
        "assessment": assessment,
        "at": util.now(),
        "action": "reviewer" if scope_reason else "builder" if builder_rounds < REPAIR_ROUNDS else "investigator",
    }
    if scope_reason:
        event["scope_reason"] = scope_reason
    else:
        # Which Builder round this is and how many the gate allows, so the status view can say
        # "round 1 of 2" from the record (the Investigator takes the round after the last one).
        event["round"], event["round_limit"] = builder_rounds + 1, REPAIR_ROUNDS
    history.append(event)
    if scope_reason:
        run_status.running(state, "EXECUTING", next_stage="astra_review", next_action=scope_reason)
        return True
    run_status.running(
        state,
        "EXECUTING",
        next_stage="terra",
        next_action=(
            reason + ". Add meaningful tests for these exact lines, or retain a precise reason "
            "for the independent Tester to check. Preserve the approved product scope and existing tests; "
            "do not install a coverage dependency or ask the person to resolve test machinery."
            + "".join(
                f"\nTester rejected a reason for {row['path']}:{row['line']}: {row['reason']}"
                for row in list(rejected)[:REJECTIONS_SHOWN]
            )
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
        and row.get("action") != "reviewer"
        and row.get("review_output") == record.get("output")
        and row.get("source_revision") == record.get("source_revision")
        for row in state.get("regression_proofs") or []
    )


# Lines the Builder has nothing to do for: a test already settles them, or the independent Tester
# accepted their reason. Their rows stay in the regression proof.
SETTLED = ("tested", "excepted")


def _measured(row):
    """An open line something observed: a mutant outcome, an evidence file or a recorded execution."""
    return bool(row.get("mutation_outcomes") or row.get("evidence") or row.get("executed"))


def builder_evidence(assessment):
    """The changed-line evidence a Builder acts on: each observed open line whole, the rest by number.

    A live Builder round got all 229 rows of its proof (205 KB, four fifths of its handoff) to repair
    28 lines, and the provider compacted its conversation (#2815). An open row that carries an
    observation keeps every field. Each tested or excepted line is listed under its file in
    ``settled_lines``, and each open line nothing observed (a proof that did not pass ran no mutant,
    or the line has no mutation site) under its file in ``unmeasured_lines``.
    """
    rows = assessment.get("obligations")
    if not isinstance(rows, list):
        return assessment
    view = {key: value for key, value in assessment.items() if key != "obligations"}
    view["obligations"], settled, unmeasured = [], {}, {}
    for row in rows:
        if not isinstance(row, dict):
            view["obligations"].append(row)
        elif row.get("status") in SETTLED:
            settled.setdefault(row["status"], {}).setdefault(row.get("path"), []).append(row.get("line"))
        elif _measured(row):
            view["obligations"].append(row)
        else:
            unmeasured.setdefault(row.get("path"), []).append(row.get("line"))
    if settled:
        view["settled_lines"] = settled
    if unmeasured:
        view["unmeasured_lines"] = unmeasured
    return view


def owns_builder_round(state: RunState, revision):
    """True when the next Builder round is this module's own changed-line repair of ``revision``.

    The latest proof record is a Builder ``changed_code_rework`` of the current task and contract,
    queued from an independent review of exactly this source, whose regression proof passed. A
    recovery packet that the current task came from does not own that round: its incident was
    fixed, and REPAIR_ROUNDS bounds this one (#2815).
    """
    rows = state.get("regression_proofs") or []
    last = rows[-1] if rows else {}
    task, contract = state.get("current_task") or {}, state.get("goal_contract") or {}
    proof = state.get("regression_proof") or {}
    return (
        isinstance(last, dict)
        and last.get("kind") == "changed_code_rework"
        and last.get("action") == "builder"
        and bool(revision)
        and last.get("source_revision") == revision == proof.get("source_revision")
        and proof.get("verdict") == "PASS"
        and (last.get("task_id"), last.get("contract_hash"), last.get("contract_revision"))
        == (task.get("id"), contract.get("hash"), contract.get("revision"))
    )
