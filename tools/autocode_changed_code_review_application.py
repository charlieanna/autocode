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


def route(state: RunState, validation, record, *, investigation_enabled):
    """Keep unresolved obligations inside Builder/Investigator repair."""
    proof = state.get("regression_proof") or {}
    if not proof.get("changed_code_evidence"):
        return False
    proof["changed_code_evidence"] = proof_policy.reviewed(proof, validation.get("changed_code_decisions") or [])
    return rework.route(state, record, investigation_enabled=investigation_enabled)
