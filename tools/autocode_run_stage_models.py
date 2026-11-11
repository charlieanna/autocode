"""Change the model and reasoning level a stage runs for one started run (owner request 2026-11-10).

A new task takes the person's saved stage choices (autocode_stage_choices); a task already running
keeps the routes it started with, so changing ``autocode stages`` never reaches a chat that has
started. This module lets the person change that chat's own routes, one stage at a time, between
stages:

- ``offer`` is the status view's ``stage_models``: whether the run's models can be changed now, why
  not, and the read-only command that lists them (``autocode stages list --run-dir RUN --json``);
- ``view`` is that list: the run's current route for each stage, the models its backend offers and
  the ``set`` argument array a client fills, ``--stage-model ROLE MODEL EFFORT`` on the run;
- ``refusal`` and ``effort`` are what the ``--stage-model`` action (autocode_action_stage_model)
  checks before it saves the route as a recorded ``route_assignment`` (autocode_quota_route.assign).

A change is saved only between stages: never while a stage attempt or runner check is open, and
never while the run is RUNNING (the run lock also refuses a second writer then). The next launch
of that stage uses the new route on a fresh session. Nothing here launches a model.

Layer: domain helper over saved run state and autocode_stage_choices. It never imports the runner.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

FLAG = "--stage-model"
VIA = "stage_models"
BUSY = "A stage is working. Pause the chat, or wait until it asks you something, to change its models."
NO_ROUTES = "This run has no saved routes yet; its models are chosen when it starts."


def _choices():
    try:
        from . import autocode_stage_choices as stage_choices
    except ImportError:
        import autocode_stage_choices as stage_choices
    return stage_choices


def backend(settings: dict) -> str:
    """The stage-choice backend a started run runs on, as a new run's choices are keyed (native_invocation)."""
    if settings.get("engine") == "codex":
        return "codex"
    return settings.get("provider") or "opencode"


def routes(state: RunState) -> dict:
    """``{role: {"model", "effort"}}`` for each stage a person chooses that the run has a route for."""
    saved = (state.get("settings") or {}).get("roles") or {}
    found = {}
    for role in _choices().ORDER:
        route = saved.get(role)
        if isinstance(route, dict) and isinstance(route.get("model"), str) and route["model"]:
            found[role] = {"model": route["model"], "effort": route.get("reasoning_effort")}
    return found


def busy(state: RunState) -> str | None:
    """Why the run's models cannot be changed now, or None when it is between stages."""
    if not routes(state):
        return NO_ROUTES
    if state.get("status") == "RUNNING" or state.get("active_stage") or state.get("active_runner_check"):
        return BUSY
    return None


def offer(state: RunState, *, run_dir=None) -> dict | None:
    """The status view's ``stage_models``; None for a view with no run directory or a run with no routes."""
    if run_dir is None or not routes(state):
        return None
    reason = busy(state)
    return {
        "changeable": reason is None,
        "reason": reason,
        "next_command": ["stages", "list", "--run-dir", str(run_dir), "--json"],
    }


def view(state: RunState, run_dir, workspace, *, resolve=None, discover=None) -> dict:
    """The run's stage list: autocode_stage_choices.run_view, with the run and whether it can change now."""
    where = ["--run-dir", str(run_dir), "--workspace", str(workspace)]
    listed = _choices().run_view(
        backend(state.get("settings") or {}),
        routes(state),
        lambda role: [*where, FLAG, role, "MODEL", "EFFORT"],
        workspace,
        resolve=resolve,
        discover=discover,
    )
    reason = busy(state)
    return {**listed, "run_dir": str(run_dir), "changeable": reason is None, "reason": reason}


def refusal(state: RunState, role: str, model: str, effort: str) -> str | None:
    """Why ``--stage-model ROLE MODEL EFFORT`` cannot be saved on this run, or None.

    The launch rules (engine format, the backend's listing, a checker's independence) are checked by
    the action with the run's own provider; this is what the saved state alone decides.
    """
    current = routes(state)
    if role not in current:
        named = ", ".join(current) or "none"
        return f"This run has no {role} stage to change; its stages are {named}"
    reason = busy(state)
    if reason:
        return reason
    if not model or any(character.isspace() for character in model):
        return "Name the model without spaces"
    if current[role]["model"] == model and current[role]["effort"] == effort_level(effort):
        return f"The {_choices().LABELS[role]} already runs {model} at {effort}"
    return None


def effort_level(effort: str) -> str | None:
    """The saved level for a named one: None for the model's own default."""
    return None if effort == _choices().OWN_DEFAULT else effort


def label(role: str) -> str:
    return _choices().LABELS[role]
