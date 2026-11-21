"""Admission and routing of an independent changed-line evidence review.

Preflight rejects incomplete reports before clean check replay. Admission then
pins files through the existing evidence boundary; the domain binder never
grants authority from an unverified model citation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_acceptance_test_repair as acceptance_test_repair
    from . import autocode_acceptance_tests as acceptance_tests
    from . import autocode_changed_code_proof as proof_policy
    from . import autocode_changed_code_review as review
    from . import autocode_changed_code_rework as rework
    from . import autocode_claim_evidence as claim_evidence
    from . import autocode_report_repair_queue as report_repair_queue
    from . import autocode_util as util
except ImportError:
    import autocode_acceptance_test_repair as acceptance_test_repair
    import autocode_acceptance_tests as acceptance_tests
    import autocode_changed_code_proof as proof_policy
    import autocode_changed_code_review as review
    import autocode_changed_code_rework as rework
    import autocode_claim_evidence as claim_evidence
    import autocode_report_repair_queue as report_repair_queue
    import autocode_util as util


def _spellings(ref, workspace):
    """A pinned file's exact path, and its workspace-relative form when it lies in the workspace.

    The Tester cites the other changed-line inputs by workspace-relative path, so it also cites the
    proof file that way (live: ``.autocode/runs/<run>/regression/proof-07-<id>/verification.json``).
    Both spellings name the one file pinned here; nothing outside these exact refs is admitted.
    """
    spellings = [ref]
    if workspace is not None and Path(ref).is_absolute():
        try:
            relative = Path(ref).relative_to(Path(workspace)).as_posix()
        except ValueError:
            relative = None
        if relative and all(part not in ("", ".", "..") for part in Path(relative).parts):
            spellings.append(relative)
    return spellings


def preflight(state: RunState, validation, record, *, passing_claim):
    """Require executed checks and offered decisions before spending clean replay.

    This establishes no acceptance: complete reports still need fresh executions
    and the post-replay evidence binding in ``accept``.
    """
    report_repair_queue.require_executed_passes(validation, passing_claim=passing_claim)
    if record.get("role") != "builder":
        actual = str(
            record.get("original_stage") or str(record.get("stage") or "tester").removesuffix("_report_repair")
        )
        acceptance_test_repair.validate_report(
            validation,
            stage=actual if actual in acceptance_test_repair.REVIEW_STAGES else "tester",
            covered_case_ids=acceptance_tests.covered(state),
            survivor_case_ids=acceptance_test_repair.survivor_case_ids(state),
        )
    problems = []
    for check in (require_exact_lines, require_tester_decisions):
        try:
            check(state, validation, record)
        except ValueError as error:
            problems.append(str(error))
    verified, _ = _references(state, validation, record, state.get("workspace"))
    unknown = sorted(_cited(validation) - verified.keys())
    if unknown:
        citation = review.proof_reference(state.get("regression_proof") or {}, record.get("source_revision"))
        choice = f" Cite {citation['current_proof_reference']} for the current enclosing proof." if citation else ""
        problems.append(f"Changed-code reasons cite unauthenticated evidence: {json.dumps(unknown)}.{choice}")
    if record.get("role") != "builder":
        # A Tester claim its cited observation cannot establish (#3947): the judgment `autocode report-check` gives.
        problems += claim_evidence.tester_problems(state, validation, record)
    if problems:
        raise ValueError("\n\n".join(problems))


def accept(state: RunState, validation, record, workspace=None, *, pin):
    """Bind explicitly offered decisions after checks have their actual receipts."""
    require_tester_decisions(state, validation, record)
    decisions = validation.get(review.FIELD)
    if not decisions:
        return
    proof = state.get("regression_proof") or {}
    verified, pins = _references(state, validation, record, workspace, pin=pin)
    validation["evidence_hashes"].update(pins)
    validation["changed_code_decisions"] = review.bind(
        proof.get("changed_code_evidence"),
        decisions,
        record,
        offered_schema=util.read(record["schema"]),
        verified_refs=verified,
    )


def _cited(validation):
    decisions = validation.get(review.FIELD)
    return (
        {
            ref
            for row in decisions
            if isinstance(row, dict) and isinstance(row.get("evidence_refs"), list)
            for ref in row["evidence_refs"]
            if isinstance(ref, str)
        }
        if isinstance(decisions, list)
        else set()
    )


def _references(state: RunState, validation, record, workspace, *, pin=None):
    """One citation inventory for diagnostic preflight and pinned final admission.

    Preflight recognizes names only; only final admission pins the actual files.
    Mutation observations still authenticate their producing proof and receipts.
    """
    proof = state.get("regression_proof") or {}
    pins = {}
    checks = validation["checks"]
    verified = {check["evidence_ref"]: check["evidence_ref"] for check in checks}
    verified.update({f"check:{number}": check["evidence_ref"] for number, check in enumerate(checks, 1)})
    refs = [proof.get("path"), record.get("output")]
    refs += [row["path"] for row in (proof.get("changed_code_inputs") or {}).get("inventory") or []]
    refs += list(proof.get("test_files") or [])
    for ref in refs:
        if ref:
            if pin is not None:
                pins.update(pin([ref]))
            for spelling in _spellings(ref, workspace):
                verified[spelling] = ref
    citation = review.proof_reference(proof, record.get("source_revision"))
    if citation:
        verified[citation["current_proof_reference"]] = citation["proof_reference"]
    cited = _cited(validation) - verified.keys()
    if cited:
        if workspace is not None:
            cited |= {str(Path(workspace) / ref) for ref in cited if not Path(ref).is_absolute()}
        mutation_pins = proof_policy.cited_mutation_pins(proof, cited, source_revision=record.get("source_revision"))
        pins.update(mutation_pins)
        for ref in mutation_pins:
            for spelling in _spellings(ref, workspace):
                verified[spelling] = ref
    return verified, pins


def require_exact_lines(state: RunState, value, record):
    """Return a report whose decision quotes another line than its handle binds (#2977).

    Checked before clean replay, and again when the decisions are bound; a repaired report is held
    to it too, since the repair is how the Tester corrects the drift.
    """
    assessment = (state.get("regression_proof") or {}).get("changed_code_evidence") or {}
    if assessment.get("source_revision") != record.get("source_revision"):
        return
    mismatches = review.misquoted(assessment, value.get(review.FIELD))
    if mismatches:
        raise ValueError(review.misquote_message(mismatches))


# Listed in the error the Tester's report repair receives; the rest are in the offered gap_id enum.
LISTED_GAPS = 40


def _runner_gap(row) -> bool:
    """A line whose every mutant went unrun: no mutation result exists for a Tester to judge."""
    outcomes = row.get("mutation_outcomes")
    return bool(outcomes) and isinstance(outcomes, list) and all(outcome == "not_run" for outcome in outcomes)


def require_tester_decisions(state: RunState, value, record):
    """Request omitted decisions before another expensive replay or repair.

    A Builder round cannot settle a gap with no mutation outcome (autocode_changed_code_review
    .tester_only_gaps), so the bounded report repair asks the Tester instead. An explicit
    missing-test FAIL is asked once to decide the rest of the offered matrix, so its independent
    writer receives one complete review instead of successive partial reviews; a line whose
    mutants never ran (``not_run``: the runner's budget ended, no result exists) is the runner's
    gap, not one the Tester must explain. A repaired report, PASS or FAIL, keeps the existing
    Builder/Investigator fallback (``route``) for what it leaves undecided: the repair channel's
    own caps (``edits.maxItems``, ``value_json.maxLength``) cannot carry hundreds of decisions, so
    demanding them again paused the run with no way forward (#3736).
    """
    stage = str(record.get("stage", ""))
    # A repaired report is applied with its original stage's record, marked report_repaired.
    repaired = stage.endswith("_report_repair") or record.get("report_repaired") is True
    decisions = value.get(review.FIELD)
    missing_tests = (
        value.get("verdict") == "FAIL"
        and isinstance(decisions, list)
        and any(
            isinstance(row, dict) and row.get("decision") == "reject" and row.get("reason_kind") == "missing_test"
            for row in decisions
        )
    )
    if (value.get("verdict") != "PASS" and not missing_tests) or repaired or not record.get("schema"):
        return
    assessment = (state.get("regression_proof") or {}).get("changed_code_evidence") or {}
    if assessment.get("source_revision") != record.get("source_revision"):
        return
    gaps = (review.undecided_gaps if missing_tests else review.tester_only_gaps)(
        assessment,
        decisions if isinstance(decisions, list) else [],
        util.read(record["schema"]),
        str(record.get("original_stage") or stage).removesuffix("_report_repair"),
    )
    if missing_tests:
        gaps = [row for row in gaps if not _runner_gap(row)]
    if not gaps:
        return
    # Each line's text is listed too: the repair handoff carries this error, not the proof's rows (#2977).
    listed = ", ".join(
        f"{row['handle'] + ' ' if row.get('handle') else ''}{row['path']}:{row['line']} ({row['gap_id']})"
        + (f" {json.dumps(row['source_line'])}" if isinstance(row.get("source_line"), str) else "")
        for row in gaps[:LISTED_GAPS]
    )
    more = f", and {len(gaps) - LISTED_GAPS} more in the gap_id enum" if len(gaps) > LISTED_GAPS else ""
    problem = (
        "The missing-test FAIL leaves other offered changed lines undecided"
        if missing_tests
        else "The PASS leaves changed lines that no added test can mark tested, because they have no mutation outcome"
    )
    raise ValueError(
        f"{problem}: {listed}{more}. Give one changed_code_exceptions decision for each, with its "
        "handle (such as L12) as gap_id and the quoted text of that line as source_line: accept "
        "with the concrete reason the tests cover the line, or reject with the test it still needs. "
        f"The lines and their tests are in {(state.get('regression_proof') or {}).get('path')}."
    )


def route(state: RunState, validation, record, *, investigation_enabled):
    """Keep unresolved obligations inside Builder/Investigator repair."""
    if acceptance_test_repair.route_from_review(
        state, validation, record, covered_case_ids=acceptance_tests.covered(state)
    ):
        return True
    proof = state.get("regression_proof") or {}
    if not proof.get("changed_code_evidence"):
        return False
    decisions = validation.get("changed_code_decisions") or []
    proof["changed_code_evidence"] = proof_policy.reviewed(proof, decisions)
    rejected = [row for row in decisions if not row.get("accepted")]
    return rework.route(state, record, investigation_enabled=investigation_enabled, rejected=rejected)
