"""Stop validation-only rounds that keep leaving the same blocking findings open (#300).

The Completion Owner can send unchanged work back to the Validator, as a validate task or as
VALIDATE. Only the reviewer that raised a finding closes it, through a fresh, independently
evidenced report (autocode_findings); a rejected or repaired report cannot. A live run kept
dispatching such rounds for one Validator-owned finding whose closing reports were never
accepted. When a blocking finding stays open through ``LIMIT`` rounds in a row that made no
progress on it, the runner launches no further Validator and asks the user one question naming
those findings and why each remains open. Nothing here closes, merges or retracts a finding, and
the answer authorizes no retry.

A round is validation-only when the source is the one the latest accepted validation already
reviewed. Another attempt at the same round (a recovered timeout, an explicit fresh retry) is
not a new round. Rounds are counted per finding, so a finding opened or closed elsewhere does not
restart another finding's count. Progress only counts when it is new at this contract and source:
a criterion passing or a milestone accepted for the first time, or the finding reaching a state it
had not had (reported, not rechecked, resolution pending) or fewer unverified criteria than ever.
Each of those can happen only finitely often, so results that alternate cannot keep a finding's
rounds going.

Imports nothing from the runner: the caller (autopilot.admit_validation, before every Validator
dispatch) passes the open blocking ledger rows and the current source revision, and publishes the
returned request through the existing AutoResolver operational request. State key written:
``validation_only_rounds`` (one entry per admitted round, written only by ``admit``; read only
here).
"""
from __future__ import annotations

try:
    from . import autocode_finding_cause as finding_cause
    from . import autocode_util as util
except ImportError:
    import autocode_finding_cause as finding_cause
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
                "findings": {row["id"]: list(_status(row)) for row in blocking},
                "passed": sorted(str(row.get("id")) for row in validation.get("criterion_results") or []
                                 if row.get("status") == "PASS"),
                "accepted": sorted(key for key, row in (state.get("milestone_progress") or {}).items()
                                   if isinstance(row, dict) and row.get("accepted")
                                   and row.get("contract_hash") == contract["hash"])}
    rounds = state.setdefault(KEY, [])
    if any(row.get("attempt") == attempt for row in rounds):
        return None
    segment = []
    for row in reversed(rounds):
        saved = row.get("frontier") or {}
        if (saved.get("contract_hash"), saved.get("source_revision")) != (contract["hash"], revision) \
                or not isinstance(saved.get("findings"), dict):
            break
        segment.insert(0, row)
    stalled = _stalled([row["frontier"] for row in segment] + [frontier])
    if stalled:
        count = max(stalled.values())
        return _stop(state, [row for row in blocking if row["id"] in stalled], revision, segment[-count:], count)
    rounds.append({"attempt": attempt, "frontier": frontier, "at": util.now()})
    return None


def _status(row):
    """A finding's state as its reviewer left it, and how many criteria still keep it from closing."""
    pending = row.get("pending_resolution")
    if pending:
        return "pending", len(pending.get("unverified_criteria") or [])
    return ("not_rechecked" if row.get("not_rechecked_in") else "reported"), None


def _stalled(frontiers):
    """{finding id: rounds without progress} for each finding the latest frontier holds open through
    LIMIT or more consecutive rounds that made no new progress, overall or on that finding."""
    progress, passed, accepted = 0, set(), set()
    since, states, fewest = {}, {}, {}
    for index, frontier in enumerate(frontiers):
        if index and (set(frontier["passed"]) - passed or set(frontier["accepted"]) - accepted):
            progress = index
        passed |= set(frontier["passed"])
        accepted |= set(frontier["accepted"])
        for ident in [ident for ident in since if ident not in frontier["findings"]]:
            # Closed in that round: if it is reported open again, its count starts over.
            del since[ident], states[ident]
            fewest.pop(ident, None)
        for ident, (name, unverified) in frontier["findings"].items():
            seen = states.setdefault(ident, set())
            fewer = unverified is not None and unverified < fewest.get(ident, unverified + 1)
            if ident not in since or name not in seen or fewer:
                since[ident] = index
            seen.add(name)
            if unverified is not None:
                fewest[ident] = min(unverified, fewest.get(ident, unverified))
    latest = len(frontiers) - 1
    rounds = {ident: latest - max(since[ident], progress) for ident in frontiers[latest]["findings"]}
    return {ident: count for ident, count in rounds.items() if count >= LIMIT}


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


def _stop(state, stalled, revision, window, count):
    tasks = {row["attempt"]["task_id"] for row in window} | {(state.get("current_task") or {}).get("id")}
    rejected = list(dict.fromkeys(
        str(row.get("rejection_reason") or "").strip()[:300] for row in state.get("stages", [])
        if row.get("rejected") and row.get("task_id") in tasks
        and (row.get("original_stage") or row.get("stage")) == "sol"
        and str(row.get("rejection_reason") or "").strip()))
    ids = ", ".join(sorted(row["id"] for row in stalled))
    why = "; ".join(_why(row) for row in sorted(stalled, key=lambda row: row["id"]))
    settled = _settled_matches(state, stalled)
    if settled:
        why += ". " + "; ".join(settled)
    if rejected:
        why += ". Rejected Validator reports in these rounds: " + "; ".join(rejected)
    reason = (f"{count} validation-only rounds at source {revision[:12]} made no progress on blocking "
              f"finding(s) {ids}; no further Validator was launched for them. {why}")
    request = {"kind": "blocker", "discovered": reason,
               "impact": why + ". Only the reviewer that raised a finding can close it, with a fresh independently "
                               "evidenced report; work, evidence and findings are retained.",
               "decision_needed": (f"Blocking finding(s) {ids} stayed open through {count} validation-only "
                                   "rounds at this source. What must change before they are rechecked? Answering "
                                   "keeps the run paused and launches no Validator; to continue instead, revise "
                                   "the goal with --feedback, or, if a finding no longer applies (for example a "
                                   "duplicate of one already settled), close it with --close-finding ID "
                                   "--close-reason TEXT."),
               "options": ["Provide corrective information", "Leave paused"],
               "finding_ids": sorted(row["id"] for row in stalled),
               "proposed_delta": "Answering closes no finding and does not authorize a retry, approval, "
                                 "permission or budget change."}
    return {"reason": reason, "request": request}


def _settled_matches(state, stalled):
    """A hint, never a closure: stalled findings with the same text or evidence as a resolved one.

    Another part of the same split finding is not offered: it covers other criteria
    (autocode_finding_cause.split_family), so closing this part would drop the defect for its own.
    The user is told it was resolved for those criteria, which does not settle this part."""
    resolved = [row for row in state.get("findings_ledger", []) if row.get("status") == "resolved"]
    hints = []
    for row in sorted(stalled, key=lambda row: row["id"]):
        family = finding_cause.split_family(row)
        parts = [other["id"] for other in resolved if finding_cause.split_family(other) == family]
        same = [other["id"] for other in resolved
                if finding_cause.split_family(other) != family
                and ((row.get("finding") and other.get("finding") == row.get("finding"))
                     or (row.get("evidence") and other.get("evidence") == row.get("evidence")))]
        if parts:
            hints.append(f"{row['id']} and {', '.join(parts)} are parts of finding {family}, split across milestones by "
                         f"an approved revision; resolving {', '.join(parts)} covered other criteria and does not "
                         f"settle {row['id']}")
        if same:
            hints.append(f"{row['id']} has the same finding or evidence as resolved {', '.join(same)}; if it is the "
                         f"same problem, close it with --close-finding {row['id']} --close-reason TEXT")
    return hints
