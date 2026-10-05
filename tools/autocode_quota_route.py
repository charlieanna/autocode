"""A role's quota ran out: ask the person for a model, apply the one they name, record it (#184).

There is no fallback list, configuration table or automatic switch. A quota stop
(PAUSED_BUDGET) publishes one question, ``route-<role>``, which only a person can
answer: it has no proposed default and is never delegable. The answer is a model
for the same engine; it must pass the same format and cross-model rules a launch
applies. Applying it is a recorded ``route_assignment`` in ``user_events``.

Pure functions over the saved state. This module imports nothing from the runner:
the provider-error classifier (``failure_status``) and the cross-model rule
(``cross_check``) arrive as arguments, so the status view can read it too.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path

try:
    from . import autocode_roles as roles
except ImportError:
    import autocode_roles as roles

PREFIX = "route-"
CATEGORY = "quota"
QUOTA_STATUS = "PAUSED_BUDGET"
KIND = "route_assignment"
# Roles a person can route by flag on resume (autocode_args: --<role>-model).
ROLES = ("astra", "terra", "sol", "completion", "glm", "requirements", "resolver", "plan_reviewer")
_OPENCODE_MODEL = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]*/[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,120}")


def flag(role: str) -> str:
    return "--" + role.replace("_", "-") + "-model"


def engine(settings: dict, role: str) -> str:
    """The session engine a role runs on (autoplanner.engine_for, without importing the cycle)."""
    return ((settings or {}).get("roles") or {}).get(role, {}).get(
        "engine", (settings or {}).get("engine", "codex"))


def model_problem(role: str, model, *, role_engine: str, configured_tool: bool) -> str | None:
    """Why ``model`` cannot be saved for ``role`` on its engine, or None. One rule for configure and here."""
    label = role.title()
    if role_engine == "codex":
        if not isinstance(model, str) or "/" in model:
            return f"{label} uses a bare Codex model name on the Codex engine, e.g. gpt-5.6-sol"
        return None
    if not configured_tool:
        # Preserve OpenCode's catalogue identifier, not a Codex alias or a
        # provider whitelist. check_models verifies actual availability.
        if not isinstance(model, str) or not _OPENCODE_MODEL.fullmatch(model):
            return (f"{label} requires an OpenCode provider/model identifier; "
                    "saved session engines cannot be switched on resume")
        return None
    if not isinstance(model, str) or not model.strip() or any(char.isspace() for char in model):
        return f"{label} requires a model name from the provider config"
    return None


def _attempt_id(record: dict) -> str | None:
    if record.get("output") and isinstance(record.get("iteration"), int):
        return f"{record['iteration']:03d}/{Path(record['output']).stem}"
    return None


def _launched_model(record: dict) -> str | None:
    """The model the attempt was launched on, from its saved route or command (as autocode_status reads it)."""
    route = record.get("launch_route") or {}
    if route.get("model"):
        return str(route["model"])
    command = record.get("command") if isinstance(record.get("command"), list) else []
    for flag in ("--model", "-m"):
        if flag in command[:-1]:
            return str(command[command.index(flag) + 1])
    return None


def _routable(state: dict, record: dict) -> str | None:
    role = record.get("route_role") or record.get("role")
    configured = ((state.get("settings") or {}).get("roles") or {})
    if role in ROLES and isinstance(configured.get(role), dict) and not state.get("parent_run"):
        return role
    return None


def stopped_attempt(state: dict, *, failure_status) -> dict | None:
    """The attempt a quota stop left for one routable role, or None.

    Either the uncertain ``active_stage`` (``active`` True), or the attempt a person
    already set aside with --abandon-stage while nothing has run since.
    """
    active = state.get("active_stage") or {}
    record, is_active = (active, True) if active else (None, False)
    if record is None:
        latest = next((row for row in reversed(state.get("stages") or [])
                       if isinstance(row, dict) and not row.get("runner_owned")), None)
        if not latest or not latest.get("abandoned"):
            return None
        record = latest
    events = record.get("events")
    role = _routable(state, record)
    try:
        quota = bool(events) and Path(events).is_file() and failure_status(events) == QUOTA_STATUS
    except (OSError, ValueError, TypeError):
        quota = False
    if not role or not quota:
        return None
    return {"role": role, "stage": record.get("original_stage") or record.get("stage"),
            "attempt_id": _attempt_id(record), "events": events, "active": is_active,
            "model": _launched_model(record)}


def question(state: dict, attempt: dict) -> dict:
    """The quota question for ``attempt``: named by the job on screen, answered only by a person."""
    role = attempt["role"]
    job = roles.screen_name(attempt.get("stage") or role, state)
    settings = state.get("settings") or {}
    current = ((settings.get("roles") or {}).get(role) or {}).get("model")
    stopped_on = attempt.get("model") or current
    return {"id": PREFIX + role,
            "question": f"{job}'s quota is exhausted; name the model to continue on",
            "why": (f"The {job} stopped on {stopped_on}: its provider reported the quota, usage limit or "
                    "credits used up. AutoCode never switches models on its own. Name a model for the "
                    f"same engine ({engine(settings, role)}) that does not share its producer's or "
                    "checker's model family."),
            "options": [], "proposed_default": "", "kind": "decision", "category": CATEGORY,
            "delegable": False, "route_role": role, "job": job, "current_model": current,
            "engine": engine(settings, role)}


def advice(asked: dict, attempt_id: str | None) -> str:
    """The two commands the CLI accepts at this stop. Never names one it refuses (#288/#301)."""
    role = asked["route_role"]
    text = (f"To continue on another model, answer --answer {asked['id']}=MODEL --resolver-token TOKEN, "
            "then --resume-paused")
    if attempt_id:
        text += f"; or --abandon-stage {attempt_id}, then --resume-paused {flag(role)} MODEL"
    return text + "."


def option(asked: dict) -> str:
    return f"Name the model the {asked['job']} continues on with --answer {asked['id']}=MODEL"


def asked_route(questions, question_id: str) -> dict | None:
    """The published quota question ``question_id``, or None when the request asks no such thing."""
    return next((q for q in questions or () if isinstance(q, dict) and q.get("id") == question_id
                 and q.get("category") == CATEGORY and q.get("route_role") in ROLES
                 and question_id == PREFIX + q["route_role"]), None)


def parse_answer(answers, questions, origin: dict) -> tuple[dict, str]:
    """(question, model) from ``--answer route-<role>=MODEL`` at a quota stop; ValueError otherwise."""
    if (origin or {}).get("pause_status") != QUOTA_STATUS:
        raise ValueError("Only a quota stop is answered with a model; use --resolver-response for this request")
    if len(answers) != 1:
        raise ValueError("Answer the quota question on its own: --answer route-ROLE=MODEL")
    question_id, separator, model = answers[0].partition("=")
    asked = asked_route(questions, question_id)
    if not separator or asked is None:
        raise ValueError(f"{question_id} is not the quota question of the current request")
    model = model.strip()
    if not model:
        raise ValueError("Name the model to continue on: --answer route-ROLE=MODEL")
    return asked, model


def validate(state: dict, role: str, model: str, *, configured_tool: bool, cross_check) -> None:
    """Refuse a model a launch would refuse: wrong format for the engine, unchanged, or a cross-model clash."""
    settings = state.get("settings") or {}
    route = (settings.get("roles") or {}).get(role)
    if not isinstance(route, dict):
        raise ValueError(f"This run has no {role} route to assign")
    problem = model_problem(role, model, role_engine=engine(settings, role), configured_tool=configured_tool)
    if not problem and (not isinstance(model, str) or not model.strip() or any(c.isspace() for c in model)):
        problem = f"{role.title()} needs a model name without spaces"
    if problem:
        raise ValueError(problem)
    if route.get("model") == model:
        raise ValueError(f"The {role} route already uses {model}; name a different model")
    trial = {"settings": copy.deepcopy(settings)}
    trial["settings"]["roles"][role]["model"] = model
    try:
        cross_check(trial)
    except Exception as error:  # the rule raises Paused(PAUSED_CROSS_MODEL); a refused answer is input
        if getattr(error, "status", None) != "PAUSED_CROSS_MODEL":
            raise
        raise ValueError(str(error)) from None


def assign(state: dict, role: str, model: str, *, at: str, via: str, attempt: dict | None = None,
           request_id: str | None = None) -> dict:
    """Set the role's model, drop its session and append the route_assignment record; return the record."""
    settings = state["settings"]
    route = settings["roles"][role]
    record = {"kind": KIND, "actor": "user_cli", "at": at, "via": via, "role": role,
              "job": roles.screen_name((attempt or {}).get("stage") or role, state),
              "from": route.get("model"), "to": model, "engine": engine(settings, role),
              "stage": (attempt or {}).get("stage"), "attempt_id": (attempt or {}).get("attempt_id"),
              "pause_status": QUOTA_STATUS, "events": (attempt or {}).get("events")}
    if request_id:
        record["request_id"] = request_id
    route["model"] = model
    state.setdefault("sessions", {}).pop(role, None)
    state.setdefault("user_events", []).append(record)
    return copy.deepcopy(record)


def record_resume_change(state: dict, previous: dict, selected: dict, *, failure_status, at: str) -> list[dict]:
    """Record a --<role>-model change saved while that role is stopped on quota (resume path)."""
    attempt = stopped_attempt({**state, "settings": previous}, failure_status=failure_status)
    if not attempt:
        return []
    role = attempt["role"]
    before = ((previous.get("roles") or {}).get(role) or {}).get("model")
    after = ((selected.get("roles") or {}).get(role) or {}).get("model")
    if not after or before == after:
        return []
    record = {"kind": KIND, "actor": "user_cli", "at": at, "via": "resume_flag", "role": role,
              "job": roles.screen_name(attempt.get("stage") or role, state), "from": before, "to": after,
              "engine": engine(selected, role), "stage": attempt.get("stage"),
              "attempt_id": attempt.get("attempt_id"), "pause_status": QUOTA_STATUS,
              "events": attempt.get("events")}
    state.setdefault("user_events", []).append(record)
    return [copy.deepcopy(record)]


def routes(state: dict) -> dict:
    """{role: {model, engine}} for every configured role: the routes the next launch uses."""
    settings = state.get("settings") or {}
    return {role: {"model": config.get("model"), "engine": engine(settings, role)}
            for role, config in sorted((settings.get("roles") or {}).items()) if isinstance(config, dict)}


def assignments(state: dict) -> list[dict]:
    """Every recorded route assignment, oldest first."""
    return [copy.deepcopy(event) for event in state.get("user_events") or []
            if isinstance(event, dict) and event.get("kind") == KIND]
