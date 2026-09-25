"""One authoritative list of reviewer findings.

Sol reports findings in every validation; Astra reports them in review and
checkpoint decisions.  Both land here with a runner-owned identity, the task assigned
to fix them, and the report that resolved them.  The identity is not derived
from the wording or the evidence, so two defects with the same description stay
separate until a report cites one existing id.  Only the reviewer who raised a
finding can resolve it, and only through an explicit disposition on that
finding's ID in a fresh report: ``resolved`` with verification evidence, or
``retracted`` when the finding itself was wrong.  A report that simply omits a
finding leaves it open and marks it as not rechecked, and a report-only repair
can never close one.  A BLOCKED review records the defects it has already found
and does not close any.  The resolver diagnoses findings; it never reconciles
them.
"""
from __future__ import annotations

import copy

try:
    from . import autocode_support as s
    from . import autocode_milestones as milestones
except ImportError:
    import autocode_support as s
    import autocode_milestones as milestones

SOURCES = ("sol", "astra")
SEVERITIES = ("critical", "high", "medium", "low")
DISPOSITIONS = ("resolved", "retracted")


def allocate_id(state) -> str:
    """A runner-owned id. Wording and evidence are not part of it, so two defects
    with the same description stay distinct until a report cites one id."""
    taken = {row.get("id") for row in state.get("findings_ledger", [])}
    seq = int(state.get("findings_seq", 0))
    while True:
        seq += 1
        candidate = "F-" + s.digest({"n": seq})[:10]
        if candidate not in taken:
            state["findings_seq"] = seq
            return candidate


def ledger(state) -> list:
    return state.setdefault("findings_ledger", [])


def open_entries(state, source=None) -> list:
    return [row for row in state.get("findings_ledger", [])
            if row.get("status") == "open" and (source is None or row.get("source") == source)]


def blocking_entries(state) -> list:
    return [row for row in open_entries(state) if row.get("blocking", True)]


def _normalize(source, raw):
    if not isinstance(raw, dict):
        raise ValueError(f"{source} findings must be objects")
    text = str(raw.get("finding", "")).strip()
    if not text:
        raise ValueError(f"{source} findings need a non-empty finding text")
    severity = raw.get("severity", "medium")
    if severity not in SEVERITIES:
        raise ValueError(f"{source} finding severity must be one of {', '.join(SEVERITIES)}")
    return {"source": source, "severity": severity, "finding": text,
            "evidence": str(raw.get("evidence", "")), "blocking": bool(raw.get("blocking", True)),
            "cited_id": str(raw.get("id") or "").strip()}


def report_scope(state):
    """The milestone criteria the current report reviewed, or None for runs without milestones."""
    body = state.get("goal_contract", {}).get("body", {})
    if not body.get("milestones") or not state.get("current_task"):
        return None
    scope = milestones.scope(state)
    criteria = sorted(set(scope.get("acceptance_criteria", [])))
    if not criteria:
        return None
    return {"milestone_id": scope.get("id", ""), "criteria": criteria}


def _initial_task_scope(contract, task):
    """An initial planner's findings belong to its explicit approved task scope."""
    if task.get("kind") != "implement" or task.get("milestone_ids"):
        return None
    body = contract.get("body", {})
    milestone = next((m for m in body.get("milestones", [])
                      if m["id"] == task.get("milestone_id")), None)
    criteria = task.get("acceptance_criteria", [])
    if (not milestone or not criteria or len(criteria) != len(set(criteria))
            or not set(criteria) <= set(milestone["acceptance_criteria"])
            or not set(criteria) <= {c["id"] for c in body.get("acceptance_criteria", [])}):
        return None
    return {"milestone_id": milestone["id"], "criteria": sorted(criteria)}


