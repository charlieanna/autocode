"""When a stage stops making progress, investigate before pausing for the user.

Today a stage that does not converge (its report is rejected again and again, the
Planner and Plan Reviewer cannot agree within the review budget, the Builder makes no
progress, the Completion Owner will not decide) repairs, loops, then pauses and waits
for a person. Most of those pauses have a cause a stronger model can find by reading
the saved exchange: a rule the stage misunderstands, two roles talking past each other,
a missing fact the runner already has.

So before the runner pauses with one of ``STATUSES``, ``drive`` sends the run to
``STAGE``: a read-only Investigator on a different model from the stuck stage's (``route``:
Claude Opus 5.5 in kilocode runs, otherwise GPT-6 Sol, or GLM 5.3 / Luna when the stuck stage runs
on Sol) reads the task, the run's saved state and the
stuck stage's attempts, and returns a diagnosis plus either guidance for one more
attempt or the question only the user can answer. Then:

- ``retry`` (only for ``RETRYABLE`` statuses): the runner resets exactly one attempt's
  worth of the limit that stopped the stage and runs it again with the guidance in its
  prompt (``with_guidance``). Planning guidance stays in force for every planning stage
  until the plan is presented; any other stage's guidance until that stage completes.
  Earlier report-format diagnoses remain context for the run, matching the lifetime
  of spent investigation identities; they grant no additional retry.
- ``pause``, a ``DIAGNOSE_ONLY`` status, or a failed investigation: the original pause
  is restored exactly, with the diagnosis added to its reason.

Bounds: one investigation per distinct (stage, pause) problem and ``MAX_CALLS`` per run
(``settings.stuck_investigation.max_calls_per_run``; 0 disables). The Investigator
cannot approve work, change requirements or criteria, weaken tests, grant permissions,
spend beyond the one attempt, or write anything (it runs read-only). Decisions that belong to the
user (permissions, scope, goal and criteria changes, spend limits) never come here.

A retry is checked, not trusted. The Investigator states the diagnosed cause as a
plain-English ``example`` and, unless it says why none can (``untestable``), a ``probe``:
a command that exits 0 exactly when the files it cites in ``evidence_refs`` show that
cause. Every cited file must exist (in the repository or this run's directory). The runner
copies only the cited run files into a scratch tree, under ``run/``, and runs the probe
there (autocode_test_cases.run_probes); a probe that does not exit 0, or that needs an
uncited file, rejects the report and the original pause is restored.

Pure module: prompt, schema, transitions and the drive loop. Imports nothing from the
runner; the runner passes its pause exception type in, and the unit passes the function
that runs the probe. State keys written:
``stuck_investigation`` (the one in progress or in force) and ``stuck_investigations``
(history).
"""
from __future__ import annotations

import copy
import datetime as dt
import json
from dataclasses import replace
from pathlib import Path

try:
    from . import autocode_stage_access as stage_access, autocode_stray_writes as stray_writes
    from . import autocode_progressive_state as progressive
    from .autocode_test_cases import run_probes
except ImportError:
    import autocode_stage_access as stage_access
    import autocode_stray_writes as stray_writes
    import autocode_progressive_state as progressive
    from autocode_test_cases import run_probes

STAGE = "investigate_stuck"
ROUTE = "stuck_investigator"
MAX_CALLS = 3
# The runner can reset one attempt's worth of what stopped these, so a retry is well-defined.
RETRYABLE = frozenset({"PAUSED_REPEATED_FAILURE", "PAUSED_INVALID_OUTPUT", "PAUSED_PLANNING_BUDGET",
                       "PAUSED_NO_PROGRESS", "PAUSED_COMPLETION_REVIEW"})
# These have operator-only resume semantics; the Investigator explains them, the user decides.
DIAGNOSE_ONLY = frozenset({"PAUSED_REPORT_REPAIR_LIMIT", "PAUSED_BUILDER_RETRY_LIMIT",
                           "PAUSED_MILESTONE_STALLED", "PAUSED_MILESTONE_REPLAN"})
