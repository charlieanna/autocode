"""Parallel Builder quota handoff across the parent and isolated worker checkpoint.

A worker stopped on its model, by quota or by its provider's content filter (quota_route.STATUSES),
hands the parent the same route-terra question a serial Builder asks (#184, #458, #465). The answer
reruns only that member, on the named model. Nothing reruns the model a content filter refused.
"""
from pathlib import Path

try:
    from . import autocode_quota_route as quota_route, autocode_util as util, autocode_roles as roles
    from . import autocode_provider_refusal as provider_refusal, autocode_support as support
except ImportError:
    import autocode_quota_route as quota_route
    import autocode_util as util
    import autocode_roles as roles
    import autocode_provider_refusal as provider_refusal
    import autocode_support as support


def payload(state, directory, workspace):
    record = state.get("active_stage") or {}
    if not record or not record.get("events"):
        return None
    return {"role": "terra", "milestone_id": state["current_task"]["milestone_id"],
            "run_dir": str(directory), "workspace": str(workspace),
            "model": quota_route._launched_model(record),
            "events": record["events"], "attempt_id": quota_route._attempt_id(record)}


def stop(state, row, result, directory):
    """The parent's stop for a batch member its model stopped, or None for any other result.

    A refusal is named from the worker's own events, never from its saved reason: after a restart that
    reason gives a command only the worker takes (--abandon-stage of its own attempt).
    """
    worker, status, milestone = result.get("quota_worker"), result.get("status"), row["milestone_id"]
    if not worker or status not in quota_route.STATUSES:
        return None
    if status == quota_route.QUOTA_STATUS:
        reason = f"PAUSED_BUDGET: Builder {milestone} stopped on quota"
    else:
        job = roles.screen_name("terra", state)
        reason = f"Milestone {milestone}: " + (
            provider_refusal.explain(support.events(worker["events"]), job=job, model=worker.get("model"))
            or f"{job}: the provider's content filter refused the response")
    error = util.Paused(status, f"{reason}; {directory}")
    error.quota_worker = worker
    return error


def stopped(state, error):
    worker = getattr(error, "quota_worker", None)
    if not worker or error.status not in quota_route.STATUSES:
        return None
    return {**worker, "stage": "terra", "pause_status": error.status, "active": True}


def question(state, attempt, *, family=None, **options):
    """quota_route.question for one milestone's Builder; ``options`` (cross_check, configured_tool) pass through.

    Its answer is judged against the model this member ran on (a sibling's answer may already have moved
    the parent's route) and, given ``family``, by validate_model; the models it lists are judged the same way.
    """
    rule = (lambda model: validate_model(model, attempt, family)) if family else None
    asked = quota_route.question(state, attempt, current=attempt.get("model"), rule=rule, **options)
    milestone = attempt["milestone_id"]
    asked["job"] += f" (milestone {milestone})"
    asked["question"] = asked["question"].replace("Builder", asked["job"], 1)
    asked["why"] = f"Milestone {milestone}: " + asked["why"]
    return asked


def current(state, origin):
    """Find only the stopped member of this exact BUILDING batch."""
    worker = (origin or {}).get("quota_worker") or {}
    batch = state.get("orchestration_batch") or {}
    if batch.get("status") != "BUILDING" or state.get("next_stage") != "orchestrator":
        return None
    for row in batch.get("workers", []):
        if (row.get("milestone_id") == worker.get("milestone_id")
                and row.get("run_dir") == worker.get("run_dir")
                and row.get("workspace") == worker.get("workspace")
                and row.get("status") in quota_route.STATUSES
                and row.get("status") == worker.get("pause_status", quota_route.QUOTA_STATUS)):
            return row, worker
    return None


def refused_retry(row, result):
    """Why --retry-builder may not rerun this batch member, or None.

    A member its provider's content filter refused reruns only on another model: the one a person names
    in answer to its route-terra question, which moves its saved route (#465). A quota stop keeps #458's
    explicit same-model retry.
    """
    worker = result.get("quota_worker")
    if result.get("status") != quota_route.REFUSAL_STATUS or not worker:
        return None
    try:
        routed = util.read(Path(row["run_dir"]) / "state.json")["settings"]["roles"]["terra"].get("model")
    except (OSError, ValueError, KeyError, TypeError):
        routed = None  # unreadable: never assume the route moved
    if worker.get("model") and routed and routed != worker["model"]:
        return None
    return (f"Builder {row['milestone_id']} was refused by its provider's content filter on "
            f"{worker.get('model') or 'its model'}, and the same model is likely to refuse it again, so "
            "--retry-builder does not rerun it there. It continues on the model a person names in answer "
            "to its route-terra question.")


def validate_model(model, worker, family):
    previous = worker.get("model")
    if previous and family(model) == family(previous):
        raise ValueError(f"The producing worker ran on {previous}; choose another model family")


def assign_child(row, worker, model, *, abandon):
    """Archive the uncertain attempt and mirror the assigned route while holding the child lock."""
    workspace, directory = Path(row["workspace"]), Path(row["run_dir"])
    with util.workspace_lock(workspace):
        child = util.read(directory / "state.json")
        active = child.get("active_stage") or {}
        if (child.get("parent_batch") is None or child.get("current_task") != row["task"]
                or quota_route._attempt_id(active) != worker.get("attempt_id")
                or quota_route._launched_model(active) != worker.get("model")):
            raise ValueError("The quota-stopped Builder attempt is no longer current")
        abandon(child, directory, workspace, worker["attempt_id"])
        child["settings"]["roles"]["terra"]["model"] = model
        child.setdefault("sessions", {}).pop("terra", None)
        util.atomic_json(directory / "state.json", child)
    row["retry_requested"] = True
