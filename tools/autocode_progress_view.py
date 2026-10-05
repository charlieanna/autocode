"""How far a run has got, counted only from saved records: `view.progress`.

One line a person can read ("Builder working · 2 of 5 tasks done · 3 of 6
requirements checked · 1 open problem · nothing needed from you") and the
counts behind it, so a caller never has to assemble them from state keys.

Rules (#29): every number comes from a saved record (the plan's milestones,
the accepted milestone checkpoints, the Tester's criterion results, the
person's review receipts, the findings); no percentages; nothing is estimated
from elapsed time; a requirement nobody has checked is never shown as checked,
and one checked before the latest build says so.

Pure: the caller passes what needs controller services (the accepted milestone
IDs, the criteria with a valid review receipt, the stage's screen name, the
view's `needs`, and whether the recorded workers are gone). Imports nothing
from AutoCode.
"""
from __future__ import annotations

COMPLETE = ("TASK_COMPLETE", "COMPLETE")
# What each `needs.kind` asks of a person, and the headline while it waits.
WAITING = {"answer": "Waiting for you", "review": "Waiting for you", "approve_plan": "Waiting for you",
           "planning_budget": "Waiting for you", "resume": "Paused", "retry_job": "Paused",
           "dependency": "Waiting for another run"}
# The stop of a design check that found conflicts (autocode_design_check_job.STOP_STATUS).
DESIGN_CONFLICT = "PAUSED_DESIGN_CONFLICT"
# A validation archived with this reason was never applied to the run (autopilot.apply_review_result).
NOT_APPLIED = "Stored during an open correction; not applied to the current candidate"


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" + ("" if number == 1 else "s")


def progress(state: dict, *, accepted, stage: str | None, needs: dict | None,
             reviewed=(), stopped: str | None = None, finished: bool = False) -> dict:
    """The progress projection of one run.

    accepted  milestone IDs accepted under the current contract (autocode_milestones.accepted_ids)
    stage     screen name of the running stage, else of the next one; None when there is none
    needs     the status view's `needs`
    reviewed  criterion IDs whose human review receipt is valid now (autocode_goals.missing_human_reviews)
    stopped   the next action when the recorded workers are gone with no saved report, else None
    finished  the running stage's turn is over (its report is saved, not yet applied)
    """
    status = state.get("status", "")
    done = status in COMPLETE
    contract = _mapping(state.get("goal_contract"))
    body = _mapping(contract.get("body"))
    earlier = _earlier_request(state, contract)
    if earlier:
        body = {}
    tasks = _tasks(state, body, set(accepted or ()), done, running=not stopped and not finished)
    requirements = _requirements(state, contract if not earlier else {}, body, set(reviewed or ()), legacy=not earlier)
    if earlier:
        tasks["label"] = "no task list yet for this request"
        requirements["label"] = "no requirements yet for this request"
    problems = _problems(state, _rows(state.get("turns")))
    kind = (needs or {}).get("kind")
    asked = _asked(needs, state)
    if done:
        headline = "Complete"
    elif stopped:
        headline = f"{stage or 'The stage'} stopped without saving a report"
        asked = stopped
    elif kind in WAITING:
        headline = WAITING[kind]
    elif status == "AWAITING_GOAL_APPROVAL":
        # The plan is saved but its approval token is not yet (needs.kind "continue"); a
        # --chat run is asking at its prompt, and otherwise the next launch shows it.
        headline, asked = "Waiting for you", "plan approval needed"
    elif stage and state.get("active_stage") and finished:
        headline = f"{stage} finished"
    elif stage and state.get("active_stage"):
        headline = f"{stage} working"
    elif stage:
        headline = f"Next: {stage}"
    else:
        headline = status.replace("_", " ").capitalize() or "Not started"
    line = " · ".join([headline, tasks["label"], requirements["label"],
                       _count(problems["open"], "open problem"), asked])
    return {"line": line, "headline": headline, "stage": stage, "needs_you": asked,
            "for_earlier_request": earlier, "tasks": tasks, "requirements": requirements, "problems": problems}


