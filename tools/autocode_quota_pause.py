"""The provider-quota pause loop for issue #184: ask, never switch.

A PAUSED_BUDGET stop is always a human decision. This module owns the pure
policy: composing the replacement-model question, recognizing it again,
validating an answered model against the affected route's engine, refusing a
checker assignment that would grade its producer's model or family, the
assignment event/route payloads, and recognizing an answered quota pause that
awaits relaunch. The runner-owned effects (archival through the records owner,
publication, persistence) arrive as the runner module argument, the way
autocode_run_actions receives it.

Imports stay at the bottom layer (autocode_util, autocode_roles,
autocode_planner_routes); nothing here imports the controller, so it can never
join the import cycle in tests/test_architecture.py.
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path

try:
    from . import autocode_planner_routes as planner_routes, autocode_roles as roles
except ImportError:
    import autocode_planner_routes as planner_routes
    import autocode_roles as roles


QUESTION_PREFIX = "Q-quota-"
# Mirrors the identifier rules the per-role --*-model flags apply at configure
# time (tools/autocode_configure.py): a bare GPT name for Codex routes, an
# OpenCode provider/model catalogue identifier for the builtin provider, and a
# nonblank whitespace-free name for a configured provider.
CODEX_MODEL = re.compile(r"gpt-[a-zA-Z0-9.-]+")
OPENCODE_IDENTIFIER = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]*/[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,120}")
# Checker role -> (producer role, a representative stage for the screen name).
CHECKER_PRODUCERS = {"sol": ("terra", "terra"), "completion": ("terra", "astra_review"),
                     "plan_reviewer": ("glm", "astra_challenge")}
ROLE_STAGES = {"terra": "terra", "sol": "sol", "completion": "astra_review",
               "plan_reviewer": "astra_challenge", "glm": "astra_plan",
               "requirements": "requirements_gather", "resolver": "astra_resolve",
               "astra": "astra_review", "orchestrator": "orchestrator"}


def screen_name(role):
    """Printed job name for a route role, from the one naming table."""
    return roles.screen_name(ROLE_STAGES.get(role, role)) or str(role)


def route_for(state, role):
    return ((state.get("settings") or {}).get("roles") or {}).get(role) or {}


def engine_for(state, route):
    return route.get("engine") or (state.get("settings") or {}).get("engine") or "opencode"


def placeholder_for(state, route):
    engine = engine_for(state, route)
    if engine == "opencode" and not route.get("provider"):
        return "PROVIDER/MODEL"
    return "MODEL"


def valid_model(state, route, model):
    if not isinstance(model, str) or not model.strip() or any(character.isspace() for character in model):
        return False
    engine = engine_for(state, route)
    if engine == "codex":
        return bool(CODEX_MODEL.fullmatch(model))
    if engine == "opencode" and not route.get("provider"):
        return bool(OPENCODE_IDENTIFIER.fullmatch(model))
    return True


def producer_models(state, producer):
    """Launch models recorded for the producer's retained attempts, newest first.

    Falls back to the producer's current saved route only when no attempt is
    recorded, so a checker can never be assigned the model that produced the
    artifact it checks even after the producer's own route moved on (#184).
    """
    models = []
    for row in reversed(state.get("stages") or []):
        if (row.get("route_role") or row.get("role")) != producer:
            continue
        model = (row.get("launch_route") or {}).get("model")
        if model and model not in models:
            models.append(model)
    if not models:
        current = route_for(state, producer).get("model")
        if current:
            models.append(current)
    return models


def cross_model_violation(state, role, model):
    """(producer, recorded model, reason) when a checker would grade its producer."""
    producer = CHECKER_PRODUCERS.get(role)
    if not producer:
        return None
    producer_role = producer[0]
    for recorded in producer_models(state, producer_role):
        if recorded == model:
            return producer_role, recorded, "identical model"
        family = planner_routes._model_family(recorded)
        if family and family == planner_routes._model_family(model):
            return producer_role, recorded, f"same {family} family"
    return None


def question_id_for(role):
    return QUESTION_PREFIX + str(role)


def handles(state, question_id):
    """Whether question_id addresses this run's pending replacement-model question."""
    request = state.get("user_request") or {}
    quota = request.get("quota") or {}
    return (bool(quota) and question_id == question_id_for(quota.get("role"))
            and any(question.get("id") == question_id for question in state.get("pending_questions") or []))


def resume_ready(state, current=None):
    """An answered quota assignment waiting only for relaunch (#184).

    Detected from saved state alone: the budget pause itself, no live request
    (the answer consumed it) and the model_assignment event the answer wrote as
    the most recent user event. Any other pause keeps the existing gates.
    """
    if state.get("status") != "PAUSED_BUDGET" or state.get("pending_questions"):
        return False
    if current is not None:
        if current(state):
            return False
    elif state.get("resolver_human_request"):
        return False
    events = state.get("user_events") or []
    return bool(events) and events[-1].get("kind") == "model_assignment"


