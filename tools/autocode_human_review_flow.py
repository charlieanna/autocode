"""Pure readiness checks for a technically verified flow awaiting human acceptance.

These checks classify a schema-validated report; callers still enforce current
contract/source evidence and the user's separate, artifact-bound review receipt.
"""


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
