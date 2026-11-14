"""``--stage-model ROLE MODEL EFFORT``: change one stage's route on a started run (a step of autocode_run_actions.handle).

The status view's ``stage_models`` names the list a client shows (autocode_run_stage_models) and each
stage's ``set`` array names this flag. The model must pass the launch rules a resumed run's model
flag gets: the engine's format, the backend's own listing, a checker's independence from the stage
it checks, and a level the model declares. An accepted change is saved as a recorded
``route_assignment`` (via ``stage_models``) and drops the stage's session, so its next launch runs
the new route fresh. It saves without launching anything and never changes the run's status.

Like every step of autocode_run_actions.handle, it takes the invocation's arguments and ``decided``,
returns an exit code to stop or None to go on, and calls the runner's own functions as runner.X.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_quota_route as quota_route
    from . import autocode_role_transport as role_transport
    from . import autocode_route_fit as route_fit
    from . import autocode_run_stage_models as stage_models
except ImportError:
    import autocode_quota_route as quota_route
    import autocode_role_transport as role_transport
    import autocode_route_fit as route_fit
    import autocode_run_stage_models as stage_models


def apply(runner, args, parser, state: RunState, state_path, run_dir, workspace, decided):
    """Save ``--stage-model ROLE MODEL EFFORT`` on the run, or refuse it; None when the flag is absent."""
    picked = getattr(args, "stage_model", None)
    if not picked:
        return None
    role, model, effort = picked
    candidate = copy.deepcopy(state)
    try:
        problem = stage_models.refusal(candidate, role, model, effort)
        if problem:
            raise ValueError(problem)
        level = stage_models.effort_level(effort)
        _check_level(candidate, role, model, effort, level)
        quota_route.validate(
            candidate,
            role,
            model,
            configured_tool=getattr(runner.opencode, "CONFIGURED", False),
            cross_check=quota_route.cross_model_check(role),
            job=stage_models.label(role),
            allow_unchanged=True,
        )
        if quota_route.engine(candidate["settings"], role) == "opencode":
            try:
                facade = role_transport.adapter(candidate["settings"], role, runner.opencode, workspace)
                facade.check_models({role: {"model": model}}, workspace)
            except RuntimeError as error:
                raise ValueError(str(error)) from None
        if runner.interventions.pending(run_dir):
            raise ValueError("Apply the queued intervention before changing a stage's model")
        previous_settings = copy.deepcopy(candidate["settings"])
        before = candidate["settings"]["roles"][role].get("reasoning_effort")
        record = quota_route.assign(
            candidate,
            role,
            model,
            at=runner.now(),
            via=stage_models.VIA,
            attempt={"pause_status": candidate.get("status"), "role": role},
            effort=level,
        )
        event = candidate["user_events"][-1]
        candidate["settings"].setdefault("route_origins", {})[role] = route_fit.USER_EXPLICIT
        if level is None:
            # The model's own level, sent as no level at all: the person's choice, never refitted.
            candidate["settings"]["roles"][role]["reasoning_effort"] = None
            candidate["settings"].setdefault("effort_origins", {})[role] = route_fit.USER_EXPLICIT
            (candidate["settings"].get("effort_fits") or {}).pop(role, None)
            if candidate["settings"].get("effort_fits") == {}:
                candidate["settings"].pop("effort_fits")
            event["effort"] = {"from": before, "to": None, "named": True}
            for child_role, extra in quota_route.sync_inherited_routes(candidate, role).items():
                inherited = event.setdefault("inherited_routes", {})
                if child_role in inherited:
                    inherited[child_role]["to"] = extra["to"]
                else:
                    inherited[child_role] = extra
        settings_change = quota_route._flag_provenance(previous_settings, candidate["settings"], role)
        if settings_change:
            event["settings_change"] = settings_change
        runner.commit_user_action(state, candidate, run_dir)
    except (ValueError, KeyError) as error:
        return runner.status_command.reject_input(runner, state, workspace, run_dir, error)
    print(
        f"The {stage_models.label(role)} now runs {model} at {effort} in this run (was {record['from']}); "
        "its next launch uses it. Saved; no agent launched."
    )
    return 0


def _check_level(state: RunState, role: str, model: str, effort: str, level: str | None) -> None:
    """Refuse a level outside AutoCode's scale, or one ``model``'s listing does not declare."""
    if level is None:
        return
    if level not in route_fit.SCALE:
        raise ValueError(f"{effort} is not a reasoning level; use one of {', '.join(route_fit.SCALE)}")
    trial = copy.deepcopy(state.get("settings") or {})
    trial["roles"][role].update(model=model, reasoning_effort=level)
    trial.setdefault("effort_origins", {})[role] = route_fit.USER_EXPLICIT
    try:
        route_fit.fit(trial, role)
    except route_fit.EffortUnsupported as error:
        raise ValueError(f"{model} takes {', '.join(error.declared)}, not {effort}") from None