def answer_command(run_dir, workspace, question_id, placeholder, token):
    return " ".join(["autocode", "--workspace", shlex.quote(str(workspace)),
                     "--run-dir", shlex.quote(str(run_dir)), "--answer",
                     shlex.quote(f"{question_id}={placeholder}"), "--resolver-token", token])


def _present(state, run_dir, workspace, published):
    """Recompose the stop reason with the exact working answer command."""
    request = published.get("request") or {}
    quota = request.get("quota") or {}
    role = quota.get("role", "")
    questions = published.get("questions") or [{"id": ""}]
    command = answer_command(run_dir, workspace, questions[0].get("id", ""),
                             quota.get("placeholder", "PROVIDER/MODEL"), published.get("request_token", ""))
    state.update(stop_reason=(
        f"PAUSED_BUDGET: provider quota exhausted for the {screen_name(role)} (role {role}) "
        f"on '{quota.get('from_model', '')}'. The failed attempt is archived without replay and no "
        f"model was switched; a quota event is always your decision. Name the replacement model "
        f"for role {role} (in place of {quota.get('placeholder', 'PROVIDER/MODEL')}): {command}"))
    return command


def _compose(state, *, role, stage, route, worker=None):
    model = route.get("model") or ""
    placeholder = placeholder_for(state, route)
    where = f", milestone {worker['milestone_id']}" if worker else ""
    text = (f"Provider quota exhausted: the {screen_name(role)} (role {role}{where}) stopped on model "
            f"'{model}'. Which replacement model should continue its next attempt? "
            f"Answer with one model identifier in place of {placeholder}.")
    question = {"id": question_id_for(role), "question": text,
                "why": "A quota stop is always a human decision: AutoCode never switches models "
                       "automatically. The failed attempt is archived without replay, its partial "
                       "workspace changes are retained, and the next attempt starts a fresh provider "
                       "session on the model you name.",
                "options": [], "proposed_default": ""}
    request = {"kind": "permission", "decision_needed": text,
               "impact": f"The {screen_name(role)} (role {role}{where}) reached its provider quota "
                         "limit. Naming a model routes the next attempt on it from the checkpoint; "
                         "nothing else changes.",
               "options": [], "proposed_delta": "Names the replacement model for one role; no goal, "
                "scope, acceptance-criteria or standing-policy change.",
               "quota": {"role": role, "stage": stage, "from_model": model,
                         "engine": engine_for(state, route), "placeholder": placeholder,
                         **({"worker": dict(worker)} if worker else {})}}
    return question, request


def _archive_attempt(runner, state, run_dir, workspace, record):
    """Archive the proven-stopped quota attempt through the records owner.

    The reconcile_rate_limited_stage recipe without its source-changed abort:
    here a human decides, so an attempt that changed tracked files is archived
    with its changed paths recorded and the workspace changes left in place.
    """
    before = runner.read_json(Path(record["before_ref"]))
    after = runner.support.snapshot(workspace)
    record["metrics"] = runner.support.event_metrics(record["events"])
    runner.account_stage(state, record)
    after_path = Path(record["output"]).with_suffix(".after.json")
    runner.write_json(after_path, after)
    record.update(after_ref=str(after_path), source_revision=after["revision"],
                  changed_files=runner.support.changed_paths(before, after), abandoned=True)
    originals = runner.archive_rejected_stage(
        state, run_dir, record,
        "Provider quota exhausted; attempt archived without replay, its workspace changes retained")
    route = record.get("route_role") or record.get("role")
    if route:
        state.setdefault("sessions", {}).pop(route, None)
    state.update(next_stage=record.get("original_stage") or record.get("stage"))
    runner.write_json(Path(run_dir) / "state.json", state)
    for artifact in originals:
        artifact.unlink(missing_ok=True)


def _proven_quota_attempt(runner, record):
    """The record only when it is a proven-stopped provider-quota attempt.

    A live process, a completed turn, missing events or a missing before
    snapshot is not provable; report-repair and workflow-job attempts have
    their own owners. Unprovable stops keep the ordinary pause.
    """
    events = Path(record.get("events") or "")
    if (not events.is_file()
            or runner.support.failure_status(events) != "PAUSED_BUDGET"
            or any(event.get("type") == "turn.completed" for event in runner.support.events(events))
            or not record.get("before_ref") or not Path(record["before_ref"]).is_file()
            or record.get("report_only") or runner.job_failure.owner(record)):
        return None
    try:
        runner.assert_stage_stopped(record)
    except runner.support.Paused:
        return None
    return record


def _archive_worker_attempt(runner, worker):
    """Archive the parallel worker's failed child attempt exactly once (#184).

    AC12 requires the child's failed attempt recorded exactly once in the
    child's saved stage records at the pause boundary, before the parent
    publishes its question. The retry lane then starts a fresh attempt
    instead of reconciling the failed one. The child is restored to its own
    terra checkpoint under its own workspace lock, the same boundary the
    worker process uses.
    """
    child_path = Path(worker["run_dir"]) / "state.json"
    if not child_path.is_file():
        return False
    with runner.support.workspace_lock(Path(worker["workspace"])):
        child = runner.read_json(child_path)
        record = child.get("active_stage") or {}
        if not child.get("parent_run") or not _proven_quota_attempt(runner, record):
            return False
        _archive_attempt(runner, child, Path(worker["run_dir"]), Path(worker["workspace"]), record)
    return True


