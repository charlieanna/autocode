"""Admission and routing of an independent changed-line evidence review.

The controller calls this after ordinary review/check/source admission. Files
are pinned through its existing evidence boundary; the domain binder never
grants authority from an unverified model citation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_changed_code_proof as proof_policy
    from . import autocode_changed_code_review as review
    from . import autocode_changed_code_rework as rework
    from . import autocode_util as util
except ImportError:
    import autocode_changed_code_proof as proof_policy
    import autocode_changed_code_review as review
    import autocode_changed_code_rework as rework
    import autocode_util as util


def accept(state: RunState, validation, record, *, pin):
    """Bind explicitly offered decisions after checks have their actual receipts."""
    require_tester_decisions(state, validation, record)
    decisions = validation.get(review.FIELD)
    if not decisions:
        return
    proof = state.get("regression_proof") or {}
    checks = validation["checks"]
    verified = {check["evidence_ref"]: check["evidence_ref"] for check in checks}
    verified.update({f"check:{number}": check["evidence_ref"] for number, check in enumerate(checks, 1)})
    refs = [proof.get("path"), record.get("output")]
    refs += [row["path"] for row in (proof.get("changed_code_inputs") or {}).get("inventory") or []]
    refs += list(proof.get("test_files") or [])
    for ref in refs:
        if ref:
            validation["evidence_hashes"].update(pin([ref]))
            verified[ref] = ref
    validation["changed_code_decisions"] = review.bind(
        proof.get("changed_code_evidence"),
        decisions,
        record,
        offered_schema=util.read(record["schema"]),
        verified_refs=verified,
    )


# Listed in the error the Tester's report repair receives; the rest are in the offered gap_id enum.
LISTED_GAPS = 40


def require_tester_decisions(state: RunState, value, record):
    """Return a PASS that leaves Tester-only gaps undecided for that decision.

    A Builder round cannot settle a gap with no mutation outcome (autocode_changed_code_review
    .tester_only_gaps), so the bounded report repair asks the Tester instead. The repair itself
    is never refused for this: an undecided gap left after it keeps the existing Builder and
    Investigator rounds.
    """
    stage = str(record.get("stage", ""))
    # A repaired report is applied with its original stage's record, marked report_repaired.
    repaired = stage.endswith("_report_repair") or record.get("report_repaired") is True
    if value.get("verdict") != "PASS" or repaired or not record.get("schema"):
        return
    assessment = (state.get("regression_proof") or {}).get("changed_code_evidence") or {}
    if assessment.get("source_revision") != record.get("source_revision"):
        return
    decisions = value.get(review.FIELD)
    gaps = review.tester_only_gaps(
        assessment,
        decisions if isinstance(decisions, list) else [],
        util.read(record["schema"]),
        str(record.get("original_stage") or stage).removesuffix("_report_repair"),
    )
    if not gaps:
        return
    listed = ", ".join(
        f"{row['handle'] + ' ' if row.get('handle') else ''}{row['path']}:{row['line']} ({row['gap_id']})"
        for row in gaps[:LISTED_GAPS]
    )
    more = f", and {len(gaps) - LISTED_GAPS} more in the gap_id enum" if len(gaps) > LISTED_GAPS else ""
    raise ValueError(
        "The PASS leaves changed lines that no added test can mark tested, because they have no "
        f"mutation outcome: {listed}{more}. Give one changed_code_exceptions decision for each, with its "
        "handle (such as L12) as gap_id: accept "
        "with the concrete reason the tests cover the line, or reject with the test it still needs. "
        f"The lines and their tests are in {(state.get('regression_proof') or {}).get('path')}."
    )


def route(state: RunState, validation, record, *, investigation_enabled):
    """Keep unresolved obligations inside Builder/Investigator repair."""
    proof = state.get("regression_proof") or {}
    if not proof.get("changed_code_evidence"):
        return False
    decisions = validation.get("changed_code_decisions") or []
    proof["changed_code_evidence"] = proof_policy.reviewed(proof, decisions)
    rejected = [row for row in decisions if not row.get("accepted")]
    return rework.route(state, record, investigation_enabled=investigation_enabled, rejected=rejected)