STATUSES = RETRYABLE | DIAGNOSE_ONLY
PLANNING = ("requirements_gather", "astra_discovery", "astra_challenge", "glm_revise", "astra_finalize")
# Diagnosticians are never themselves investigated.
NEVER = (STAGE, "astra_diagnose", "astra_resolve")
TEXT = {"type": "string"}
TEXTS = {"type": "array", "items": TEXT}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["diagnosis", "cause", "guidance", "recommendation", "user_question", "evidence_refs",
                 "example", "probe", "untestable"],
    "properties": {
        "diagnosis": TEXT,
        # example: the diagnosed cause as one concrete case in plain English; probe: a shell command
        # that exits 0 exactly when the cited files show it; untestable: why no command can. A retry
        # needs the example and exactly one of probe / untestable.
        "example": TEXT,
        "probe": TEXT,
        "untestable": TEXT,
        "cause": {"type": "string", "enum": ["stage_output", "role_disagreement", "missing_information",
                                             "environment", "needs_user", "other"]},
        "guidance": TEXT,
        "recommendation": {"type": "string", "enum": ["retry", "pause"]},
        "user_question": TEXT,
        "evidence_refs": TEXTS,
    },
}


def schema(state):
    if (state.get('stuck_investigation') or {}).get('mode') != 'builder_failure':
        return SCHEMA
    result = copy.deepcopy(SCHEMA)
    result['required'] += ['failure_class', 'failure_id']
    result['properties'].update(failure_class={'type': 'string', 'enum': ['plan', 'execution', 'operational', 'unknown']},
                                failure_id=TEXT)
    return result

# Repeated in a report-only repair of this stage (autocode_jobs.repair_rules): without them, both
# repairs of a live run's rejected probe guessed where the cited files would be (2026-09-29).
EVIDENCE_RULES = """- evidence_refs: files or saved outputs you relied on. Every one must exist: a repository path, or a
  saved output's path from recent_stages or failure_history. A retry needs at least one.
- example: for a retry, the diagnosed cause as one concrete case in plain English: "Given <the attempt's
  exact output>, when <the runner checked it>, then <it rejected it because ...>".
- probe: for a retry, a shell command that exits 0 exactly when the files you cite show that cause. The
  runner copies ONLY the run files you cite into a scratch tree, each at run/<its file name> (repository
  files keep their own paths), and runs the probe there; it rejects your report if the probe does not
  exit 0 or needs a file you did not cite. For example: python3 -c "import json; r = json.load(open(
  'run/astra_discovery-03.json')); assert ' ' in r['code_refs'][0]". When no command can show the cause
  (a judgement about two positions), leave probe "" and say why in untestable; otherwise untestable is "".
"""
REPAIR_RULES = "The Investigator's evidence fields, as its prompt states them:\n" + EVIDENCE_RULES

PROMPT = """You are the Investigator. A stage of an AI engineering run has stopped making progress and the runner
is about to pause the run for a person. Before it does, find out WHY the stage is stuck and whether one more
attempt, with the right guidance, would succeed. You do not do the stage's work and you do not edit the
repository.

stuck in the handoff names the stage, the pause status and the runner's reason. The runner's state.json at
state_file is authoritative: read the task, the contract or plan exchange, and the stuck stage's attempts
(recent_stages lists them, with each rejected attempt's reason and saved output). Read the repository files
they concern. You work read-only: read files and run only commands that do not write.

Decide what is actually wrong:
- stage_output: the stage keeps producing output the runner rejects (a rule or format it misreads, a path it
  cites wrongly, a field it drops). Say exactly what to produce instead.
- role_disagreement: two roles talk past each other (a Planner and Plan Reviewer, a Builder and Validator).
  Say which position the evidence supports, and what each should do.
- missing_information: the stage lacks a fact the runner or repository already has. Give the fact and where
  it lives.
- environment: tooling, dependencies or the machine are broken. Say what is broken; another attempt will not help.
- needs_user: only the user can settle it (a permission, a scope or goal change, a product decision).
- other: say what you found.

Return:
- diagnosis: two to five sentences a person can act on, citing evidence.
- guidance: concrete instructions for the stuck stage's next attempt (and for its reviewer when roles
  disagree): what to do differently and why. Empty when recommending pause.
- recommendation: retry only when your guidance would plausibly make the next attempt succeed; pause for
  environment, needs_user, or when you cannot tell. Never guess.
- user_question: when pausing, the one question or action the user must take; otherwise "".
""" + EVIDENCE_RULES + """
You cannot approve work, change requirements or acceptance criteria, weaken tests, grant permissions or extend
budgets; the runner grants at most one more attempt. Return JSON only, matching the schema the runner gives you.
"""


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def enabled(state: dict) -> bool:
    return state.get("version", 2) >= 3 and max_calls(state) > 0