def _earlier_request(state, contract):
    """True when a --follow-up came after the saved plan was drafted: that plan, its tasks and
    its checks answer the previous request, not the one the run is on now."""
    turns = _rows(state.get("turns"))
    drafted = contract.get("created_at") or _mapping(contract.get("approval_event")).get("at")
    return bool(turns and contract and isinstance(drafted, str)
                and isinstance(turns[-1].get("at"), str) and drafted < turns[-1]["at"])


def _tasks(state, body, accepted, done, running):
    milestones = _rows(body.get("milestones"))
    tracked = done or (_mapping(_mapping(state.get("settings")).get("milestone_checkpoints")).get("enabled") is True)
    task = _mapping(state.get("current_task"))
    current = set(task.get("milestone_ids") or [task.get("milestone_id")]) - {None}
    working = running and state.get("status") == "RUNNING" and bool(state.get("active_stage"))
    items = []
    for number, milestone in enumerate(milestones, 1):
        mid = milestone.get("id")
        if done or mid in accepted:
            item_state = "done"
        elif mid in current and working:
            item_state = "working"
        else:
            item_state = "waiting" if tracked else "unknown"
        items.append({"number": number, "id": mid, "label": milestone.get("objective") or milestone.get("title") or str(mid),
                      "state": item_state, "current": mid in current and not done})
    finished = sum(item["state"] == "done" for item in items)
    if not items:
        label = "no task list yet"
    elif tracked:
        label = f"{finished} of {_count(len(items), 'task')} done"
    else:
        label = f"{_count(len(items), 'task')}, completion not tracked"
    return {"known": bool(items) and tracked, "done": finished, "total": len(items), "label": label, "items": items}


def _requirements(state, contract, body, reviewed, legacy):
    """Each criterion's newest PASS or FAIL across the Tester results under this contract.

    NOT_VERIFIED and a missing row say nothing new, so an earlier verdict stands. Only the
    current validation, with no source change recorded since, counts as `checked`; a pass from
    an earlier validation, or from one the code has changed since, is `checked_earlier`."""
    criteria = body.get("acceptance_criteria") or (state.get("acceptance_criteria") if legacy else None) or []
    criteria = [row if isinstance(row, dict) else {"id": None, "criterion": str(row)} for row in criteria]
    chash = contract.get("hash")

    def same_contract(record):
        return record.get("contract_hash") == chash if chash else True

    validation = _mapping(state.get("validation")) if criteria else {}
    current = validation if validation and same_contract(validation) else {}
    rebuilt = _rebuilt_since(state, current)
    history = [_mapping(entry.get("validation")) for entry in _rows(state.get("validation_archive"))
               if entry.get("reason") != NOT_APPLIED] if criteria else []
    verdicts = {}
    for record in [*(row for row in history if same_contract(row)), *([current] if current else [])]:
        for row in _rows(record.get("criterion_results")):
            outcome = str(row.get("status") or "").upper()
            if outcome in ("PASS", "FAIL"):
                verdicts[row.get("id")] = (outcome, record is current and not rebuilt)
    items = []
    for criterion in criteria:
        cid = criterion.get("id")
        outcome, fresh = verdicts.get(cid, (None, False))
        if cid is not None and cid in reviewed:
            # The receipt binds the current validation; after a rebuild it accepted earlier code.
            item_state = "checked_earlier" if rebuilt else "reviewed"
        elif outcome == "FAIL":
            item_state = "failed"
        elif outcome == "PASS" and criterion.get("human_review") is True and fresh:
            item_state = "awaiting_review"
        elif outcome == "PASS":
            item_state = "checked" if fresh else "checked_earlier"
        else:
            item_state = "unchecked"
        items.append({"id": cid, "label": criterion.get("criterion") or criterion.get("text") or str(cid),
                      "state": item_state})
    counts = {name: sum(item["state"] == name for item in items)
              for name in ("checked", "checked_earlier", "reviewed", "failed", "awaiting_review", "unchecked")}
    passed = counts["checked"] + counts["checked_earlier"] + counts["reviewed"]
    if not items:
        label = "no requirements yet"
    else:
        notes = [text for number, text in ((counts["checked_earlier"], f"{counts['checked_earlier']} from earlier checks"),
                                           (counts["reviewed"], f"{counts['reviewed']} accepted in review")) if number]
        label = f"{passed} of {_count(len(items), 'requirement')} checked" + (f" ({', '.join(notes)})" if notes else "")
        if counts["failed"]:
            label += f", {counts['failed']} failed"
        if counts["awaiting_review"]:
            label += f", {counts['awaiting_review']} awaiting your review"
    return {**counts, "total": len(items), "validated_source_revision": current.get("source_revision"),
            "rebuilt_since_check": rebuilt, "label": label, "items": items}