def handle_budget_pause(runner, state, run_dir, workspace, error):
    """Archive the stopped attempt and publish the quota question (#184).

    Returns True when a replacement-model question is pending. Any doubt about
    the attempt (live process, missing events or before-snapshot, a report-repair
    or workflow-job attempt) keeps today's ordinary pause with no question and
    no advertised command, so the CLI never advertises an unanswerable ask.
    """
    human = runner.resolver_human
    published = human.current(state)
    if published and (published.get("request") or {}).get("quota"):
        _present(state, run_dir, workspace, published)
        return True
    worker = getattr(error, "quota_worker", None) or None
    if worker:
        if state.get("active_stage") or not _archive_worker_attempt(runner, worker):
            return False
        role, stage = "terra", "orchestrator"
    else:
        record = state.get("active_stage") or {}
        if not _proven_quota_attempt(runner, record):
            return False
        _archive_attempt(runner, state, run_dir, workspace, record)
        role = record.get("route_role") or record.get("role")
        stage = record.get("original_stage") or record.get("stage")
    route = route_for(state, role)
    if not role or not route.get("model"):
        return False
    question, request = _compose(state, role=role, stage=stage, route=route, worker=worker)
    origin = {"stage": stage, "pause_status": "PAUSED_BUDGET"}
    human.queue(state, "permission", origin, request=request, questions=[question],
                phase="PAUSED_OR_BLOCKED", next_stage=state.get("next_stage"))
    # Evaluate before persisting: only a published request is answerable. A
    # genuine evaluation deferral (for example a pending interruption) keeps
    # the ordinary pause instead of advertising a command the CLI would refuse.
    disposition = human.evaluate(state)
    published = human.current(state) if disposition == "escalate" else None
    runner.write_json(Path(run_dir) / "state.json", state)
    if published is None:
        state.pop(human.PRIVATE, None)
        state.update(status="PAUSED_BUDGET", phase="PAUSED_OR_BLOCKED")
        return False
    _present(state, run_dir, workspace, published)
    return True


def apply_answer(runner, candidate, question_id, response):
    """Validate and apply one human model assignment; never launches a provider."""
    human = runner.resolver_human
    request = candidate.get("user_request") or {}
    quota = request.get("quota") or {}
    if not quota or not any(question.get("id") == question_id for question in candidate.get("pending_questions") or []):
        raise ValueError(f"{question_id} does not address this run's pending quota question")
    role = quota.get("role", "")
    worker = quota.get("worker") or None
    route = route_for(candidate, role)
    model = str(response or "").strip()
    if not valid_model(candidate, route, model):
        raise ValueError(f"{question_id} needs one replacement model identifier for the "
                         f"{screen_name(role)} (role {role}) in place of "
                         f"{quota.get('placeholder', 'PROVIDER/MODEL')}; the saved engine "
                         f"{engine_for(candidate, route)!r} is never changed by an answer")
    violation = cross_model_violation(candidate, role, model)
    if violation:
        producer, recorded, reason = violation
        raise ValueError(
            f"refusing to assign the {screen_name(role)} (role {role}) to '{model}': it must not grade "
            f"its producer the {screen_name(producer)} (role {producer}), whose recorded launch model "
            f"is '{recorded}' ({reason}); choose a different model or family for role {role}")
    if worker:
        child_path = Path(worker["run_dir"]) / "state.json"
        if not child_path.is_file():
            raise ValueError(f"The quota-paused Builder run is missing at {child_path}")
        with runner.support.workspace_lock(Path(worker["workspace"])):
            child = runner.read_json(child_path)
            child_route = route_for(child, role)
            if not child_route.get("model"):
                raise ValueError(f"The Builder run has no saved {role} route to assign")
            child_route["model"] = model
            runner.write_json(child_path, child)
        rows = (candidate.get("orchestration_batch") or {}).get("workers") or []
        if not any(row.get("milestone_id") == worker.get("milestone_id") for row in rows):
            raise ValueError(f"Milestone {worker.get('milestone_id')} is not in the current Builder batch")
        for row in rows:
            if row.get("milestone_id") == worker.get("milestone_id"):
                row["retry_requested"] = True
    else:
        route["model"] = model
    event = {"kind": "model_assignment", "actor": "user_cli", "at": runner.now(),
             "role": role, "from_model": quota.get("from_model", ""), "to_model": model,
             "stage": quota.get("stage", ""), "question_id": question_id}
    if worker:
        event["milestone_id"] = worker.get("milestone_id")
    candidate.setdefault("user_events", []).append(event)
    candidate.setdefault("answers", {})[question_id] = event
    candidate["pending_questions"] = []
    candidate.pop("user_request", None)
    candidate.update(status="PAUSED_BUDGET", phase="PAUSED_OR_BLOCKED",
                     stop_reason=(f"PAUSED_BUDGET: {screen_name(role)} (role {role}) assigned to '{model}' "
                                  f"for the next attempt; resume with --resume-paused"))
    return event
