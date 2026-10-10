"""A role's quota ran out: ask the person for a model, apply the one they name, record it (#184).

A provider's content-filter refusal (PAUSED_CONTENT_FILTER, autocode_provider_refusal)
is the same kind of stop: it is about the model, and the same model is likely to refuse again.
There is no fallback list, configuration table or automatic switch. Either stop
(PAUSED_BUDGET or PAUSED_CONTENT_FILTER) publishes one question, ``route-<role>``,
which only a person can answer: it has no proposed default and is never delegable.
A refusal's question names the run's other configured models that would pass the
launch rules, as advice, never as a default. The answer is a model
for the same engine; it must pass the same format and cross-model rules a launch
applies. Applying it is a recorded ``route_assignment`` in ``user_events``.

A workflow job (autocode_jobs.STAGES) stopped this way pauses under autocode_job_failure with
one exact retry bound to the run's configuration (#463). Its question is kept on that failure,
not published: it is answered with ``--answer route-<role>=MODEL --job-retry-token TOKEN``
(autocode_job_route), which issues a new exact-retry token for the named model. The Architect,
Analyst and Investigator routes have no --<role>-model flag, so only that answer routes them.

Pure functions over the saved state. This module imports nothing from the runner:
the provider-error classifier (``failure_status``) and the cross-model rule
(``cross_check``) arrive as arguments, so the status view can read it too.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import copy
import re
from pathlib import Path

try:
    from . import autocode_provider_refusal as provider_refusal
    from . import autocode_roles as roles
except ImportError:
    import autocode_provider_refusal as provider_refusal
    import autocode_roles as roles

PREFIX = "route-"
CATEGORY = "quota"  # a non-inferable question category (autocode_goals); both causes use it
QUOTA_STATUS = "PAUSED_BUDGET"
REFUSAL_STATUS = provider_refusal.STATUS
STATUSES = (QUOTA_STATUS, REFUSAL_STATUS)
_STOPPED = {QUOTA_STATUS: "stopped on quota", REFUSAL_STATUS: "its provider's content filter refused"}
KIND = "route_assignment"
# Roles a person can route by flag on resume (autocode_args: --<role>-model).
ROLES = ("astra", "terra", "sol", "completion", "glm", "requirements", "resolver", "plan_reviewer")
# Workflow-job routes a person can route only by answering the job's model question (#463): no
# flag sets them, so advice never names one. The stuck-stage Investigator's route
# (stuck_investigator) is not routable: investigate_stuck rebuilds it on every launch and releases
# it afterwards, so a model saved on it would not stick. Its stop keeps only the exact retry
# (--investigator-model pins the model of a later investigation).
JOB_ROLES = ("architect", "analyst", "investigator")
ROUTABLE = ROLES + JOB_ROLES
_JOB_PAUSES = ("PAUSED_JOB_FAILURE", "PAUSED_STAGE_ABANDONED")  # autocode_job_failure.PAUSES
_OPENCODE_MODEL = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]*/[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,120}")


def flag(role: str) -> str:
    return "--" + role.replace("_", "-") + "-model"


def engine(settings: dict, role: str) -> str:
    """The session engine a role runs on (autoplanner.engine_for, without importing the cycle)."""
    return ((settings or {}).get("roles") or {}).get(role, {}).get("engine", (settings or {}).get("engine", "codex"))


def model_problem(role: str, model, *, role_engine: str, configured_tool: bool, label: str | None = None) -> str | None:
    """Why ``model`` cannot be saved for ``role`` on its engine, or None. One rule for configure and here.

    ``label`` is the job a person sees (``Tester``); configure, which has no stage, falls back to the role.
    """
    label = label or role.title()
    if role_engine == "codex":
        if not isinstance(model, str) or "/" in model:
            return f"{label} uses a bare Codex model name on the Codex engine, e.g. gpt-5.6-sol"
        return None
    if not configured_tool:
        # Preserve OpenCode's catalogue identifier, not a Codex alias or a
        # provider whitelist. check_models verifies actual availability.
        if not isinstance(model, str) or not _OPENCODE_MODEL.fullmatch(model):
            return (
                f"{label} requires an OpenCode provider/model identifier; "
                "saved session engines cannot be switched on resume"
            )
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
    command = record.get("command")
    if not isinstance(command, list):
        command = []
    for flag in ("--model", "-m"):
        if flag in command[:-1]:
            return str(command[command.index(flag) + 1])
    return None


def _routable(state: RunState, record: dict) -> str | None:
    role = record.get("route_role") or record.get("role")
    configured = (state.get("settings") or {}).get("roles") or {}
    if role in ROUTABLE and isinstance(configured.get(role), dict) and not state.get("parent_run"):
        return role
    return None


def job_pause_current(state: RunState) -> bool:
    """True when ``job_failure`` is the pause the run is in now.

    A later non-job ``--abandon-stage`` can leave the old failure in place and
    write its own recovery record (#567). That pause is not this job. A failure
    with no recovery record is still current: older job pauses, and callers that
    only set ``job_failure``, keep the exact retry.
    """
    failure = state.get("job_failure") or {}
    if not failure or state.get("status") not in _JOB_PAUSES:
        return False
    recovery = state.get("recovery_context") or {}
    if not recovery:
        return True
    if recovery.get("kind") == "job_failure":
        return recovery.get("attempt_id") == failure.get("attempt_id") and (
            not recovery.get("stage") or recovery.get("stage") == failure.get("stage")
        )
    later = recovery.get("attempt_id")
    return not later or later == failure.get("attempt_id")


def _job_row(state: RunState) -> dict | None:
    """The archived attempt of a workflow job paused on quota or a refusal (autocode_job_failure), or None."""
    failure = state.get("job_failure") or {}
    if not job_pause_current(state) or failure.get("pause_status") not in STATUSES:
        return None
    return next(
        (
            row
            for row in reversed(state.get("stages") or [])
            if isinstance(row, dict)
            and not row.get("runner_owned")
            and row.get("stage") == failure.get("stage")
            and _attempt_id(row) == failure.get("attempt_id")
        ),
        None,
    )


def stopped_attempt(state: RunState, *, failure_status) -> dict | None:
    """The attempt a quota or content-filter stop left for one routable role, or None.

    The one lookup for that stop. ``kind`` says which attempt it is, checked in this order:

    - ``stage``: the uncertain ``active_stage`` (``active`` True);
    - ``job``: a workflow job's attempt autocode_job_failure already set aside, with one exact
      retry bound to the run's configuration (#463). Its model is named with --answer and the
      job retry token, never with --abandon-stage or a --<role>-model flag;
    - ``abandoned``: the attempt a person set aside with --abandon-stage while nothing has run since.

    ``workflow_job`` is True for a workflow job's attempt: the ``job`` kind, or an uncertain
    ``stage`` that admission bound to its launch configuration (autocode_job_failure.admit saves
    ``job_configuration``), which setting it aside turns into a job stop. ``bound_model`` is the
    role's model in the configuration that job's exact retry is bound to, else None.
    """
    active = state.get("active_stage") or {}
    job = None if active else _job_row(state)
    if active:
        record, kind = active, "stage"
    elif job is not None:
        record, kind = job, "job"
    else:
        latest = next(
            (
                row
                for row in reversed(state.get("stages") or [])
                if isinstance(row, dict) and not row.get("runner_owned") and not row.get("worker_attempt")
            ),
            None,
        )
        if not latest or not latest.get("abandoned"):
            return None
        record, kind = latest, "abandoned"
    events = record.get("events")
    role = _routable(state, record)
    if kind == "job":
        status = state["job_failure"]["pause_status"]  # classified when the job stopped
    else:
        try:
            status = failure_status(events) if events and Path(events).is_file() else None
        except (OSError, ValueError, TypeError):
            status = None
    if not role or status not in STATUSES:
        return None
    bound = (
        state["job_failure"].get("configuration")
        if kind == "job"
        else record.get("job_configuration")
        if kind == "stage"
        else None
    )
    bound_route = ((bound.get("roles") if isinstance(bound, dict) else None) or {}).get(role)
    return {
        "role": role,
        "stage": record.get("original_stage") or record.get("stage"),
        "attempt_id": _attempt_id(record),
        "events": events,
        "active": kind == "stage",
        "kind": kind,
        "model": _launched_model(record),
        "pause_status": status,
        "workflow_job": kind == "job" or isinstance(bound, dict),
        "bound_model": bound_route.get("model") if isinstance(bound_route, dict) else None,
    }


def question(
    state: RunState,
    attempt: dict,
    *,
    cross_check=None,
    configured_tool: bool = False,
    current: str | None = None,
    rule=None,
) -> dict:
    """The model question for ``attempt``: named by the job on screen, answered only by a person.

    With ``cross_check`` a content-filter question also lists the configured models that would pass.
    ``current`` and ``rule`` are as for ``candidates``.
    """
    role = attempt["role"]
    job = roles.screen_name(attempt.get("stage") or role, state)
    settings = state.get("settings") or {}
    current = current or ((settings.get("roles") or {}).get(role) or {}).get("model")
    stopped_on = attempt.get("model") or current
    asked = {
        "id": PREFIX + role,
        "question": f"{job}'s quota is exhausted; name the model to continue on",
        "why": (
            f"The {job} stopped on {stopped_on}: its provider reported the quota, usage limit or "
            "credits used up. AutoCode never switches models on its own. Name a model for the "
            f"same engine ({engine(settings, role)}) that does not share its producer's or "
            "checker's model family."
        ),
        "options": [],
        "proposed_default": "",
        "kind": "decision",
        "category": CATEGORY,
        "delegable": False,
        "route_role": role,
        "job": job,
        "current_model": current,
        "engine": engine(settings, role),
        "cause": "quota",
        "stopped_model": stopped_on,
    }
    if attempt.get("pause_status") != REFUSAL_STATUS:
        return asked
    asked.update(
        cause="content_filter",
        question=f"{job}'s model was refused by its provider's content filter; name another model to continue on",
        why=(
            f"The {job} stopped on {stopped_on}: its provider's content filter refused the response, "
            "and the same model is likely to refuse it again, so AutoCode does not replay it and never "
            f"switches models on its own. Name a model for the same engine ({engine(settings, role)}), "
            "preferably from another provider, that does not share its producer's or checker's "
            "model family."
        ),
    )
    if cross_check is not None:
        passing, refused = candidates(
            state,
            role,
            stopped_on,
            cross_check=cross_check,
            configured_tool=configured_tool,
            job=job,
            current=current,
            rule=rule,
        )
        asked["candidates"] = passing
        asked["recommendation"] = (
            f"Configured models that pass the launch rules for the {job}: {', '.join(passing)}."
            if passing
            else f"No other configured model passes the launch rules for the {job} (refused: {', '.join(refused)}); "
            "name one from another provider."
            if refused
            else f"No model from another provider is configured; name one for the {job}."
        )
        asked["why"] += " " + asked["recommendation"]
    return asked


def _provider(model) -> str | None:
    return model.split("/", 1)[0] if isinstance(model, str) and "/" in model else None


def candidates(
    state: RunState,
    role: str,
    refused_model,
    *,
    cross_check,
    configured_tool: bool = False,
    job: str | None = None,
    current: str | None = None,
    rule=None,
) -> tuple[list[str], list[str]]:
    """(models that pass, models refused): the run's other configured models for ``role`` after a refusal.

    The refusing provider's models are left out: the content filter is the provider's. ``current`` is
    as for ``validate``, and ``rule`` one more check the answer must pass (raising ValueError), so the
    list never names a model the answer path refuses (#288/#301).
    """
    settings = state.get("settings") or {}
    role_engine = engine(settings, role)
    configured = dict.fromkeys(
        config["model"]
        for name, config in sorted((settings.get("roles") or {}).items())
        if isinstance(config, dict) and isinstance(config.get("model"), str) and engine(settings, name) == role_engine
    )
    passing, refused = [], []
    for model in configured:
        if model == refused_model or (_provider(model) and _provider(model) == _provider(refused_model)):
            continue
        try:
            validate(
                state, role, model, configured_tool=configured_tool, cross_check=cross_check, job=job, current=current
            )
            if rule is not None:
                rule(model)
        except ValueError:
            refused.append(model)
        else:
            passing.append(model)
    return passing, refused


def advice(asked: dict, attempt_id: str | None, *, kind: str = "stage", answerable: bool = True) -> str:
    """The commands the CLI accepts at this stop. Never names one it refuses (#288/#301).

    At a workflow job's stop (``kind`` "job") the attempt is already set aside and the job takes
    one exact retry, so neither --abandon-stage nor a --<role>-model flag is accepted there: the
    answer carries the job retry token and issues a new one for the retry. A route without a
    flag (JOB_ROLES) is named only by an answer.

    ``answerable`` is False when the current request does not ask this model question
    (``answers_route``): --answer is refused there, so only setting the attempt aside is named.
    """
    role = asked["route_role"]
    if kind == "job":
        text = (
            f"To continue on another model, answer --answer {asked['id']}=MODEL --job-retry-token TOKEN, "
            "then retry the job with the new token: --resume-paused --retry-failed-stage "
            "--job-retry-token NEW_TOKEN"
        )
        if asked.get("cause", "quota") == "quota":
            text += (
                "; or, once the quota resets, retry it unchanged with --resume-paused --retry-failed-stage "
                "--job-retry-token TOKEN"
            )
        return text + "." + (" " + asked["recommendation"] if asked.get("recommendation") else "")
    steps = [f"answer --answer {asked['id']}=MODEL --resolver-token TOKEN, then --resume-paused"] if answerable else []
    if attempt_id and role in ROLES:
        steps.append(
            ("" if answerable else "set the attempt aside with ")
            + f"--abandon-stage {attempt_id}, then --resume-paused {flag(role)} MODEL"
        )
    return (
        "To continue on another model, "
        + "; or ".join(steps)
        + "."
        + (" " + asked["recommendation"] if asked.get("recommendation") else "")
    )


def option(asked: dict) -> str:
    return f"Name the model the {asked['job']} continues on with --answer {asked['id']}=MODEL"


def asked_route(questions, question_id: str) -> dict | None:
    """The published model question ``question_id``, or None when the request asks no such thing."""
    return next(
        (
            q
            for q in questions or ()
            if isinstance(q, dict)
            and q.get("id") == question_id
            and q.get("category") == CATEGORY
            and q.get("route_role") in ROUTABLE
            and question_id == PREFIX + q["route_role"]
        ),
        None,
    )


def answers_route(questions, origin: dict | None, role: str) -> bool:
    """Whether the current request takes ``--answer route-<role>=MODEL``: parse_answer's own rule."""
    return (origin or {}).get("pause_status") in STATUSES and asked_route(questions, PREFIX + role) is not None


def parse_answer(answers, questions, origin: dict) -> tuple[dict, str]:
    """(question, model) from ``--answer route-<role>=MODEL`` at a quota or refusal stop; ValueError otherwise."""
    if (origin or {}).get("pause_status") not in STATUSES:
        raise ValueError(
            "Only a quota or content-filter stop is answered with a model; use --resolver-response for this request"
        )
    if len(answers) != 1:
        raise ValueError("Answer the model question on its own: --answer route-ROLE=MODEL")
    question_id, separator, model = answers[0].partition("=")
    asked = asked_route(questions, question_id)
    if not separator or asked is None:
        raise ValueError(f"{question_id} is not the model question of the current request")
    model = model.strip()
    if not model:
        raise ValueError("Name the model to continue on: --answer route-ROLE=MODEL")
    return asked, model


def validate(
    state: RunState,
    role: str,
    model: str,
    *,
    configured_tool: bool,
    cross_check,
    job: str | None = None,
    current: str | None = None,
) -> None:
    """Refuse a model a launch would refuse: wrong format for the engine, unchanged, or a cross-model clash.

    ``job`` names the role as the question does (``Tester``), never by its code name. ``current`` is the
    model the stopped attempt ran on when the role's saved route has moved since (a parallel Builder whose
    sibling's answer already moved it, #465); unchanged means unchanged from that model.
    """
    settings = state.get("settings") or {}
    route = (settings.get("roles") or {}).get(role)
    job = job or roles.screen_name(role, state)
    if not isinstance(route, dict):
        raise ValueError(f"This run has no {job} route to assign")
    problem = model_problem(role, model, role_engine=engine(settings, role), configured_tool=configured_tool, label=job)
    if not problem and (not isinstance(model, str) or not model.strip() or any(c.isspace() for c in model)):
        problem = f"{job} needs a model name without spaces"
    if problem:
        raise ValueError(problem)
    if (current or route.get("model")) == model:
        raise ValueError(f"The {job} already uses {model}; name a different model")
    clash = _clashes(settings, role, model, cross_check)
    if clash:
        raise ValueError(clash)


def _clashes(settings: dict, role: str, model: str, cross_check) -> str | None:
    """The cross-model refusal a launch would raise with ``model`` on ``role``, or None."""
    trial = {"settings": copy.deepcopy(settings)}
    trial["settings"]["roles"][role]["model"] = model
    try:
        cross_check(trial)
    except Exception as error:  # the rule raises Paused(PAUSED_CROSS_MODEL); a refused answer is input
        if getattr(error, "status", None) != "PAUSED_CROSS_MODEL":
            raise
        return str(error)
    return None


def _carry(state: RunState, role: str, stopped: str | None, model: str) -> None:
    """Keep a named model past the milestone boundary.

    The Builder retry lane restores its saved routes when the next milestone starts
    (autocode_builder_policy.lane); a saved route still on the model that ran out
    would silently switch the role back to it.
    """
    lane = (state.get("builder_retries") or {}).get(state.get("builder_retry_key")) or {}
    saved = lane.get("initial_route") if role == "terra" else (lane.get("checker_routes") or {}).get(role)
    if isinstance(saved, dict) and stopped and saved.get("model") == stopped:
        saved["model"] = model


def assign(
    state: RunState,
    role: str,
    model: str,
    *,
    at: str,
    via: str,
    attempt: dict | None = None,
    request_id: str | None = None,
    current: str | None = None,
) -> dict:
    """Set the role's model, drop its session and append the route_assignment record; return the record.

    The record's ``from`` is ``current`` (as for ``validate``) when given, else the role's saved route.
    """
    settings = state["settings"]
    route = settings["roles"][role]
    record = {
        "kind": KIND,
        "actor": "user_cli",
        "at": at,
        "via": via,
        "role": role,
        "job": roles.screen_name((attempt or {}).get("stage") or role, state),
        "from": current or route.get("model"),
        "to": model,
        "engine": engine(settings, role),
        "stage": (attempt or {}).get("stage"),
        "attempt_id": (attempt or {}).get("attempt_id"),
        "pause_status": (attempt or {}).get("pause_status") or QUOTA_STATUS,
        "events": (attempt or {}).get("events"),
    }
    if request_id:
        record["request_id"] = request_id
    _carry(state, role, route.get("model"), model)
    route["model"] = model
    state.setdefault("sessions", {}).pop(role, None)
    state.setdefault("user_events", []).append(record)
    return copy.deepcopy(record)


def _changed_role(state: RunState, previous: dict, selected: dict, failure_status):
    """(attempt, model before, model after) when the flags change the stopped role's model."""
    attempt = stopped_attempt({**state, "settings": previous}, failure_status=failure_status)
    if not attempt:
        return None
    role = attempt["role"]
    before = ((previous.get("roles") or {}).get(role) or {}).get("model")
    after = ((selected.get("roles") or {}).get(role) or {}).get("model")
    return (attempt, before, after) if after and before != after else None


def resume_refusal(
    state: RunState,
    previous: dict,
    selected: dict,
    *,
    failure_status,
    abandoning: str | None,
    questions=None,
    origin: dict | None = None,
) -> str | None:
    """Why a --<role>-model change cannot be saved now, or None.

    While the stopped attempt is still uncertain the model is named with --answer, or
    the attempt is set aside first (--abandon-stage, in the same or an earlier invocation).
    Saving the flag alone would change the route under an unresolved attempt and leave the
    question asking for the model just named. For a workflow job's attempt the flag is never saved,
    whether the job already stopped or is still uncertain (set aside in this invocation or later):
    the job's exact retry is bound to the configuration it ran under, so the flag would make that
    retry stale and record nothing; the job's answer names the model and issues a new retry token
    (#463). Only a flag that puts back the bound model is saved: it restores that configuration.

    ``questions`` and ``origin`` are the current published request's. The saved events can
    classify as a quota or refusal stop while that request was published as another stop (a
    session-ID mismatch, or a run paused before a finish-only refusal was typed, #464); it asks
    no model question and refuses --answer, so only --abandon-stage is offered then.
    """
    changed = _changed_role(state, previous, selected, failure_status)
    if not changed:
        return None
    attempt, role, after = changed[0], changed[0]["role"], changed[2]
    job = roles.screen_name(attempt.get("stage") or role, state)
    asked = {
        "id": PREFIX + role,
        "route_role": role,
        "cause": "content_filter" if attempt.get("pause_status") == REFUSAL_STATUS else "quota",
    }
    stopped = _STOPPED.get(attempt.get("pause_status"), _STOPPED[QUOTA_STATUS])
    unsaved = flag(role) if role in ROLES else "the model change"
    if attempt.get("workflow_job"):
        if after == attempt.get("bound_model"):
            return None  # puts back the model the job's exact retry is bound to
        if attempt["kind"] == "job":
            return (
                f"The {job} attempt that {stopped} was set aside for one exact retry bound to its "
                f"configuration; {unsaved} is not saved. " + advice(asked, attempt["attempt_id"], kind="job")
            )
        return (
            f"The {job} attempt that {stopped} is a workflow job: setting it aside keeps one exact retry "
            f"bound to the configuration it ran under, so {unsaved} is not saved. Set it aside first with "
            f"--abandon-stage {attempt['attempt_id']} alone. " + advice(asked, attempt["attempt_id"], kind="job")
        )
    if not attempt["active"] or (abandoning and abandoning == attempt["attempt_id"]):
        return None
    answerable = answers_route(questions, origin, role)
    if not answerable and not (attempt["attempt_id"] and role in ROLES):
        return None  # no command this request accepts names the model first
    return f"The {job} attempt that {stopped} is still uncertain; {unsaved} is not saved. " + advice(
        asked, attempt["attempt_id"], answerable=answerable
    )


def record_resume_change(
    state: RunState,
    previous: dict,
    selected: dict,
    *,
    failure_status,
    at: str,
    cross_check=None,
    configured_tool: bool = False,
) -> list[dict]:
    """Record a --<role>-model change saved while that role is stopped on quota or a refusal (resume path).

    A model a launch refuses (wrong format, a cross-model clash) is not recorded: it never runs.
    """
    changed = _changed_role(state, previous, selected, failure_status)
    if not changed or changed[0].get("workflow_job"):  # a job's model is named by its answer (resume_refusal)
        return []
    attempt, before, after = changed
    role = attempt["role"]
    if model_problem(role, after, role_engine=engine(selected, role), configured_tool=configured_tool):
        return []
    if cross_check is not None and _clashes(selected, role, after, cross_check):
        return []
    # A refused earlier flag (never recorded, never run) is not where the role came from.
    stopped = attempt.get("model") or before
    before = (
        before
        if any(event.get("role") == role and event.get("to") == before for event in assignments(state))
        else stopped
    )
    if before == after:
        return []
    _carry(state, role, before, after)
    record = {
        "kind": KIND,
        "actor": "user_cli",
        "at": at,
        "via": "resume_flag",
        "role": role,
        "job": roles.screen_name(attempt.get("stage") or role, state),
        "from": before,
        "to": after,
        "engine": engine(selected, role),
        "stage": attempt.get("stage"),
        "attempt_id": attempt.get("attempt_id"),
        "pause_status": attempt.get("pause_status", QUOTA_STATUS),
        "events": attempt.get("events"),
    }
    state.setdefault("user_events", []).append(record)
    return [copy.deepcopy(record)]


def routes(state: RunState) -> dict:
    """{role: {model, engine}} for every configured role: the routes the next launch uses."""
    settings = state.get("settings") or {}
    return {
        role: {"model": config.get("model"), "engine": engine(settings, role)}
        for role, config in sorted((settings.get("roles") or {}).items())
        if isinstance(config, dict)
    }


def unassigned(state: RunState, settings: dict) -> dict:
    """``settings`` with every recorded route assignment undone, newest first.

    A model a person named at a quota stop is not new evidence or a new cause, so a binding
    taken before it (a recovery packet) still holds after it. Only a route still on the
    assigned model is undone; any other model change stays visible to the binding.
    """
    settings = copy.deepcopy(settings)
    for event in reversed(assignments(state)):
        route = (settings.get("roles") or {}).get(event.get("role"))
        if isinstance(route, dict) and event.get("from") and route.get("model") == event.get("to"):
            route["model"] = event["from"]
    return settings


def assignments(state: RunState) -> list[dict]:
    """Every recorded route assignment, oldest first."""
    return [
        copy.deepcopy(event)
        for event in state.get("user_events") or []
        if isinstance(event, dict) and event.get("kind") == KIND
    ]