def max_calls(state: dict) -> int:
    limit = ((state.get("settings") or {}).get("stuck_investigation") or {}).get("max_calls_per_run", MAX_CALLS)
    return limit if type(limit) is int and limit >= 0 else MAX_CALLS


def identity(stage: str, status: str) -> str:
    return f"{stage}:{status}"


def intercept(state: dict, status: str, reason: str) -> bool:
    """Send a run about to pause with a non-convergence ``status`` to the Investigator.

    Returns False (pause as before) when the feature is off, the status is not one of
    ``STATUSES``, a provider attempt is still unreconciled, this problem was already
    investigated, or the run's investigation budget is spent.
    """
    stuck = state.get("next_stage")
    if progressive.retained_review_budget_pause(state, status):
        return False
    if (not enabled(state) or status not in STATUSES or state.get("active_stage")
            or not isinstance(stuck, str) or not stuck or stuck in NEVER):
        return False
    history = state.get("stuck_investigations") or []
    key = identity(stuck, status)
    if (any(entry.get("identity") == key for entry in history) or len(history) >= max_calls(state)
            or operator_retried(state, stuck)):
        return False
    history = state.setdefault("stuck_investigations", [])
    request = {"identity": key, "stage": stuck, "status": status, "reason": reason, "phase": state.get("phase"),
               "requested_at": now()}
    if state.get("pending_report_repair"):
        # The before-stage hook would replay a spent repair ahead of the investigation.
        request["pending_report_repair"] = state.pop("pending_report_repair")
    state["stuck_investigation"] = request
    last = next((row for row in reversed(state.get("stages", []))
                 if (row.get("original_stage") or row.get("stage")) == stuck), {})
    # Written here once; planning_lessons uses the trigger even when repeated
    # rejected reports become PAUSED_REPEATED_FAILURE instead of INVALID_OUTPUT.
    trigger = "rejected_output" if (status == "PAUSED_INVALID_OUTPUT" or
              (status == "PAUSED_REPEATED_FAILURE" and last.get("rejected"))) else "non_convergence"
    history.append({"identity": key, "stage": stuck, "status": status, "reason": reason,
                    "requested_at": request["requested_at"], "outcome": "investigating", "trigger": trigger})
    for field in ("stop_reason", "paused_at"):
        state.pop(field, None)
    state.update(status="RUNNING", phase="INVESTIGATING", next_stage=STAGE)
    return True


def operator_retried(state: dict, stage: str) -> bool:
    """An operator authorized a retry of this stage's failure (--retry-failed-stage): its
    outcome goes back to the operator, who is already handling that exact failure."""
    keys = {row.get("failure_key") for row in state.get("stages") or []
            if row.get("stage") == stage and row.get("failure_key")}
    return any(grant.get("failure_key") in keys for grant in state.get("failure_retry_authorizations") or [])


def annotate(state: dict, status: str, reason: str) -> str:
    """A pause reason with the Investigator's diagnosis of the same problem, when there is one."""
    stuck = state.get("next_stage")
    entry = next((row for row in reversed(state.get("stuck_investigations", []))
                  if row.get("identity") == identity(stuck or "", status) and row.get("diagnosis")), None)
    if not entry or "\nInvestigator (" in reason:
        return reason
    ask = f" Needs you: {entry['user_question']}" if entry.get("user_question") else ""
    return f"{reason}\nInvestigator ({entry['outcome']}): {entry['diagnosis']}{ask}"


CLAUDE = "kilo/anthropic/claude-opus-5.5"