def _rebuilt_since(state, validation):
    """Whether a stage recorded after this validation saw different source than it checked.

    Stage records carry the source revision after the stage; a Builder that changed nothing,
    or a dispatch record with no revision, does not count."""
    checked = validation.get("source_revision")
    stages = _rows(state.get("stages"))
    index = next((number for number, record in enumerate(stages)
                  if validation.get("output") and record.get("output") == validation["output"]), None)
    return bool(checked and index is not None and any(
        record.get("source_revision") and record["source_revision"] != checked for record in stages[index + 1:]))


def _problems(state, turns):
    """Open findings: the build ledger, and the counts a review or design workflow saves instead of it
    (only those its job wrote for the current request: not before the latest --follow-up)."""
    items = [{**{key: row.get(key) for key in ("id", "finding", "source", "severity")},
              "blocking": row.get("blocking", True)}
             for row in _rows(state.get("findings_ledger")) if row.get("status") == "open"]
    kind = _mapping(state.get("workflow")).get("kind")
    reports = []
    for key, workflow in (("review", "review"), ("design_review", "design")):
        record = _mapping(state.get(key))
        if kind == workflow and isinstance(record.get("blocking"), int) and _since_last_turn(state, record, turns):
            reports.append({"kind": key, "blocking": record["blocking"], "advisory": record.get("advisory") or 0,
                            "report_path": record.get("report_path")})
    check = _mapping(state.get("design_check"))
    if state.get("status") == DESIGN_CONFLICT and isinstance(check.get("conflicts"), int):
        reports.append({"kind": "design_check", "blocking": check["conflicts"], "advisory": 0,
                        "report_path": check.get("blockers")})
    total = len(items) + sum(report["blocking"] + report["advisory"] for report in reports)
    return {"open": total, "items": items, "reports": reports}


def _asked(needs, state=None):
    kind = (needs or {}).get("kind")
    if kind == "answer":
        return f"{_count(len(needs.get('questions') or []), 'question')} to answer"
    if kind == "review":
        return f"{_count(len(needs.get('criteria') or []), 'requirement')} to review"
    if kind == "approve_plan":
        return "plan approval needed"
    if kind == "planning_budget":
        return "plan review budget used up"
    if kind == "resume":
        return "inspect the pause, then resume"
    if kind == "retry_job" and needs.get("route"):
        route = needs["route"]
        job = route.get("job") or "job"
        # Once a person named the model (job_failure.route_assignment), only the retry is left (#463).
        if _mapping(_mapping((state or {}).get("job_failure")).get("route_assignment")):
            return f"retry the {job} on {route.get('current_model')}"
        return f"name another model for the {job}, then retry it"
    if kind == "retry_job":
        return "inspect the failed job, then retry"
    if kind == "dependency":
        return "nothing needed from you until the other run delivers"
    return "nothing needed from you"


def _since_last_turn(state, record, turns):
    if not turns:
        return True
    started = next((row.get("started_at") for row in _rows(state.get("stages"))
                    if record.get("output") and row.get("output") == record["output"]), None)
    return isinstance(started, str) and isinstance(turns[-1].get("at"), str) and started > turns[-1]["at"]
