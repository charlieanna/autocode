"""Return exact changed-line obligations to the existing bounded repair path.

Two direct Builder repairs precede the existing failure Investigator. Decisions
are retained in regression_proofs beside their source and accepted review; no
new retry allowance, route, approval or person question is manufactured here.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_builder_failure as builder_failure
    from . import autocode_changed_code_review as review
    from . import autocode_mutation_check as mutation_check
    from . import autocode_run_status as run_status
    from . import autocode_util as util
except ImportError:
    import autocode_builder_failure as builder_failure
    import autocode_changed_code_review as review
    import autocode_mutation_check as mutation_check
    import autocode_run_status as run_status
    import autocode_util as util

REPAIR_ROUNDS = 2
# The proof's runner produces no mutation evidence at all (autocode_mutation_check.record ``unavailable``).
# A Builder change cannot close such a gap, so its rounds are not spent on it (#3817). An assessment
# that lists its open lines is judged line by line instead (_tester_only).
UNCLOSABLE = (mutation_check.NO_SOURCE, mutation_check.NO_SELECTION)
# The Tester's rejected reasons the Builder sees; each names the test its line still needs.
REJECTIONS_SHOWN = 20


def _contains(parent, path):
    parent = parent.rstrip("/")
    return bool(parent) and (path == parent or path.startswith(parent + "/"))


def _scope_conflict(task, contract, rejected):
    """Detect an assignment that cannot authorize the required test repairs.

    Compare approved ownership areas, not the product file directly with test-only
    task paths. This only requests review; no model citation grants write scope.
    Legacy tasks without those declarations retain their existing bounded route.
    """
    if task.get("kind") == "validate":
        return (
            "The current task is validation-only, so the Builder cannot repair its unresolved changed-line evidence. "
            "Review the evidence and assign a correction within the approved plan before dispatching the Builder; "
            "retain the unverified lines and all outstanding milestone obligations."
        )
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


# Open lines a builder_skipped or Builder note names; the rest are in the assessment's obligations.
LISTED_LINES = 20


def _listed(rows):
    names = ", ".join(f"{row.get('path')}:{row.get('line')}" for row in rows[:LISTED_LINES])
    return names + (f", and {len(rows) - LISTED_LINES} more" if len(rows) > LISTED_LINES else "")


def _no_site(rows, mutation_report):
    why = mutation_report.get("note") if mutation_report.get("unavailable") in UNCLOSABLE else None
    return (
        str(why)
        if why
        else "the mutation check made no mutant for them (no mutation site), so only the Tester's reasons settle "
        + _listed(rows)
    )


def _unclosable(gaps, tester_only, mutation_report, rejected):
    """True when no Builder change can settle any open line, so its rounds go unspent (#3817).

    A Tester rejection names the test a line still needs, which the Builder can write. Otherwise each
    open line is judged by its own evidence (autocode_changed_code_review.tester_only): a line with no
    mutation outcome, because its runner has no mutation source or the line has no mutation site, is
    settled only by a reason. An assessment that lists no open line falls back to the runner's record.
    """
    if any(isinstance(row, dict) for row in rejected or ()):
        return False
    if gaps:
        return len(tester_only) == len(gaps)
    return mutation_report.get("unavailable") in UNCLOSABLE


def route(state: RunState, record, *, investigation_enabled, rejected=()):
    """Consume only a reconciled independent review of this exact measurement."""
    actual = record.get("original_stage") or record.get("stage")
    if (actual, record.get("role")) not in (("tester", "tester"), ("review_checkpoint", "task_planner")):
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
    mutation_report = proof.get("mutation_check") or {}
    gaps = review.open_gaps(assessment)
    tester_only = [row for row in gaps if review.tester_only(row)]
    unclosable = _unclosable(gaps, tester_only, mutation_report, rejected)
    event = {
        "kind": "changed_code_rework",
        **binding,
        "source_revision": record["source_revision"],
        "review_output": record["output"],
        "assessment": assessment,
        "at": util.now(),
        "action": "reviewer"
        if scope_reason
        else "builder"
        if builder_rounds < REPAIR_ROUNDS and not unclosable
        else "investigator",
    }
    if scope_reason:
        event["scope_reason"] = scope_reason
    elif unclosable:
        event["builder_skipped"] = "No Builder change can give these lines mutation evidence: " + (
            str(mutation_report.get("note") or mutation_report["unavailable"])
            if not gaps
            else _no_site(tester_only, mutation_report)
        )
    else:
        # Which Builder round this is and how many the gate allows, so the status view can say
        # "round 1 of 2" from the record (the Investigator takes the round after the last one).
        event["round"], event["round_limit"] = builder_rounds + 1, REPAIR_ROUNDS
    history.append(event)
    if scope_reason:
        run_status.running(state, "EXECUTING", next_stage="review_completion", next_action=scope_reason)
        return True
    assigned = [path for path in (task.get("affected_paths") or []) if isinstance(path, str) and path]
    scope_note = (
        f" Keep every edit within current_task.affected_paths ({', '.join(assigned)}); "
        "even if a rejected reason names another file, add or update tests only inside those assigned paths."
        if assigned
        else ""
    )
    if unclosable:
        reason += ". " + event["builder_skipped"]
    elif event["action"] == "builder" and tester_only:
        # The Builder's work is the lines a test can settle; the others wait for the Tester's reasons.
        reason += (
            f". {len(tester_only)} of the {len(gaps)} open lines have no mutation outcome, so no test you add "
            "can mark them tested; leave them to the Tester's reasons: " + _listed(tester_only)
        )
    run_status.running(
        state,
        "EXECUTING",
        next_stage="builder",
        next_action=(
            reason + ". Add meaningful tests for these exact lines, or retain a precise reason "
            "for the independent Tester to check. Preserve the approved product scope and existing tests; "
            "do not install a coverage dependency or ask the person to resolve test machinery."
            + scope_note
            + "".join(
                f"\nTester rejected a reason for {row['path']}:{row['line']}: {row['reason']}"
                for row in list(rejected)[:REJECTIONS_SHOWN]
            )
        ),
    )
    if event["action"] == "builder":
        state["iteration"] += 1
    elif any(row.get("investigation") for row in previous) and builder_failure.policy.execution_exhausted(
        state, record.get("output"), changed_code_evidence=assessment
    ):
        builder_failure.policy.failure(state, record.get("output"), reason, classification="execution")
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


def reconcile_classified_hold(state: RunState, workspace, *, route, prior_next_action: str | None = None) -> bool:
    """Reconcile a saved PAUSED_BUILDER_CLASSIFICATION hold whose changed-code Investigator already returned retry."""
    held = state.get("builder_failure_hold")
    if (
        not isinstance(held, dict)
        or held.get("status") != "PAUSED_BUILDER_CLASSIFICATION"
        or held.get("classification_retry")
        or state.get("next_stage") != "builder"
        or any(
            state.get(key)
            for key in (
                "active_stage",
                "uncertain_artifacts",
                "pending_report_repair",
                "pending_builder_failure",
                "stuck_investigation",
            )
        )
    ):
        return False
    failure_id, bound = held.get("failure_id"), held.get("binding")
    if not isinstance(failure_id, str) or not failure_id or not isinstance(bound, dict):
        return False
    entry = next(
        (
            row
            for row in reversed(state.get("stuck_investigations") or [])
            if isinstance(row, dict) and row.get("identity") == "builder-failure:" + failure_id
        ),
        None,
    )
    output_path = entry.get("output") if isinstance(entry, dict) else None
    if (
        entry is None
        or entry.get("outcome") != "classified"
        or entry.get("failure_class") != "unknown"
        or entry.get("cause") == "needs_user"
        or not isinstance(entry.get("guidance"), str)
        or not entry["guidance"].strip()
        or not isinstance(output_path, str)
        or not Path(output_path).is_file()
    ):
        return False
    try:
        saved_report = json.loads(Path(output_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    refs = saved_report.get("evidence_refs") if isinstance(saved_report, dict) else None
    allowed_hashes = bound.get("evidence_hashes")
    if (
        not isinstance(saved_report, dict)
        or saved_report.get("recommendation") != "retry"
        or saved_report.get("failure_id") != failure_id
        or not isinstance(refs, list)
        or not refs
        or not isinstance(allowed_hashes, dict)
        or not set(refs) <= set(allowed_hashes)
    ):
        return False
    rework_row = next(
        (
            row
            for row in reversed(state.get("regression_proofs") or [])
            if isinstance(row, dict)
            and row.get("kind") == "changed_code_rework"
            and row.get("action") == "investigator"
            and not row.get("investigation")
            and row.get("review_output") == bound.get("output")
            and row.get("source_revision") == bound.get("source_revision")
        ),
        None,
    )
    if rework_row is None:
        return False
    review_record = next(
        (
            row
            for row in reversed(state.get("stages") or [])
            if isinstance(row, dict) and row.get("output") == bound.get("output")
        ),
        {
            "output": bound.get("output"),
            "task_id": bound.get("task_id"),
            "source_revision": bound.get("source_revision"),
        },
    )
    try:
        builder_failure.guard(state, {"failure_evidence": {"binding": bound, "record": review_record}}, workspace)
    except util.Paused:
        return False
    effective_class = "operational" if entry.get("cause") == "environment" else "execution"
    entry["failure_class"] = effective_class
    state.pop("builder_failure_hold", None)
    ev = builder_failure.evidence(state, review_record)
    ev["failure_id"] = failure_id
    ev["binding"] = copy.deepcopy(bound)
    ev["evidence_refs"] = list(allowed_hashes)
    ev["changed_code_evidence"] = copy.deepcopy(rework_row.get("assessment"))
    ev["diagnosis"] = {
        **{
            key: saved_report[key]
            for key in (
                "failure_id",
                "evidence_refs",
                "diagnosis",
                "guidance",
                "example",
                "recommendation",
                "cause",
                "user_question",
            )
            if key in saved_report
        },
        "failure_class": effective_class,
    }
    reason = str((rework_row.get("assessment") or {}).get("reason") or entry.get("reason") or "")
    if not state.get("next_action"):
        state["next_action"] = prior_next_action or reason
    action = route(state, ev, reason)
    if state.get("status") != "RUNNING":
        run_status.running(
            state,
            "EXECUTING",
            next_stage="builder",
            next_action=state.get("next_action") or prior_next_action or reason,
        )
    diagnosed(state, ev, ev["diagnosis"], {"output": output_path, "source_revision": bound["source_revision"]}, action)
    return True


def reconcile_false_changed_code_stop(state: RunState, workspace) -> bool:
    """Retire a pre-fix runner could_not_finish stop recorded after changed_code_rework progress."""
    if state.get("status") != "PAUSED_INTERVENTION" or state.get("next_stage") != "builder":
        return False
    if any(
        state.get(key)
        for key in (
            "active_stage",
            "uncertain_artifacts",
            "pending_report_repair",
            "pending_builder_failure",
            "stuck_investigation",
        )
    ):
        return False
    proof = state.get("regression_proof")
    if not isinstance(proof, dict) or proof.get("verdict") != "PASS":
        return False
    try:
        current_rev = builder_failure.source_scope.snapshot(workspace, state)["revision"]
    except Exception:
        return False
    if not current_rev or proof.get("source_revision") != current_rev:
        return False
    rework_row = next(
        (
            row
            for row in reversed(state.get("regression_proofs") or ())
            if isinstance(row, dict)
            and row.get("kind") == "changed_code_rework"
            and row.get("action") == "investigator"
            and not row.get("investigation")
            and row.get("source_revision") == current_rev
        ),
        None,
    )
    if not isinstance(rework_row, dict):
        return False
    failure_output = rework_row.get("review_output")
    if not isinstance(failure_output, str) or not failure_output:
        return False
    milestone_key = builder_failure.policy.key(state)
    inv_entry = next(
        (
            row
            for row in reversed(state.get("stuck_investigations") or ())
            if isinstance(row, dict)
            and row.get("trigger") == "builder_failure"
            and row.get("outcome") == "classified"
            and row.get("failure_class") == "execution"
            and row.get("milestone_key") == milestone_key
        ),
        None,
    )
    if not isinstance(inv_entry, dict):
        return False
    inv_output = inv_entry.get("output")
    if not isinstance(inv_output, str) or not Path(inv_output).is_file():
        return False
    try:
        saved_inv = json.loads(Path(inv_output).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    if (
        not isinstance(saved_inv, dict)
        or saved_inv.get("recommendation") != "retry"
        or saved_inv.get("cause") == "needs_user"
        or not isinstance(saved_inv.get("guidance"), str)
        or not saved_inv["guidance"].strip()
        or not isinstance(saved_inv.get("diagnosis"), str)
        or not isinstance(saved_inv.get("evidence_refs"), list)
    ):
        return False
    if not builder_failure.policy.reconcile_false_progress_stop(state, failure_output):
        return False
    reason = str((rework_row.get("assessment") or {}).get("reason") or inv_entry.get("reason") or "")
    if not state.get("next_action"):
        state["next_action"] = reason
    diagnosed(
        state,
        {"record": {"output": failure_output}},
        saved_inv,
        {"output": inv_output, "source_revision": current_rev},
        "retry",
    )
    state.pop("paused_at", None)
    run_status.running(state, "EXECUTING", next_stage="builder", next_action=state.get("next_action") or reason)
    return True
