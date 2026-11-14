"""Switch a stopped model's role to its next eligible route without asking the person.

On a quota, content-filter, context-window or output-limit stop (``quota_route.STATUSES``),
AutoCode moves the stopped stage, report repair, workflow job or parallel Builder worker
to the next eligible model on its own (``autocode_route_pool``) — keeping cross-model
verifier independence across both configured routes and already-produced work — and pauses
to ask the person only when no eligible model remains or automatic recoveries are spent.
``--single-model`` and ``--no-auto-select`` opt out.

Every automatic move records the same ``route_assignment`` event (``actor="runner"``) that
a person's answer records, so route records, held recovery bindings and final reports read
the switch uniformly. Imports only lower-level modules; the runtime is passed in.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_model_identity as model_identity
    from . import autocode_quota_route as quota_route
    from . import autocode_route_pool as route_pool
    from . import autocode_run_status as run_status
    from . import autocode_support as support
    from . import autocode_verifier_independence as independence
    from . import autocode_worker_quota as worker_quota
except ImportError:
    import autocode_model_identity as model_identity
    import autocode_quota_route as quota_route
    import autocode_route_pool as route_pool
    import autocode_run_status as run_status
    import autocode_support as support
    import autocode_verifier_independence as independence
    import autocode_worker_quota as worker_quota

_PLANNING_STAGES = frozenset({"requirements_gather", "draft_plan", "review_plan", "revise_plan", "finalize_plan"})


def billing(selection, model):
    """How the saved permitted set says ``model`` bills, or None when it does not say."""
    wanted = model_identity.identity(model)
    for candidates in ((selection or {}).get("candidates") or {}).values():
        for candidate in candidates:
            if model_identity.identity(candidate.get("model")) == wanted and candidate.get("billing"):
                return candidate["billing"]
    return None


def _guard_recovery_budget(runtime, state: RunState, candidate: RunState) -> bool:
    guard = getattr(runtime, "timeout_recovery_guard", None)
    count = getattr(runtime, "count_automatic_recovery", None)
    try:
        if callable(guard):
            guard(state)
    except support.Paused:
        return False
    if callable(count):
        count(candidate)
    return True


def _apply_step(
    candidate: RunState,
    step: dict,
    *,
    at: str,
    attempt: dict,
    current: str | None = None,
    selection: dict,
    prior_routes: list[list[str]] | None = None,
    paired_with: str | None = None,
) -> tuple[dict, bool, str | None, str | None]:
    settings = candidate["settings"]
    role = step["role"]
    role_dict = settings["roles"][role]
    had_effort = "reasoning_effort" in role_dict
    old_effort = role_dict.get("reasoning_effort")
    sel_routes = (settings.get("route_selection") or {}).get("routes")
    had_sel = isinstance(sel_routes, dict) and role in sel_routes
    old_sel = copy.deepcopy(sel_routes[role]) if isinstance(sel_routes, dict) and role in sel_routes else None
    before_model = current or role_dict.get("model")

    if step.get("levels") is not None:
        settings.setdefault("declared_variants", {})[step["model"]] = list(step["levels"])

    quota_route.assign(
        candidate,
        role,
        step["model"],
        at=at,
        via="runner",
        attempt=attempt,
        current=current,
        actor="runner",
    )
    event = candidate["user_events"][-1]
    if prior_routes is not None:
        event["prior_routes"] = prior_routes
    if paired_with is not None:
        event["paired_partner"] = True
        event["paired_with"] = paired_with

    was = billing(selection, before_model)
    now = step.get("billing") if selection.get("candidates") else None
    if now is None:
        now = billing(selection, step["model"])
    changed = bool(was and now and was != now)
    if changed and paired_with is None:
        event["billing"] = {"from": was, "to": now}

    if step.get("has_selector_effort"):
        role_dict["reasoning_effort"] = step.get("effort")

    if step.get("update_selection") and step.get("selection_route") is not None:
        settings["route_selection"]["routes"][role] = step["selection_route"]
        event["route_selection_before"] = {
            "present": had_sel,
            "from": old_sel,
            "to": copy.deepcopy(step["selection_route"]),
        }

    if (("reasoning_effort" in role_dict), role_dict.get("reasoning_effort")) != (had_effort, old_effort):
        event["runner_effort_before"] = {
            "present": had_effort,
            "from": old_effort,
            "to": role_dict.get("reasoning_effort"),
        }

    return event, changed, was, now


def _apply_plan(
    candidate: RunState,
    plan: dict,
    *,
    at: str,
    attempt: dict,
    dead: str,
    selection: dict,
) -> tuple[dict, bool, str | None, str | None]:
    prior = route_pool.routes_signature(candidate["settings"].get("roles") or {})
    primary = plan["primary"]
    primary_event, changed, was, now = _apply_step(
        candidate,
        primary,
        at=at,
        attempt=attempt,
        current=dead,
        selection=selection,
        prior_routes=prior,
    )
    partner_attempt = {
        "stage": attempt.get("stage"),
        "attempt_id": attempt.get("attempt_id"),
        "pause_status": attempt.get("pause_status"),
        "events": attempt.get("events"),
    }
    for partner in plan.get("partners") or ():
        _apply_step(
            candidate,
            partner,
            at=at,
            attempt=partner_attempt,
            selection=selection,
            paired_with=primary["role"],
        )
    return primary_event, changed, was, now


def _recover_worker(
    runtime,
    state: RunState,
    run_dir,
    workspace,
    error,
    worker_stopped: dict,
    *,
    offers_fn=None,
    stages_file=None,
) -> bool:
    if not worker_quota.at_checkpoint(state):
        return False
    if state.get("pause_requested") or (Path(run_dir) / "pause-requested").exists():
        return False
    dead = worker_stopped.get("model")
    milestone = worker_stopped.get("milestone_id")
    if not dead or not milestone:
        return False
    failed = [
        event.get("from")
        for event in state.get("user_events", [])
        if event.get("kind") == quota_route.KIND
        and event.get("actor") == "runner"
        and event.get("milestone_id") == milestone
    ]
    plan = route_pool.choose(
        runtime,
        state,
        workspace,
        "builder",
        dead,
        error.status,
        worker=worker_stopped,
        failed_for_role=failed,
        offers_fn=offers_fn,
        stages_file=stages_file,
    )
    if plan is None:
        return False
    candidate = copy.deepcopy(state)
    row = next(
        (
            item
            for item in (candidate.get("orchestration_batch") or {}).get("workers", [])
            if item.get("milestone_id") == milestone
        ),
        None,
    )
    if row is None or not _guard_recovery_budget(runtime, state, candidate):
        return False
    selection = (state.get("settings") or {}).get("route_selection") or {}
    primary_event, changed, was, now = _apply_plan(
        candidate, plan, at=runtime.now(), attempt=worker_stopped, dead=dead, selection=selection
    )
    primary_event["milestone_id"] = milestone
    if independence.violations(candidate["settings"]):
        return False
    new_model = plan["primary"]["model"]
    new_effort = candidate["settings"]["roles"]["builder"].get("reasoning_effort")
    sync_effort = "runner_effort_before" in primary_event or "effort" in primary_event
    try:
        worker_quota.assign_child(
            row,
            worker_stopped,
            new_model,
            abandon=lambda child, directory, work, aid: runtime.abandon_stage(
                child, directory, work, aid, actor="runner"
            ),
            effort=new_effort,
            sync_effort=sync_effort,
        )
    except ValueError:
        return False
    run_status.running(candidate, state.get("phase") or "EXECUTING", next_stage="orchestrator")
    runtime.write_json(Path(run_dir) / "state.json", candidate)
    cast(dict, state).clear()
    state.update(candidate)
    account = plan["primary"].get("account") or "the run's tool"
    change = f"; it bills {now} where {dead} was {was}" if changed else ""
    print(
        f"Builder (milestone {milestone}): {dead} stopped ({error.status}); "
        f"switched to {new_model} on {account} without asking{change}",
        flush=True,
    )
    return True


def recover_job(
    runtime,
    launch,
    state: RunState,
    run_dir,
    workspace,
    *,
    reroute,
    offers_fn=None,
    stages_file=None,
) -> bool:
    """Switch a model-stopped workflow job and authorize its one exact retry on the new route."""
    owner_runtime = launch or runtime
    if getattr(owner_runtime, "native_calls", None) is not None:
        return False
    failure = state.get("job_failure") or {}
    pause_status = failure.get("pause_status")
    route_question = failure.get("route")
    if (
        pause_status not in quota_route.STATUSES
        or failure.get("unrestored")
        or not failure.get("source_identity")
        or not isinstance(route_question, dict)
    ):
        return False
    if state.get("pause_requested") or (Path(run_dir) / "pause-requested").exists():
        return False
    pause_intent = state.get("pause_intent") or {}
    if pause_intent and not pause_intent.get("acknowledged_at"):
        return False
    role = route_question.get("route_role")
    dead = route_question.get("stopped_model")
    if role not in quota_route.ROUTABLE or not dead:
        return False
    failed = [
        event.get("from")
        for event in state.get("user_events", [])
        if event.get("kind") == quota_route.KIND
        and event.get("role") == role
        and event.get("actor") == "runner"
        and not event.get("paired_partner")
    ]
    plan = route_pool.choose(
        owner_runtime,
        state,
        workspace,
        role,
        dead,
        pause_status,
        workflow_job=True,
        failed_for_role=failed,
        offers_fn=offers_fn,
        stages_file=stages_file,
    )
    if plan is None:
        return False
    candidate = copy.deepcopy(state)
    if not _guard_recovery_budget(owner_runtime, state, candidate):
        return False
    now_fn = getattr(owner_runtime, "now", None) or getattr(runtime, "now", None)
    timestamp = now_fn() if callable(now_fn) else ""
    attempt = {
        "role": role,
        "stage": failure.get("stage"),
        "attempt_id": failure.get("attempt_id"),
        "pause_status": pause_status,
        "events": (candidate.get("recovery_context") or {}).get("events"),
    }
    selection = (state.get("settings") or {}).get("route_selection") or {}
    primary_event, changed, was, now = _apply_plan(
        candidate, plan, at=timestamp, attempt=attempt, dead=dead, selection=selection
    )
    if independence.violations(candidate["settings"]):
        return False
    try:
        token = reroute(owner_runtime, candidate, run_dir, workspace, copy.deepcopy(primary_event))
    except ValueError:
        return False
    stage_name = candidate["job_failure"]["stage"]
    candidate["job_retry_authorization"] = {"token": token, "stage": stage_name, "consumed": False}
    run_status.running(candidate, "EXECUTING", next_stage=stage_name)
    cast(dict, state).clear()
    state.update(candidate)
    job = quota_route.roles.screen_name(stage_name, state)
    account = plan["primary"].get("account") or "the run's tool"
    change = f"; it bills {now} where {dead} was {was}" if changed else ""
    print(
        f"{job}: {dead} stopped ({pause_status}); switched to {plan['primary']['model']} on {account} without asking{change}",
        flush=True,
    )
    return True


def recover(runtime, state: RunState, run_dir, workspace, error, *, offers_fn=None, stages_file=None) -> bool:
    """One quota-class stop, one automatic switch; False leaves the pause standing."""
    if getattr(runtime, "native_calls", None) is not None:
        return False
    if error.status not in quota_route.STATUSES:
        return False
    worker_stopped = worker_quota.stopped(state, error)
    if worker_stopped is not None:
        return _recover_worker(
            runtime,
            state,
            run_dir,
            workspace,
            error,
            worker_stopped,
            offers_fn=offers_fn,
            stages_file=stages_file,
        )
    stopped = quota_route.stopped_attempt(state, failure_status=support.failure_status)
    if not stopped or not stopped.get("active") or stopped.get("workflow_job"):
        return False
    role = stopped.get("role")
    if role not in quota_route.ROLES:
        return False
    route = ((state.get("settings") or {}).get("roles") or {}).get(role) or {}
    dead = stopped.get("model") or route.get("model")
    if not dead:
        return False
    failed = [
        event.get("from")
        for event in state.get("user_events", [])
        if event.get("kind") == quota_route.KIND
        and event.get("role") == role
        and event.get("actor") == "runner"
        and not event.get("paired_partner")
    ]
    plan = route_pool.choose(
        runtime,
        state,
        workspace,
        role,
        dead,
        error.status,
        failed_for_role=failed,
        offers_fn=offers_fn,
        stages_file=stages_file,
    )
    if plan is None:
        return False
    if state.get("pause_requested") or (Path(run_dir) / "pause-requested").exists():
        return False
    # Use the existing abandonment boundary: it proves the process stopped, accounts
    # usage, retains partial work and durably archives the failed attempt. Include
    # the replacement and failure history in that save, so a crash cannot retry an
    # exhausted route or discard its evidence. Work on a copy until it succeeds.
    candidate = copy.deepcopy(state)
    if not _guard_recovery_budget(runtime, state, candidate):
        return False
    selection = (state.get("settings") or {}).get("route_selection") or {}
    _primary_event, changed, was, now = _apply_plan(
        candidate, plan, at=runtime.now(), attempt=stopped, dead=dead, selection=selection
    )
    if independence.violations(candidate["settings"]):
        return False
    runtime.abandon_stage(candidate, Path(run_dir), Path(workspace), stopped["attempt_id"], actor="runner")
    phase = state.get("phase") or "IMPLEMENTING"
    if phase == "REPORT_REPAIR" and "pending_report_repair" not in candidate:
        phase = "PLANNING" if candidate.get("next_stage") in _PLANNING_STAGES else "IMPLEMENTING"
    run_status.running(candidate, phase)
    runtime.write_json(Path(run_dir) / "state.json", candidate)
    cast(dict, state).clear()
    state.update(candidate)
    job = quota_route.roles.screen_name(stopped.get("stage") or role, state)
    switched = plan["primary"]
    account = switched.get("account") or "the run's tool"
    change = f"; it bills {now} where {dead} was {was}" if changed else ""
    paired_note = ""
    if plan.get("partners"):
        moved = ", ".join(
            f"{quota_route.roles.screen_name(item['role'], state)} -> {item['model']}" for item in plan["partners"]
        )
        paired_note = f" (paired swap: {moved})"
    print(
        f"{job}: {dead} stopped ({error.status}); switched to {switched['model']} on {account} without asking{change}{paired_note}",
        flush=True,
    )
    return True