def route(roles: dict, stuck_model: str, provider: str | None = None) -> dict:
    """The Investigator's default route (user 2026-09-28: no Astra; Claude where the run can reach it).

    kilocode runs: Claude Opus 5.5 via the Kilo Gateway. OpenCode runs: GPT-6 Sol, or GLM 5.3 when
    the stuck stage runs on Sol. Native Codex runs (GPT only): GPT-6 Sol, or Luna when the stuck
    stage runs on Sol. A custom provider keeps its reviewer model. Always high effort, on the Plan
    Reviewer's engine and provider; --investigator-model overrides all of this (pinned_route)."""
    base = dict(roles.get("plan_reviewer") or roles.get("astra") or {})
    model, stuck_model = str(base.get("model") or ""), str(stuck_model or "")
    if provider == "kilocode" and CLAUDE not in stuck_model:
        choice = CLAUDE
    elif provider == "kilocode" or model.startswith(("openai/", "zai-coding-plan/", "kilo/")):
        choice = "zai-coding-plan/glm-5.3" if "gpt-6-sol" in stuck_model else "openai/gpt-6-sol"
    elif model.startswith("gpt-"):
        choice = "gpt-6-luna" if "gpt-6-sol" in stuck_model else "gpt-6-sol"
    else:
        choice = model
    base.update(model=choice, reasoning_effort="high")
    return base


EFFORTS = ("low", "medium", "high", "xhigh", "max")


def add_arguments(parser) -> None:
    """The Investigator's CLI flags (autocode.py): pin its model instead of the automatic choice."""
    parser.add_argument("--investigator-model",
                        help="Pin the stuck-stage Investigator's model for this run; a provider/model id "
                             "(e.g. openai/gpt-6-sol) runs it through OpenCode. Default: Claude Opus 5.5 in "
                             "kilocode runs, otherwise GPT-6 Sol (GLM 5.3 when the stuck stage runs on Sol)")
    parser.add_argument("--investigator-reasoning-effort", choices=EFFORTS,
                        help="Reasoning effort for the pinned Investigator model (default: high)")


def configure(settings: dict, args) -> dict:
    """Save a pinned Investigator route from the CLI flags (new and resumed runs, any engine);
    resumes keep it unless given again. Returns ``settings``."""
    model = getattr(args, "investigator_model", None)
    effort = getattr(args, "investigator_reasoning_effort", None)
    if not model and not effort:
        return settings
    saved = (settings.get("stuck_investigation") or {}).get("route") or {}
    model = model or saved.get("model")
    if not model:
        raise ValueError("--investigator-reasoning-effort needs --investigator-model (or a saved pinned model)")
    settings.setdefault("stuck_investigation", {})["route"] = {
        "model": model, "reasoning_effort": effort or saved.get("reasoning_effort") or "high", "provider": None,
        # provider/model ids are OpenCode routes (the repository's convention); bare names use the run's engine.
        "engine": "opencode" if "/" in model else settings.get("engine", "codex")}
    return settings


def pinned_route(settings: dict) -> dict | None:
    route = ((settings or {}).get("stuck_investigation") or {}).get("route")
    return dict(route) if route else None


def packet(state: dict, state_path, inventory: dict | None = None, engine: str | None = None) -> dict:
    request = state["stuck_investigation"]
    recent = [{key: row.get(key) for key in ("stage", "iteration", "output", "rejected", "rejection_reason")
               if row.get(key) is not None} for row in state.get("stages", [])[-12:]]
    failures = {key: {k: entry.get(k) for k in ("identity", "count", "last_error", "output_probe")}
                for key, entry in (state.get("failure_history") or {}).items()
                if (entry.get("identity") or {}).get("stage") == request["stage"]}
    return {"stage": STAGE, "task": state["task"], "workspace": state.get("workspace"), "state_file": str(state_path),
            "stuck": {key: request[key] for key in ("stage", "status", "reason")},
            "recent_stages": recent, "failure_history": failures,
            "previous_investigations": [row for row in state.get("stuck_investigations", [])
                                        if row.get("identity") != request["identity"]],
            "execution_engine": engine, "workspace_inventory": inventory or {},
            # Present because every provider reads them.
            "goal_contract": state.get("goal_contract"), "current_task": state.get("current_task"),
            "saved_answers": state.get("answers", {}),
            **({'builder_failure': request['failure_evidence']} if request.get('mode') == 'builder_failure' else {})}


