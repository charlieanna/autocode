"""Stop validation-only rounds that keep leaving the same blocking findings open (#300).

The Completion Owner can send unchanged work back to the Validator, as a validate task or as
VALIDATE. Only the reviewer that raised a finding closes it, through a fresh, independently
evidenced report (autocode_findings); a rejected or repaired report cannot. A live run kept
dispatching such rounds for one Validator-owned finding whose closing reports were never
accepted. When ``LIMIT`` rounds in a row changed nothing (the contract, source, open blocking
findings, passing criteria and accepted milestones are all as they were), the runner launches
no further Validator for them and asks the user one question naming the findings and why each
remains open. Nothing here closes, merges or retracts a finding, and the answer authorizes no
retry.

A round is validation-only when the source is the one the latest accepted validation already
reviewed. Another attempt at the same round (a recovered timeout, an explicit fresh retry) is
not a new round.

Imports nothing from the runner: the caller (autopilot.admit_validation, before every Validator
dispatch) passes the open blocking ledger rows and the current source revision, and publishes the
returned request through the existing AutoResolver operational request. State key written:
``validation_only_rounds`` (one entry per admitted round, written only by ``admit``; read only
here).
"""
from __future__ import annotations

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util

KEY = "validation_only_rounds"
LIMIT = 2
STATUS = "PAUSED_RESOLVER"
OWNERS = {"sol": "Validator", "astra": "Completion Owner"}


def admit(state, blocking, revision):
    """Record this Validator dispatch as a validation-only round, or return the stop it needs.

    Returns None to dispatch, or {"reason", "request"} when the run must pause instead.
    """
    contract = state.get("goal_contract") or {}
    validation = state.get("validation") or {}
    task = state.get("current_task") or {}
    if (not blocking or not task.get("id") or not revision or not contract.get("hash")
            or validation.get("source_revision") != revision
            or validation.get("contract_hash") != contract["hash"]):
        return None
    attempt = {"task_id": task["id"], "after_validation": validation.get("output")}
    frontier = {"contract_hash": contract["hash"], "source_revision": revision,
                "findings": sorted(row["id"] for row in blocking),
                "passed": sorted(str(row.get("id")) for row in validation.get("criterion_results") or []
                                 if row.get("status") == "PASS"),
                "accepted": sorted(key for key, row in (state.get("milestone_progress") or {}).items()
                                   if isinstance(row, dict) and row.get("accepted")
                                   and row.get("contract_hash") == contract["hash"])}
    rounds = state.setdefault(KEY, [])
    if any(row.get("attempt") == attempt for row in rounds):
        return None
    stalled = []
    for row in reversed(rounds):
        if row.get("frontier") != frontier:
            break
        stalled.append(row)
    if len(stalled) >= LIMIT:
        return _stop(state, blocking, frontier, stalled)
    rounds.append({"attempt": attempt, "frontier": frontier, "at": util.now()})
    return None


def _why(row):
    owner = OWNERS.get(row.get("source"), "raising reviewer")
    pending = row.get("pending_resolution") or {}
    if pending:
        unverified = ", ".join(pending.get("unverified_criteria") or [])
        return (f"{row['id']} ({owner}): its resolution was not accepted ({pending.get('reason', 'unsupported')}"
                + (f"; unverified {unverified}" if unverified else "") + ")")
    if row.get("not_rechecked_in"):
        return f"{row['id']} ({owner}): the {owner}'s latest accepted report did not recheck it"
    return f"{row['id']} ({owner}): the {owner} still reports it"


def _stop(state, blocking, frontier, stalled):
    tasks = {row["attempt"]["task_id"] for row in stalled} | {(state.get("current_task") or {}).get("id")}
    rejected = list(dict.fromkeys(
        str(row.get("rejection_reason") or "").strip()[:300] for row in state.get("stages", [])
        if row.get("rejected") and row.get("task_id") in tasks
        and (row.get("original_stage") or row.get("stage")) == "sol"
        and str(row.get("rejection_reason") or "").strip()))
    ids = ", ".join(frontier["findings"])
    why = "; ".join(_why(row) for row in blocking)
    if rejected:
        why += ". Rejected Validator reports in these rounds: " + "; ".join(rejected)
    reason = (f"{len(stalled)} validation-only rounds at source {frontier['source_revision'][:12]} left blocking "
              f"finding(s) {ids} open; no further Validator was launched for them. {why}")
    request = {"kind": "blocker", "discovered": reason,
               "impact": why + ". Only the reviewer that raised a finding can close it, with a fresh independently "
                               "evidenced report; work, evidence and findings are retained.",
               "decision_needed": (f"Blocking finding(s) {ids} stayed open through {len(stalled)} validation-only "
                                   "rounds at this source. What must change before they are rechecked?"),
               "options": ["Provide corrective information", "Leave paused"],
               "proposed_delta": "Answering closes no finding and does not authorize a retry, approval, "
                                 "permission or budget change."}
    return {"reason": reason, "request": request}
