"""Parallel Builder quota handoff across the parent and isolated worker checkpoint."""
from pathlib import Path

try:
    from . import autocode_quota_route as quota_route, autocode_util as util
except ImportError:
    import autocode_quota_route as quota_route
    import autocode_util as util


def payload(state, directory, workspace):
    record = state.get("active_stage") or {}
    if not record or not record.get("events"):
        return None
    return {"role": "terra", "milestone_id": state["current_task"]["milestone_id"],
            "run_dir": str(directory), "workspace": str(workspace),
            "model": quota_route._launched_model(record),
            "events": record["events"], "attempt_id": quota_route._attempt_id(record)}


def stopped(state, error):
    worker = getattr(error, "quota_worker", None)
    if not worker or error.status != quota_route.QUOTA_STATUS:
        return None
    return {**worker, "stage": "terra", "pause_status": quota_route.QUOTA_STATUS, "active": True}


def question(state, attempt):
    asked = quota_route.question(state, attempt)
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
                and row.get("status") == quota_route.QUOTA_STATUS):
            return row, worker
    return None


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
