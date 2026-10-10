"""Parallel Builder quota handoff across the parent and isolated worker checkpoint.

A worker stopped on its model, by quota or by its provider's content filter (quota_route.STATUSES),
hands the parent the same route-terra question a serial Builder asks (#184, #458, #465). The answer
reruns only that member, on the named model. Nothing reruns the model a content filter refused. Once
the question was answered without a model, --retry-builder is that member's one control
(autocode_member_stop, #541).
"""

from pathlib import Path

try:
    from . import autocode_member_stop as member_stop
    from . import autocode_provider_refusal as provider_refusal
    from . import autocode_quota_route as quota_route
    from . import autocode_roles as roles
    from . import autocode_support as support
    from . import autocode_util as util
except ImportError:
    import autocode_member_stop as member_stop
    import autocode_provider_refusal as provider_refusal
    import autocode_quota_route as quota_route
    import autocode_roles as roles
    import autocode_support as support
    import autocode_util as util


def payload(state, directory, workspace):
    record = state.get("active_stage") or {}
    if not record or not record.get("events"):
        return None
    return {
        "role": "terra",
        "milestone_id": state["current_task"]["milestone_id"],
        "run_dir": str(directory),
        "workspace": str(workspace),
        "model": quota_route._launched_model(record),
        "events": record["events"],
        "attempt_id": quota_route._attempt_id(record),
    }


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
            or f"{job}: the provider's content filter refused the response"
        )
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


current = member_stop.current  # The stopped member a request's origin names, for the answer path.


def at_checkpoint(state):
    """Whether the parent is at the parallel batch's build checkpoint.

    Its last stage records there are copied member attempts, never legacy parent decisions.
    """
    batch = state.get("orchestration_batch") or {}
    return batch.get("status") == "BUILDING" and state.get("next_stage") == "orchestrator"


def asked_again(state, error, origin):
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


ROUTE = ("engine", "model", "provider", "reasoning_effort")


def retry_route_refusal(state, selected, before, after, *, asked=None):
    """Why a member retry cannot use a Builder route changed only in its parent's settings.

    The child's saved route is changed by assign_child when its route-terra question is answered.
    A settings flag on the parent alone would otherwise silently rerun the child's previous route.
    """
    old, new = (((settings.get("roles") or {}).get("terra") or {}) for settings in (before, after))
    if all(old.get(key) == new.get(key) for key in ROUTE):
        return None
    rows = {row["milestone_id"]: row for row in (state.get("orchestration_batch") or {}).get("workers", [])}
    for mid in (mid for mid in selected if mid in rows):
        try:
            model = util.read(Path(rows[mid]["run_dir"]) / "state.json")["settings"]["roles"]["terra"].get("model")
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            model = None
        return (
            f"Builder {mid}'s retry runs on the Builder route its batch started it with"
            + (f" ({model})" if model else "")
            + ", so a Builder model, provider or reasoning effort given with --retry-builder would not reach it"
            + (
                "; it continues on another model named in answer to its route-terra question "
                "(--answer route-terra=MODEL)"
                if asked == mid and rows[mid].get("status") in quota_route.STATUSES
                else ""
            )
        )
    return None


def asked_member(state, public):
    """What the open request asks about: a member's milestone ID, "" for another request, None for none."""
    if not public and not state.get("pending_questions"):
        return None
    try:
        origin = state["resolver"]["human_escalations"][public["request_id"]]["identity"]["proposal"]["origin"]
        return origin["quota_worker"]["milestone_id"] if public["scope"] == "operational_exhaustion" else ""
    except (KeyError, TypeError):
        return ""


def refused_retry(state, row, result, *, asked):
    """The error --retry-builder raises for this batch member, or None when it may rerun it.

    A member its provider's content filter refused reruns only on another model: the one a person names
    in answer to its route-terra question, which moves its saved route (#465). While a request is open
    (``asked``, from asked_member) the retry is refused (ValueError) and points at that member's question
    only when it is the one asked. With none open (the request was answered with information or left
    paused, #541) the member's saved stop is collected again instead: the returned Paused carries it, so
    AutoResolver asks its route-terra question again, and nothing launches. That is the answered member's
    control (autocode_member_stop); another refused member is asked about after the answered member
    continues, whichever way it stopped. A quota stop keeps
    #458's explicit same-model retry.
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
    milestone = row["milestone_id"]
    first = (member_stop.answered(state) or ({},))[0].get("milestone_id")
    if asked is None and first in (None, milestone):
        return stop(state, row, result, Path(row["run_dir"]))
    if asked is None:
        then = (
            f"AutoCode continues from Builder {first}'s stop first, with --resume-paused --retry-builder "
            f"{first}, and asks which model this member continues on after it."
        )
    elif asked == milestone:
        then = (
            "It continues on the model a person names in answer to its open route-terra question "
            "(--answer route-terra=MODEL)."
        )
    else:
        then = (
            "AutoCode asks which model it continues on once the open request"
            + (f" about Builder {asked}" if asked else "")
            + " is answered."
        )
    return ValueError(
        f"Builder {milestone} was refused by its provider's content filter on "
        f"{worker.get('model') or 'its model'}, and the same model is likely to refuse it again, so "
        f"--retry-builder does not rerun it there. {then}"
    )


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
        if (
            child.get("parent_batch") is None
            or child.get("current_task") != row["task"]
            or quota_route._attempt_id(active) != worker.get("attempt_id")
            or quota_route._launched_model(active) != worker.get("model")
        ):
            raise ValueError("The quota-stopped Builder attempt is no longer current")
        abandon(child, directory, workspace, worker["attempt_id"])
        child["settings"]["roles"]["terra"]["model"] = model
        child.setdefault("sessions", {}).pop("terra", None)
        util.atomic_json(directory / "state.json", child)
    row["retry_requested"] = True
