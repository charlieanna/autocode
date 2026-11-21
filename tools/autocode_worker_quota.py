"""Parallel Builder quota handoff across the parent and isolated worker checkpoint.

A worker stopped on its model, by quota or by its provider's content filter (quota_route.STATUSES),
hands the parent the same route-builder question a serial Builder asks (#184, #458, #465). The answer
reruns only that member, on the named model. Nothing reruns the model a content filter refused. Once
the question was answered without a model, --retry-builder is that member's one control
(autocode_member_stop, #541).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_member_stop as member_stop
    from . import autocode_quota_route as quota_route
    from . import autocode_status
    from . import autocode_util as util
    from . import autocode_worker_retry_policy as retry_policy
except ImportError:
    import autocode_member_stop as member_stop
    import autocode_quota_route as quota_route
    import autocode_status
    import autocode_util as util
    import autocode_worker_retry_policy as retry_policy


def payload(state: RunState, directory, workspace):
    record = state.get("active_stage") or {}
    if not record or not record.get("events"):
        return None
    return {
        "role": "builder",
        "milestone_id": cast(dict, state["current_task"])["milestone_id"],
        "run_dir": str(directory),
        "workspace": str(workspace),
        "model": quota_route._launched_model(record),
        "events": record["events"],
        "attempt_id": quota_route._attempt_id(record),
    }


stop = retry_policy.stop


def stopped(state: RunState, error):
    worker = getattr(error, "quota_worker", None)
    if not worker or error.status not in quota_route.STATUSES:
        return None
    return {**worker, "stage": "builder", "pause_status": error.status, "active": True}


def question(state: RunState, attempt, *, family=None, **options):
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


current = member_stop.current  # The stopped member a request's origin names, for the answer path.


def at_checkpoint(state: RunState):
    """Whether the parent is at the parallel batch's build checkpoint.

    Its last stage records there are copied member attempts, never legacy parent decisions.
    """
    batch = state.get("orchestration_batch") or {}
    return batch.get("status") == "BUILDING" and state.get("next_stage") == "orchestrator"


def asked_again(state: RunState, error, origin):
    """Restore a withdrawn request's member payload only while that exact member stop is current."""
    worker = (origin or {}).get("quota_worker")
    if (
        not worker
        or getattr(error, "quota_worker", None)
        or error.status != origin.get("pause_status")
        or current(state, origin) is None
    ):
        return error
    try:
        result = util.read(Path(worker["run_dir"]) / "result.json")
        saved = result.get("quota_worker") or {}
        # A later attempt can stop with the same status at the same location. Its result must
        # still identify this attempt, rather than reviving the previous member's route advice.
        if (
            not worker.get("attempt_id")
            or result.get("status") != error.status
            or any(
                saved.get(key) != worker.get(key)
                for key in ("role", "milestone_id", "run_dir", "workspace", "model", "events", "attempt_id")
            )
        ):
            return error
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return error
    error.quota_worker = worker
    return error


ROUTE = retry_policy.ROUTE
_builder_route = retry_policy._builder_route
retry_route_refusal = retry_policy.retry_route_refusal
asked_member = retry_policy.asked_member
refused_retry = retry_policy.refused_retry


def validate_model(model, worker, family):
    previous = worker.get("model")
    if previous and family(model) == family(previous):
        raise ValueError(f"The producing worker ran on {previous}; choose another model family")


def assign_child(row, worker, model, *, abandon, effort=None, sync_effort=False):
    """Archive the uncertain attempt and mirror the assigned route while holding the child lock."""
    workspace, directory = Path(row["workspace"]), Path(row["run_dir"])
    with util.workspace_lock(workspace):
        child = util.read(directory / "state.json")
        active = child.get("active_stage") or {}
        if (
            child.get("parent_batch") is None
            or child.get("current_task") != row["task"]
            or quota_route._attempt_id(active) != worker.get("attempt_id")
            or quota_route._launched_model(active) != worker.get("model")
        ):
            raise ValueError("The quota-stopped Builder attempt is no longer current")
        abandon(child, directory, workspace, worker["attempt_id"])
        child["settings"]["roles"]["builder"]["model"] = model
        if sync_effort:
            child["settings"]["roles"]["builder"]["reasoning_effort"] = effort
        child.setdefault("sessions", {}).pop("builder", None)
        autocode_status.persist(directory / "state.json", child)
    row["retry_requested"] = True