def prompt(state: dict, state_path, inventory: dict | None = None, soft_budget_tokens: int = 10000,
           engine: str | None = None) -> tuple[str, dict]:
    text = PROMPT + "\nCURRENT HANDOFF DATA\n" + json.dumps(packet(state, state_path, inventory, engine), indent=2)
    if state['stuck_investigation'].get('mode') == 'builder_failure':
        text = ('Classify this specific Builder failure BEFORE retry/escalation, not an exhausted stage. '
                'This mode overrides the generic retry advice below: recommendation is advisory only; '
                'failure_class selects the bounded controller route and grants no retry by itself. '
                'Return failure_id exactly from builder_failure and failure_class plan, execution, operational or unknown. '
                'Cite only builder_failure.evidence_refs. Plan means an evidenced flawed approach within approved scope; '
                'execution means concrete implementation failure; operational means tooling/transport; unknown means insufficient evidence. '
                'Supply example and exactly one of probe/untestable for every classification. '
                'Your report grants no retry, budget, approval or contract change.\n' + text)
    return text, {"estimated_prompt_tokens": (len(text.encode()) + 3) // 4, "soft_budget_tokens": soft_budget_tokens}


def check(value: dict, changed_files) -> None:
    stray = stage_access.stray(STAGE, changed_files)
    if stray:
        raise stray_writes.StrayWrites(
            "An investigation must not change the repository; this attempt changed: " + ", ".join(stray), stray)
    if not value["diagnosis"].strip():
        raise ValueError("An investigation must say what it found")
    if value["recommendation"] == "retry" and not value["guidance"].strip():
        raise ValueError("A retry needs concrete guidance for the next attempt")
    if value["recommendation"] == "pause" and value["cause"] == "needs_user" and not value["user_question"].strip():
        raise ValueError("A pause for the user must say what the user has to decide")
    if value["recommendation"] == "retry":
        if not value.get("example", "").strip():
            raise ValueError("A retry needs the diagnosed cause as an example in plain English")
        if bool(value.get("probe", "").strip()) == bool(value.get("untestable", "").strip()):
            raise ValueError("A retry needs exactly one of probe (a command that exits 0 exactly when the cited "
                             "files show the cause) or untestable (why no command can)")
        if not [ref for ref in value["evidence_refs"] if str(ref).strip()]:
            raise ValueError("A retry must cite the files it relied on in evidence_refs")


def cited_files(value: dict, workspace, run_dir) -> dict[str, Path]:
    """Resolve evidence_refs; return the run-directory ones as {run/<file name>: source file}.

    A ref is a repository path, a path under this run's directory (relative or absolute), optionally
    with a ``:line`` suffix. Raises ValueError naming refs that do not exist or lie elsewhere.
    """
    workspace = Path(workspace).resolve()
    run_dir = Path(run_dir).resolve() if run_dir else None
    copies, missing = {}, []
    for raw in value["evidence_refs"]:
        text = str(raw).strip()
        if not text:
            continue
        ref = Path(text.rsplit(":", 1)[0] if ":" in text and text.rsplit(":", 1)[1].isdigit() else text)
        candidates = [ref] if ref.is_absolute() else [workspace / ref, *([run_dir / ref] if run_dir else [])]
        found = next((c for c in candidates if c.is_file()), None)
        if found is None:
            missing.append(text)
            continue
        found = found.resolve()
        if run_dir and found.is_relative_to(run_dir):
            # At run/<file name>, as the prompt says, wherever the file sits: a rejected output
            # is moved into an archived-* directory before the Investigator runs.
            if copies.setdefault("run/" + found.name, found) != found:
                raise ValueError(f"evidence_refs cite two run files named {found.name}; cite one of them")
        elif not found.is_relative_to(workspace):
            missing.append(text)
    if missing:
        raise ValueError("evidence_refs must name files that exist in the repository or this run's directory; "
                         f"not found there: {missing}")
    return copies


def release_route(state: dict) -> dict:
    """The Investigator's route exists only while it runs: saved roles are re-validated on resume
    (e.g. native Codex runs refuse a non-Codex role), and a pinned route may be an OpenCode one."""
    return (state.get("settings") or {}).get("roles", {}).pop(ROUTE, None) or {}


def apply(state: dict, value: dict, record: dict, workspace, run_probe=None) -> None:
    """``run_probe(command, files)`` runs the probe in a scratch tree with ``files`` copied in (the unit
    passes autocode_verify.scratch_run); without it a probed diagnosis is rejected rather than trusted."""
    request = state['stuck_investigation']
    if request.get('mode') == 'builder_failure':
        try:
            from . import autocode_builder_failure as builder_failure
        except ImportError:
            import autocode_builder_failure as builder_failure
        builder_failure.guard(state, request, workspace)
        failure = request['failure_evidence']
        if (value.get('failure_id') != failure['failure_id'] or not value.get('evidence_refs')
                or not set(value['evidence_refs']) <= set(failure['evidence_refs'])):
            raise ValueError('Classification must cite the same pinned failure identity and allowed evidence')
        check({**value, 'recommendation': 'retry', 'guidance': value['diagnosis']}, record.get('changed_files'))
        if value['cause'] == 'needs_user' and not value['user_question'].strip():
            raise ValueError('A classification that needs the user must state the decision')
    check(value, record.get("changed_files"))
    run_dir = state.get("run_dir") or (Path(record["output"]).parent if record.get("output") else None)
    cited = cited_files(value, workspace, run_dir)
    probe = value.get("probe", "").strip()
    untestable = value.get("untestable", "").strip()
    if probe and request.get('mode') == 'builder_failure':
        # Fail closed where the probe cannot be held read-only (see
        # builder_failure.readonly_probe): do not run an uncontained command and
        # do not reject an otherwise valid classification. Keep it as the
        # explicitly untestable advisory cause the schema already allows.
        reason = builder_failure.probe_containment_error()
        if reason:
            untestable = untestable or reason
            probe = ''
    runner = (lambda command: run_probe(command, cited)) if run_probe else \
        (lambda command: {"error": "no probe runner was given"})
    shown = run_probes([{"id": "the diagnosed cause", "example": value.get("example", ""), "probe": probe}],
                       runner, what="diagnosed cause", key="id") if probe else []
    if request.get('mode') == 'builder_failure':
        builder_failure.guard(state, request, workspace)
    request = state.pop("stuck_investigation")
    used = release_route(state)
    retry = value["recommendation"] == "retry" and request["status"] in RETRYABLE
    outcome = "retried" if retry else "paused"
    entry = next(row for row in reversed(state["stuck_investigations"]) if row["identity"] == request["identity"])
    entry.update(outcome=outcome, diagnosis=value["diagnosis"], cause=value["cause"], guidance=value["guidance"],
                 user_question=value["user_question"], evidence_refs=value["evidence_refs"],
                 example=value.get("example", ""), probe=probe, untestable=untestable,
                 probe_result=shown[0] if shown else None,
                 model=used.get("model"), engine=used.get("engine"), reasoning_effort=used.get("reasoning_effort"),
                  output=record.get("output"), finished_at=now())
    if request.get('mode') == 'builder_failure':
        entry.update(outcome='classified', failure_class=value['failure_class'], failure_id=value['failure_id'])
        if value['recommendation'] == 'pause' or value['cause'] == 'needs_user':
            entry['outcome'] = 'paused'
            question = '\nNeeds you: ' + value['user_question'] if value['user_question'] else ''
            builder_failure.hold(state, request['failure_evidence'], request['status'],
                                 request['reason'] + '\nInvestigator: ' + value['diagnosis'] + question)
            return None
        state.update(status='RUNNING', next_stage='terra', phase=request.get('phase') or 'EXECUTING')
        return {**request, 'diagnosis': {key: value[key] for key in
                ('failure_class', 'failure_id', 'evidence_refs', 'diagnosis', 'guidance', 'example',
                 'recommendation', 'cause', 'user_question')}}
    state["next_stage"] = request["stage"]
    if not retry:
        restore(state, request, annotate(state, request["status"], request["reason"]))
        return
    if request.get("pending_report_repair"):
        state.setdefault("report_repair_archive", []).append({
            "at": now(), "reason": "Superseded by a stuck-stage investigation's retry",
            "repair": request["pending_report_repair"]})
    grant_one_attempt(state, request)
    state["stuck_investigation"] = {**{k: request[k] for k in ("identity", "stage", "status")},
                                    "guidance": value["guidance"], "diagnosis": value["diagnosis"],
                                    "example": value.get("example", ""), "in_force": True}
    state.update(status="RUNNING", phase="PLANNING" if request["stage"] in PLANNING else
                 (request.get("phase") if request.get("phase") not in (None, "PAUSED_OR_BLOCKED") else "EXECUTING"))


def restore(state: dict, request: dict, reason: str) -> None:
    """Put back exactly the pause the investigation interrupted."""
    if request.get("pending_report_repair"):
        state["pending_report_repair"] = request["pending_report_repair"]
    state.update(status=request["status"], phase="PAUSED_OR_BLOCKED", stop_reason=reason,
                 next_stage=request["stage"], paused_at=now())


def grant_one_attempt(state: dict, request: dict) -> None:
    """Reset exactly one attempt's worth of whatever stopped the stage; nothing else changes."""
    status, stage = request["status"], request["stage"]
    # PAUSED_REPEATED_FAILURE / PAUSED_INVALID_OUTPUT: nothing to reset. The stage simply runs
    # again; the failure history stays intact, so another failure counts on top of it.
    if status == "PAUSED_PLANNING_BUDGET":
        planning = state.setdefault("planning", {})
        used = max(int(planning.get("review_call_limit", 2)), int(planning.get("astra_calls", 0)))
        # One review round: a challenge still owes its finalize.
        planning["review_call_limit"] = used + (2 if stage == "astra_challenge" else 1)
    elif status == "PAUSED_NO_PROGRESS":
        limit = ((state.get("settings") or {}).get("limits") or {}).get("no_progress_batches") or 1
        state["no_progress_batches"] = max(0, limit - 1)


def abandon(state: dict, error: str) -> tuple[str, str]:
    """The investigation itself failed: restore the original pause, never a worse one."""
    request = state.pop("stuck_investigation")
    used = release_route(state)
    entry = next(row for row in reversed(state["stuck_investigations"]) if row["identity"] == request["identity"])
    entry.update(outcome="investigation_failed", error=error, model=used.get("model"), finished_at=now())
    reason = f"{request['reason']}\n(The Investigator could not finish: {error})"
    restore(state, request, reason)
    if request.get('mode') == 'builder_failure':
        try:
            from . import autocode_builder_failure as builder_failure
        except ImportError:
            import autocode_builder_failure as builder_failure
        builder_failure.hold(state, request['failure_evidence'], request['status'], reason)
    return request["status"], reason


def with_guidance(state: dict, stage: str, request):
    """Keep report corrections while their investigation identities remain spent."""
    current = state.get("stuck_investigation") or {}
    if stage == STAGE:
        return request
    active = current.get("in_force") and (
        stage == current["stage"] or (current["stage"] in PLANNING and stage in PLANNING))
    lessons = planning_lessons(state) if stage in PLANNING else []
    if active:
        lessons = [row for row in lessons if row.get("identity") != current.get("identity")]
    block = ""
    if lessons:
        block = ("\nEARLIER PLANNING CORRECTIONS\n"
                 "These are earlier report-format diagnoses, not verified successful retries. Keep only "
                 "corrections applicable to the current task, contract and saved answers. "
                 "They grant no retries, budget, permissions or approval and settle no user decision.\n"
                 + "\n".join(f"Diagnosis: {row['diagnosis']}\nCorrection: {row['guidance']}" for row in lessons) + "\n")
    if active:
        block += ("\nINVESTIGATOR GUIDANCE (takes precedence over earlier guidance on conflict). "
                  f"Diagnosis: {current['diagnosis']}\nFollow this guidance on this attempt: {current['guidance']}\n")
    if not block:
        return request
    head, marker, tail = request.prompt.partition("CURRENT HANDOFF DATA\n")
    return replace(request, prompt=head + block + marker + tail if marker else request.prompt + block)


def planning_lessons(state: dict) -> list[dict]:
    """Reuse report-format advice run-wide, matching run-wide investigation identities.

    A clarification, feedback or follow-up does not renew an investigation identity.
    Never resurrect its retry grant or carry convergence/product advice as a lesson.
    Legacy INVALID_OUTPUT rows identify rejected reports even without a trigger.
    """
    lessons = [row for row in state.get("stuck_investigations") or []
               if row.get("outcome") == "retried" and row.get("cause") == "stage_output"
               and row.get("stage") in PLANNING and row.get("diagnosis") and row.get("guidance")
               and (row.get("trigger") == "rejected_output" or
                    ("trigger" not in row and row.get("status") == "PAUSED_INVALID_OUTPUT"))]
    limit = max_calls(state)
    return lessons[-limit:] if limit else []


def settle(state: dict, stage: str) -> None:
    """A stage completed: non-planning guidance is spent once its stage succeeds."""
    current = state.get("stuck_investigation") or {}
    if current.get("in_force") and stage == current["stage"] and stage not in PLANNING:
        state.pop("stuck_investigation")


def retire(state: dict) -> None:
    """The run left RUNNING: guidance in force ends with the cycle it was given for."""
    if (state.get("stuck_investigation") or {}).get("in_force"):
        state.pop("stuck_investigation")


def drive(state, dispatch, *, apply=None, before=None, after=None, persist=None, active, skip, paused,
          investigate=False):
    """The runner's stage loop (autopilot.drive), with investigation before a non-convergence pause.

    ``paused`` is the runner's pause exception type (with ``status``); ``skip`` its restart sentinel.
    Without ``investigate`` this is exactly the plain loop.

    Only a pause this invocation produced by attempting a stage (a new stage record) is
    investigated: a guard re-asserting a known pause on resume launches nothing, since that
    pause was investigated when it happened or is now the user's decision.
    """
    # Runner-owned records (orchestration, regression proof, AutoResolver receipts) are not attempts.
    attempts = lambda: sum(1 for row in state.get("stages") or [] if not row.get("runner_owned"))
    attempted = attempts()
    fresh = lambda: attempts() > attempted
    while True:
        try:
            while active(state):
                if before and before(state) is skip:
                    continue
                stage = state.get("next_stage")
                if not isinstance(stage, str) or not stage:
                    raise ValueError("Running orchestration has no next stage")
                outcome = dispatch(state, stage)
                if outcome is skip:
                    continue
                if apply and apply(state, stage, outcome) is skip:
                    continue
                if persist:
                    persist(state)
                if after:
                    after(state, stage, outcome)
                if investigate:
                    settle(state, stage)
        except paused as error:
            if not investigate:
                raise
            status, reason = getattr(error, "status", ""), str(error)
            current = state.get("stuck_investigation") or {}
            if current and not current.get("in_force") and state.get("next_stage") == STAGE:
                if state.get("active_stage"):
                    # An unreconciled attempt pauses like any stage; the investigation reruns after.
                    raise
                status, reason = abandon(state, reason)
                if persist:
                    persist(state)
                reraised = paused(status, reason)
                if getattr(error, "quota_worker", None):
                    reraised.quota_worker = error.quota_worker
                raise reraised from error
            retire(state)
            if not fresh():
                raise
            if not intercept(state, status, reason):
                reraised = paused(status, annotate(state, status, reason))
                if getattr(error, "quota_worker", None):
                    reraised.quota_worker = error.quota_worker
                raise reraised from error
            if persist:
                persist(state)
            continue
        if not investigate:
            return state
        retire(state)
        if fresh() and intercept(state, state.get("status"), state.get("stop_reason") or ""):
            if persist:
                persist(state)
            continue
        if state.get("status") in STATUSES and state.get("stop_reason"):
            state["stop_reason"] = annotate(state, state["status"], state["stop_reason"])
        return state


def owns(state: dict) -> bool:
    """An investigation never completes a run: it retries the stuck stage or restores its pause."""
    return False


def render(state: dict) -> str:
    return ""