def restore_initial_plan_scopes(state):
    """Recover only proven initial-plan scope; never infer scope from later assignment.

    Older runners recorded the first Astra plan before assigning its task, leaving
    scope null. The accepted plan, first assignment and sealed contract jointly
    identify that scope. Unknown/global findings retain the full-review rule.
    """
    current = state.get("goal_contract", {})
    current_criteria = {c["id"]: c for c in current.get("body", {}).get("acceptance_criteria", [])}
    tasks = [*state.get("task_archive", []), state.get("current_task") or {}]
    contracts = [*state.get("contract_history", []), current]
    for row in ledger(state):
        history = row.get("assigned_history", [])
        if (row.get("source") != "astra" or row.get("status") != "open"
                or row.get("scope") is not None or not history):
            continue
        plans = [r for r in state.get("stages", []) if r.get("output") == row.get("opened_in")
                 and r.get("stage") == "astra_plan" and not r.get("task_id")
                 and r.get("exit_code") == 0 and not r.get("timed_out")
                 and not r.get("interrupted") and not r.get("rejected") and not r.get("abandoned")]
        assigned = [t for t in tasks if t.get("id") == history[0].get("task_id")
                    and row["id"] in t.get("findings", [])]
        if len(plans) != 1 or len(assigned) != 1:
            continue
        plan, task = plans[0], assigned[0]
        identity = (plan.get("contract_hash"), plan.get("contract_revision"))
        if not all(identity) or identity != (task.get("contract_hash"), task.get("contract_revision")):
            continue
        matched = [c for c in contracts if (c.get("hash"), c.get("revision")) == identity]
        token = f"r{identity[1]}:{identity[0]}"
        # Feedback can archive an old contract after resetting its approval flag;
        # the exact original approval event remains authoritative history.
        approved_event = any(e.get("kind") == "goal_approval" and e.get("token") == token
                             for e in state.get("user_events", []))
        if len(matched) != 1 or not (matched[0].get("approval_status") == "approved" or approved_event):
            continue
        scope = _initial_task_scope(matched[0], task)
        old_criteria = {c["id"]: c for c in matched[0].get("body", {}).get("acceptance_criteria", [])}
        if not scope or any(current_criteria.get(cid) != old_criteria[cid] for cid in scope["criteria"]):
            continue
        row.update(scope=scope, scope_restored_from={
            "plan_output": plan["output"], "task_id": task["id"],
            "contract_hash": identity[0], "contract_revision": identity[1], "approval_token": token})


def _covers(report, row_scope, all_criteria):
    """True when a report reviewed everything the finding was raised against."""
    if report is None:
        return True
    reviewed = set(report["criteria"])
    if row_scope is None:
        # A finding saved before scopes were recorded closes only on a full review.
        return bool(all_criteria) and reviewed >= all_criteria
    return set(row_scope["criteria"]) <= reviewed


def _apply_dispositions(state, source, dispositions, record, scope, all_criteria, can_resolve):
    rows = ledger(state)
    report = record.get("output")
    open_rows = {row["id"]: row for row in rows if row.get("source") == source and row.get("status") == "open"}
    if not isinstance(dispositions, list):
        raise ValueError(f"{source} finding_dispositions must be an array")
    for raw in dispositions:
        if not isinstance(raw, dict):
            raise ValueError(f"{source} finding_dispositions entries must be objects")
        target = raw.get("id")
        disposition = raw.get("disposition")
        evidence = str(raw.get("evidence", "")).strip()
        if disposition not in DISPOSITIONS:
            raise ValueError(f"{source} finding dispositions must be one of {', '.join(DISPOSITIONS)}")
        if not evidence:
            raise ValueError(f"{source} finding dispositions need evidence")
        row = open_rows.get(target)
        if row is None:
            # The finding may already be closed, or the original report may have
            # been rejected before its findings were recorded. A disposition for a
            # finding that was never recorded or is already closed is a no-op.
            continue
        if not can_resolve:
            raise ValueError("A report-only repair cannot close findings; resubmit the review")
        if not _covers(scope, row.get("scope"), all_criteria):
            raise ValueError(f"{source} disposition {target} belongs to work this report did not review")
        row.update(status=disposition, resolved_at=s.now(), resolved_in=report, resolution_evidence=evidence)
        row.pop("not_rechecked_in", None)


def _record(state, source, reported, record, initial_scope=None):
    """Reconcile one reviewer's latest report against that reviewer's open findings."""
    rows = ledger(state)
    at = s.now()
    report = record.get("output")
    restore_initial_plan_scopes(state)
    scope = initial_scope or report_scope(state)
    all_criteria = {c["id"] for c in state.get("goal_contract", {}).get("body", {}).get("acceptance_criteria", [])}
    # A repair only reformats an earlier report; it is not a fresh review of the work.
    can_resolve = not (record.get("report_repaired") or record.get("report_only"))
    open_ids = {row["id"] for row in rows if row.get("source") == source and row.get("status") == "open"}
    seen = {}
    for raw in reported:
        entry = _normalize(source, raw)
        cited = entry.pop("cited_id")
        if cited:
            if cited not in open_ids or cited in seen:
                raise ValueError(f"{source} finding id {cited} is not one open finding of this reviewer")
            entry["id"] = cited
        else:
            entry["id"] = allocate_id(state)
        seen[entry["id"]] = entry
    for row in rows:
        if row.get("source") != source or row.get("status") != "open":
            continue
        if row["id"] in seen:
            latest = seen.pop(row["id"])
            row.update(severity=latest["severity"], evidence=latest["evidence"], blocking=latest["blocking"],
                       times_reported=row.get("times_reported", 1) + 1, last_reported_at=at, last_reported_in=report)
            row.pop("not_rechecked_in", None)
        else:
            row["not_rechecked_in"] = report
    for entry in seen.values():
        rows.append({**entry, "status": "open", "opened_at": at, "opened_in": report, "scope": scope,
                     "times_reported": 1, "last_reported_at": at, "last_reported_in": report,
                     "assigned_task": None})


