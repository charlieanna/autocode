"""Read-only Work summaries from a dashboard view and supported CLI status.

No filesystem, runner imports, time estimates or inferred acceptance. Milestone
completion is usable only when the CLI's checked status names this contract.
"""

try:
    from .dashboard_verification import matches
except ImportError:
    from dashboard_verification import matches


def mapping(value):
    return value if isinstance(value, dict) else {}


def rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def progress_from_status(status):
    status = mapping(status)
    checkpoint = mapping(status.get("milestone_checkpoint"))
    accepted = checkpoint.get("accepted_milestones")
    return {
        "contract_token": status.get("contract_token"),
        "accepted": accepted if isinstance(accepted, list) else None,
        "current_task": mapping(status.get("current_task")),
        "working": status.get("status") == "RUNNING"
        and not status.get("stale")
        and not status.get("active_stage_finished")
        and bool(status.get("active_stage") or mapping(status.get("view")).get("runner_check")),
    }


def project(view):
    goal = mapping(view.get("goal"))
    body = mapping(goal.get("body"))
    expected = f"r{goal['revision']}:{goal['hash']}" if goal.get("hash") and goal.get("revision") is not None else None
    progress = mapping(mapping(view.get("interventions")).get("work_progress"))
    known = bool(expected and progress.get("contract_token") == expected and isinstance(progress.get("accepted"), list))
    accepted = set(progress["accepted"]) if known else set()
    current = mapping(progress.get("current_task")) if known else {}
    current_ids = current.get("milestone_ids") or [current.get("milestone_id")]
    tasks = []
    for index, milestone in enumerate(rows(body.get("milestones"))):
        mid = milestone.get("id")
        state = (
            "done"
            if mid in accepted
            else "working"
            if mid in current_ids and progress.get("working")
            else "waiting"
            if known
            else "unknown"
        )
        tasks.append(
            {
                "id": mid,
                "label": milestone.get("objective") or milestone.get("title") or str(mid or index + 1),
                "state": state,
                "number": index + 1,
            }
        )
    # Older plans have prose steps with no stable milestone IDs. Keep them
    # visible, but never invent completion from their order or current stage.
    if not tasks:
        plan = mapping(view.get("astra_plan")).get("current_plan") or view.get("plan") or []
        tasks = [
            {"id": None, "label": item, "state": "unknown", "number": index + 1}
            for index, item in enumerate(plan)
            if isinstance(item, str)
        ]
        known = False
    verification = mapping(view.get("verification"))
    current = verification.get("freshness") == "current" and matches(view, verification)
    stale = bool(view.get("validation")) and not current
    results = {str(row.get("id")): row for row in rows(verification.get("coverage"))} if current else {}
    requirements = []
    for criterion in rows(view.get("criteria")):
        result = results.get(str(criterion.get("id")), {})
        state = result.get("state") if result.get("state") in ("checked", "failed") else "unchecked"
        requirements.append(
            {
                "id": criterion.get("id"),
                "label": criterion.get("criterion") or criterion.get("description") or str(criterion.get("id")),
                "state": state,
                "verification_method": criterion.get("verification_method"),
                "human_review": criterion.get("human_review") is True,
            }
        )
    problems = [
        {
            "id": row.get("id") or f"problem-{index + 1}",
            "label": row.get("finding") or "Saved problem",
            "source": row.get("source"),
            "severity": row.get("severity"),
            "times_reported": row.get("times_reported", 1),
        }
        for index, row in enumerate(rows(mapping(view.get("monitor")).get("findings")))
    ]
    done = sum(row["state"] == "done" for row in tasks)
    passed = sum(row["state"] == "checked" for row in requirements)
    failed = sum(row["state"] == "failed" for row in requirements)
    unknown = len(requirements) - passed - failed
    task_text = f"{done} of {len(tasks)} tasks complete" if known else "Task completion not yet verified"
    if not tasks:
        task_text = "No saved task list"
    attention = (
        f"{len(view['questions'])} {'question' if len(view['questions']) == 1 else 'questions'} to answer"
        if view.get("questions")
        else "Plan approval needed"
        if view.get("status") == "AWAITING_GOAL_APPROVAL"
        else "Review requested in chat"
        if mapping(view.get("human_escalation")).get("scope") == "human_review"
        else "Decision requested in chat"
        if view.get("human_request_authorized")
        else "No decision requested"
    )
    problem_label = "problem" if len(problems) == 1 else "problems"
    line = (
        f"{task_text} · {passed} of {len(requirements)} requirements checked"
        + (f" · {failed} failed" if failed else "")
        + f" · {unknown} unchecked"
        + f" · {len(problems)} open {problem_label} · {attention}"
    )
    return {
        "tasks": tasks,
        "task_progress_known": known,
        "task_label": task_text,
        "requirements": requirements,
        "problems": problems,
        "line": line,
        "attention": attention,
        "counts": {
            "done": done,
            "tasks": len(tasks),
            "checked": passed,
            "failed": failed,
            "unchecked": unknown,
            "requirements": len(requirements),
            "problems": len(problems),
        },
        "verification_stale": stale,
        "verification_reasons": verification.get("reasons") or [],
        "verification_freshness": verification.get("freshness", "not_inspected"),
    }
