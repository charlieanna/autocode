"""Pure readiness checks for a technically verified flow awaiting human acceptance.

These checks classify a schema-validated report; callers still enforce current
contract/source evidence and the user's separate, artifact-bound review receipt.
"""


class IncompleteHumanProof(ValueError):
    """A technically passing report cannot prove that only human acceptance remains.

    Completion can offer the person's review only when the report's flow proves
    that every technical obligation passed and only the declared human approvals
    are outstanding. A report that leaves the human rows unverified without that
    proof is accepted at its own gate, then blocks completion and names a review
    control the run cannot issue yet (#1851).
    """


def require_human_pending_proof(criteria, validation, human_pending, review_missing):
    """Reject the repairable deadlock shape where a human gap lacks its proof.

    ``human_pending`` is the caller's ``human_only_pending_validation`` result and
    ``review_missing`` says the person's current receipt is still outstanding.
    Only reports that pass every technical row and fail solely on the human-only
    shape are rejected: the bounded report repair can cite already executed
    evidence and shape the flow, without rerunning a check or inventing approval.
    """
    human = {row["id"] for row in criteria if row["human_review"]}
    if human_pending or not human or not review_missing:
        return
    rows = validation.get("criterion_results") or []
    results = {row.get("id"): row for row in rows if isinstance(row, dict)}
    flow = validation.get("end_to_end_result") or {}
    if (
        validation.get("verdict") not in ("PASS", "BLOCKED")
        or validation.get("findings")
        or len(results) != len(rows)
        or flow.get("status") not in ("PASS", "NOT_VERIFIED")
        or any(
            results.get(c["id"], {}).get("status") != "PASS" or not results.get(c["id"], {}).get("evidence_refs")
            for c in criteria
            if not c["human_review"]
        )
        or any(
            results.get(c["id"], {}).get("status") not in (None, "PASS", "NOT_VERIFIED")
            for c in criteria
            if c["human_review"]
        )
        or not (
            flow.get("status") == "NOT_VERIFIED" or any(results.get(cid, {}).get("status") != "PASS" for cid in human)
        )
    ):
        return
    technical = flow.get("technical_result") or {}
    pending = {entry.split(":", 1)[0].split(" ", 1)[0] for entry in validation.get("unverified_criteria", [])}
    gaps = []
    if flow.get("status") != "NOT_VERIFIED":
        gaps.append("end_to_end_result must stay NOT_VERIFIED until the person records acceptance")
    if not str(flow.get("summary", "")).strip() or not flow.get("evidence_refs"):
        gaps.append("end_to_end_result must carry a summary and evidence references")
    if (
        technical.get("status") != "PASS"
        or not str(technical.get("summary", "")).strip()
        or not technical.get("evidence_refs")
    ):
        gaps.append("end_to_end_result.technical_result must be PASS with a summary and evidence references")
    if set(flow.get("pending_human_criteria") or []) != human:
        gaps.append(f"end_to_end_result.pending_human_criteria must list exactly {sorted(human)}")
    if pending and pending != human:
        gaps.append("unverified_criteria must name exactly the declared human criteria")
    for cid in sorted(human):
        row = results.get(cid)
        if row is None:
            gaps.append(f"criterion_results must report {cid}")
            continue
        if row.get("status") != "NOT_VERIFIED":
            gaps.append(f"{cid} must stay NOT_VERIFIED; a tool cannot record the person's acceptance")
        if not row.get("evidence_refs"):
            gaps.append(f"{cid} must cite the automated evidence already executed")
    raise IncompleteHumanProof(
        "Technically passing validation leaves "
        + ", ".join(sorted(human))
        + " awaiting human acceptance without the proof completion requires: "
        + "; ".join(dict.fromkeys(gaps))
        + f". Repair the report only: keep verdict {validation['verdict']}, cite commands already executed, "
        "never invent a person's approval and never rerun or change a recorded check."
    )


def human_only_pending_validation(criteria, validation, criterion):
    """Allow automated PASS rows only with explicit proof of the human-only flow gap."""
    human = {row["id"] for row in criteria if row["human_review"]}
    rows = validation.get("criterion_results", [])
    results = {row["id"]: row for row in rows}
    criteria_ids = {row["id"] for row in criteria}
    pending_ids = {entry.split(":", 1)[0].split(" ", 1)[0] for entry in validation.get("unverified_criteria", [])}
    flow = validation.get("end_to_end_result", {})
    if (
        criterion not in human
        or not human
        or len(criteria_ids) != len(criteria)
        or set(results) != criteria_ids
        or len(rows) != len(criteria)
        or validation.get("verdict") not in ("BLOCKED", "PASS")
        or validation.get("findings")
        or not flow_awaits_only(flow, human)
        or any(not row.get("evidence_refs") for row in rows)
        or any(row.get("status") != "PASS" for cid, row in results.items() if cid not in human)
        or any(results[cid].get("status") not in ("PASS", "NOT_VERIFIED") for cid in human)
    ):
        return False
    if flow.get("status") == "NOT_VERIFIED":
        # The Validator reports automated results; the structured flow proves
        # that only the exact declared human approvals remain. A human label or
        # passing criterion alone cannot replace that technical-flow proof.
        return not pending_ids or pending_ids == human
    # Retain older reports with a completed flow and explicit unverified human
    # rows. A PASS flow alone cannot establish that PASS rows await acceptance.
    return pending_ids == human and all(results[cid].get("status") == "NOT_VERIFIED" for cid in human)


def flow_awaits_only(flow, human):
    """Require technical-flow proof and an exact human set for an unfinished flow."""
    technical = flow.get("technical_result")
    pending = flow.get("pending_human_criteria") or []
    technical_ready = technical is None or (
        technical.get("status") == "PASS"
        and bool(technical.get("summary", "").strip())
        and bool(technical.get("evidence_refs"))
    )
    if flow.get("status") == "PASS":
        return technical_ready and not pending
    return (
        flow.get("status") == "NOT_VERIFIED"
        and bool(flow.get("evidence_refs"))
        and bool(flow.get("summary", "").strip())
        and technical is not None
        and technical_ready
        and bool(human)
        and len(pending) == len(human)
        and set(pending) == human
    )