def record_validation(state, validation, record):
    """Sol's findings open or refresh Sol entries; its dispositions close them."""
    _record(state, "sol", validation.get("findings", []), record)
    _apply_dispositions(state, "sol", validation.get("finding_dispositions", []), record,
                        report_scope(state),
                        {c["id"] for c in state.get("goal_contract", {}).get("body", {}).get("acceptance_criteria", [])},
                        not (record.get("report_repaired") or record.get("report_only")))


def record_decision(state, decision, record):
    """Astra's structured findings behave like Sol's.

    A BLOCKED review still records the defects it already identified. It does not
    close anything: the pause is about a missing decision, not a passing recheck.
    """
    initial_scope = (_initial_task_scope(state.get("goal_contract", {}), decision.get("next_task") or {})
                     if record.get("stage") == "astra_plan" and not state.get("current_task") else None)
    _record(state, "astra", decision.get("findings", []), record, initial_scope)
    if decision.get("status") == "BLOCKED":
        return
    _apply_dispositions(state, "astra", decision.get("finding_dispositions", []), record,
                        report_scope(state),
                        {c["id"] for c in state.get("goal_contract", {}).get("body", {}).get("acceptance_criteria", [])},
                        not (record.get("report_repaired") or record.get("report_only")))


def batch_limit(state):
    value = state.get("settings", {}).get("limits", {}).get("max_findings_per_task")
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError("max_findings_per_task must be a nonnegative integer or null")
    return value or None


def assign(state, task, spec, decision):
    """Link open findings to the correction task; refuse a batch above the saved limit."""
    if spec.get("kind") != "implement":
        task["findings"] = []
        return
    open_ids = {row["id"] for row in open_entries(state)}
    requested = spec.get("findings")
    # The generation schema always includes the field, so an empty list means
    # "every open finding", the same as omitting it.
    if not requested:
        selected = sorted(open_ids)
    else:
        selected = list(dict.fromkeys(requested))
        unknown = [fid for fid in selected if fid not in open_ids]
        if unknown:
            raise ValueError("Task findings must name open ledger IDs: " + ", ".join(unknown))
    limit = batch_limit(state)
    if limit is not None and decision.get("status") == "REWORK" and len(selected) > limit:
        raise ValueError(f"Correction batch names {len(selected)} open findings; the saved limit is {limit}. "
                         "Split the rework into smaller tasks, each addressing a few findings.")
    task["findings"] = selected
    for row in ledger(state):
        if row["id"] in selected and row.get("status") == "open":
            row["assigned_task"] = task["id"]
            row.setdefault("assigned_history", []).append({"task_id": task["id"], "at": s.now()})


def milestone(row):
    return (row.get("scope") or {}).get("milestone_id") or None


def handoff(state) -> list:
    """Compact open findings for role prompts: identity, source, text, milestone, fix task, repeat count."""
    return [{**{key: row.get(key) for key in ("id", "source", "severity", "finding", "evidence", "blocking",
                                               "assigned_task", "times_reported")},
             "milestone": milestone(row), "not_rechecked": bool(row.get("not_rechecked_in"))}
            for row in open_entries(state)]


def summary(state) -> dict:
    rows = state.get("findings_ledger", [])
    open_rows = [row for row in rows if row.get("status") == "open"]
    return {"open": len(open_rows), "resolved": sum(1 for row in rows if row.get("status") == "resolved"),
            "retracted": sum(1 for row in rows if row.get("status") == "retracted"),
            "by_source": {source: sum(1 for row in open_rows if row.get("source") == source) for source in SOURCES},
            "repeated": sum(1 for row in open_rows if row.get("times_reported", 1) > 1),
            "not_rechecked": sum(1 for row in open_rows if row.get("not_rechecked_in")),
            "entries": [copy.deepcopy({**{key: row.get(key) for key in (
                "id", "source", "severity", "finding", "status", "assigned_task", "times_reported",
                "opened_at", "resolved_at")}, "milestone": milestone(row)}) for row in rows]}
