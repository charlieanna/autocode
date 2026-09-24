"""One authoritative list of open reviewer findings.

Sol reports findings in every validation; Astra may report them in a REWORK
decision.  Both land here with a stable identity, the task assigned to fix
them, and the later report that resolved them.  Only the reviewer who raised a
finding can resolve it, by submitting a newer report that no longer lists it
and that reviewed the milestone scope the finding was raised under.  A review
of different work, or a reformatting repair of an earlier report, leaves the
finding open.
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


def finding_id(source: str, text: str) -> str:
    return "F-" + s.digest({"source": source, "finding": " ".join(str(text).split()).lower()})[:10]


def ledger(state) -> list:
    return state.setdefault("findings_ledger", [])


def open_entries(state, source=None) -> list:
    return [row for row in state.get("findings_ledger", [])
            if row.get("status") == "open" and (source is None or row.get("source") == source)]


def _normalize(source, raw):
    if not isinstance(raw, dict):
        raise ValueError(f"{source} findings must be objects")
    text = str(raw.get("finding", "")).strip()
    if not text:
        raise ValueError(f"{source} findings need a non-empty finding text")
    severity = raw.get("severity", "medium")
    if severity not in SEVERITIES:
        raise ValueError(f"{source} finding severity must be one of {', '.join(SEVERITIES)}")
    return {"id": finding_id(source, text), "source": source, "severity": severity, "finding": text,
            "evidence": str(raw.get("evidence", "")), "blocking": bool(raw.get("blocking", True))}


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


def _covers(report, row_scope, all_criteria):
    """True when a report reviewed everything the finding was raised against."""
    if report is None:
        return True
    reviewed = set(report["criteria"])
    if row_scope is None:
        # A finding saved before scopes were recorded closes only on a full review.
        return bool(all_criteria) and reviewed >= all_criteria
    return set(row_scope["criteria"]) <= reviewed


def _record(state, source, reported, record):
    """Reconcile one reviewer's latest report against that reviewer's open findings."""
    rows = ledger(state)
    at = s.now()
    report = record.get("output")
    scope = report_scope(state)
    all_criteria = {c["id"] for c in state.get("goal_contract", {}).get("body", {}).get("acceptance_criteria", [])}
    # A repair only reformats an earlier report; it is not a fresh review of the work.
    can_resolve = not (record.get("report_repaired") or record.get("report_only"))
    seen = {}
    for raw in reported:
        entry = _normalize(source, raw)
        seen[entry["id"]] = entry
    for row in rows:
        if row.get("source") != source or row.get("status") != "open":
            continue
        if row["id"] in seen:
            latest = seen.pop(row["id"])
            row.update(severity=latest["severity"], evidence=latest["evidence"], blocking=latest["blocking"],
                       times_reported=row.get("times_reported", 1) + 1, last_reported_at=at, last_reported_in=report)
            row.pop("not_rechecked_in", None)
        elif can_resolve and _covers(scope, row.get("scope"), all_criteria):
            row.update(status="resolved", resolved_at=at, resolved_in=report)
            row.pop("not_rechecked_in", None)
        else:
            row["not_rechecked_in"] = report
    for entry in seen.values():
        rows.append({**entry, "status": "open", "opened_at": at, "opened_in": report, "scope": scope,
                     "times_reported": 1, "last_reported_at": at, "last_reported_in": report,
                     "assigned_task": None})


def record_validation(state, validation, record):
    """Sol's findings open or refresh Sol entries; absent in-scope ones are resolved by this validation."""
    _record(state, "sol", validation.get("findings", []), record)


def record_decision(state, decision, record):
    """Astra's structured findings behave like Sol's. BLOCKED decisions leave the ledger unchanged."""
    if decision.get("status") == "BLOCKED":
        return
    _record(state, "astra", decision.get("findings", []), record)


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
            "by_source": {source: sum(1 for row in open_rows if row.get("source") == source) for source in SOURCES},
            "repeated": sum(1 for row in open_rows if row.get("times_reported", 1) > 1),
            "not_rechecked": sum(1 for row in open_rows if row.get("not_rechecked_in")),
            "entries": [copy.deepcopy({**{key: row.get(key) for key in (
                "id", "source", "severity", "finding", "status", "assigned_task", "times_reported",
                "opened_at", "resolved_at")}, "milestone": milestone(row)}) for row in rows]}
