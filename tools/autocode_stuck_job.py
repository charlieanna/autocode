"""When a stage stops making progress, investigate before pausing for the user.

Today a stage that does not converge (its report is rejected again and again, the
Planner and Plan Reviewer cannot agree within the review budget, the Builder makes no
progress, the Completion Owner will not decide) repairs, loops, then pauses and waits
for a person. Most of those pauses have a cause a stronger model can find by reading
the saved exchange: a rule the stage misunderstands, two roles talking past each other,
a missing fact the runner already has.

So before the runner pauses with one of ``STATUSES``, ``drive`` sends the run to
``STAGE``: a read-only Investigator on a different model from the stuck stage's (``route``:
Claude Opus 5.5 in kilocode runs, otherwise GPT-6 Sol, or GLM 5.3 when the stuck stage runs on a GPT
model, or on native Codex Luna when it runs on Sol) reads the task, the run's saved state and the
stuck stage's attempts, and returns a diagnosis plus either guidance for one more
attempt or the question only the user can answer. Then:

- ``retry`` (only for ``RETRYABLE`` statuses): the runner resets exactly one attempt's
  worth of the limit that stopped the stage and runs it again with the guidance in its
  prompt (``with_guidance``). Planning guidance stays in force for every planning stage
  until the plan is presented; any other stage's guidance until that stage completes.
  Earlier report-format diagnoses remain context only while the diagnosed planning
  inputs match. Spent investigation identities remain run-wide; they grant no additional retry.
- ``pause``, a ``DIAGNOSE_ONLY`` status, or a failed investigation: the original pause
  is restored exactly, with the diagnosis added to its reason.

Bounds: one investigation per distinct (stage, pause) problem on one source revision, and
one more for a rejected answer AutoCode then retried itself up to its limit
(autocode_rejection_retry: ``retry_rejected`` before the pause, ``second_look`` here), and
``MAX_CALLS`` per run, or one per approved milestone when there are more
(``settings.stuck_investigation.max_calls_per_run``; 0 disables). A Builder failure's
classification (autocode_builder_failure) is not counted here: it has its own budget per
milestone (#686). The Investigator
cannot approve work, change requirements or criteria, weaken tests, grant permissions,
spend beyond the one attempt, or write anything (it runs read-only). Decisions that belong to the
user (permissions, scope, goal and criteria changes, spend limits) never come here.

A retry is checked, not trusted. The Investigator states the diagnosed cause as a
plain-English ``example`` and, unless it says why none can (``untestable``), a ``probe``:
a command that exits 0 exactly when the files it cites in ``evidence_refs`` show that
cause. Every cited file must exist (in the repository or this run's directory). The runner
copies only the cited run files into a scratch tree, under ``run/``, and runs the probe
there (autocode_test_cases.run_probes); a probe that does not exit 0, or that needs an
uncited file, rejects the report and the original pause is restored. A retry's probe also
names its ``counterexample``, one edit to a cited file after which that file no longer shows
the cause; the runner makes it in another scratch tree and the probe must fail there, so a
probe that passes whatever the files say cannot send a stage back as diagnosed (#1769).

Pure module: prompt, schema, transitions and the drive loop. Imports nothing from the
runner; the runner passes its pause exception type in, and the unit passes the function
that runs the probe. State keys written:
``stuck_investigation`` (the one in progress or in force) and ``stuck_investigations``
(history).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


import copy
import datetime as dt
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

try:
    from . import autocode_classification_recovery as classification_recovery
    from . import autocode_failures as failures
    from . import autocode_model_identity as model_identity
    from . import autocode_planning_advice as recovery_binding
    from . import autocode_planning_calls as planning_calls
    from . import autocode_progressive_state as progressive
    from . import autocode_project_notes_lifecycle as project_notes
    from . import autocode_recovery_accounting as recovery_accounting
    from . import autocode_rejection_retry as rejection_retry
    from . import autocode_roles as roles
    from . import autocode_run_status as run_status
    from . import autocode_stage_access as stage_access
    from . import autocode_stray_writes as stray_writes
    from .autocode_investigation_policy import MAX_CALLS as MAX_CALLS
    from .autocode_investigation_policy import enabled, max_calls
    from .autocode_test_cases import COUNTEREXAMPLE, run_probes
except ImportError:
    import autocode_classification_recovery as classification_recovery
    import autocode_failures as failures
    import autocode_model_identity as model_identity
    import autocode_planning_advice as recovery_binding
    import autocode_planning_calls as planning_calls
    import autocode_progressive_state as progressive
    import autocode_project_notes_lifecycle as project_notes
    import autocode_recovery_accounting as recovery_accounting
    import autocode_rejection_retry as rejection_retry
    import autocode_roles as roles
    import autocode_run_status as run_status
    import autocode_stage_access as stage_access
    import autocode_stray_writes as stray_writes
    from autocode_investigation_policy import MAX_CALLS as MAX_CALLS
    from autocode_investigation_policy import enabled, max_calls
    from autocode_test_cases import COUNTEREXAMPLE, run_probes

STAGE = "investigate_stuck"
ROUTE = "stuck_investigator"
# The runner can reset one attempt's worth of what stopped these, so a retry is well-defined.
RETRYABLE = frozenset(
    {
        "PAUSED_REPEATED_FAILURE",
        "PAUSED_INVALID_OUTPUT",
        "PAUSED_PLANNING_BUDGET",
        "PAUSED_NO_PROGRESS",
        "PAUSED_COMPLETION_REVIEW",
        # The automatic timeout recoveries ran out. Without this the run asked a person to grant more
        # (--grant-recovery), a question about AutoCode's own retries (2026-11-06 live run).
        "PAUSED_TIMEOUT_RECOVERY",
        # A stage that spent its whole output limit on reasoning at the lowest level its model offers
        # asked the person for another model (#3349). Only the runner's own recoverer sends it here
        # (autocode_output_cap_recovery), after setting the attempt aside: RUNNER_ROUTED.
        "PAUSED_OUTPUT_CAP",
    }
)
# Statuses the Investigator takes only when the runner's own recoverer routes them (``routed``), never
# from a raised pause: their stopped attempt must first be set aside by that recoverer.
RUNNER_ROUTED = frozenset({"PAUSED_OUTPUT_CAP"})
# These have operator-only resume semantics; the Investigator explains them, the user decides.
DIAGNOSE_ONLY = frozenset(
    {"PAUSED_REPORT_REPAIR_LIMIT", "PAUSED_BUILDER_RETRY_LIMIT", "PAUSED_MILESTONE_STALLED", "PAUSED_MILESTONE_REPLAN"}
)
STATUSES = RETRYABLE | DIAGNOSE_ONLY
# Pauses the runner publishes as an AutoResolver operational request when they reach it raised. An
# investigation that ends in one of them raises it again, so it is published as before.
PUBLISHED = frozenset({"PAUSED_TIMEOUT_RECOVERY"})
# Run-wide holds checked at every stage boundary: the bounds, and a provider transport that changed
# under the run. One reached before a queued Builder classification launched stops the run, not the
# Investigator (#1949): the classification stays queued under that pause, whose own control (a raise,
# --accept-transport-change) lets it run, and no classification is spent.
BOUNDARY_LIMITS = frozenset(
    {
        "PAUSED_TIME_LIMIT",
        "PAUSED_COST_LIMIT",
        "PAUSED_ITERATION_LIMIT",
        planning_calls.STATUS,
        "PAUSED_TRANSPORT_CHANGED",
    }
)
PLANNING = roles.ALL_PLANNING_STAGES
# Diagnosticians are never themselves investigated.
NEVER = (STAGE, "diagnose", "resolve")
TEXT = {"type": "string"}
TEXTS = {"type": "array", "items": TEXT}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "diagnosis",
        "cause",
        "guidance",
        "recommendation",
        "user_question",
        "evidence_refs",
        "example",
        "probe",
        "untestable",
    ],
    "properties": {
        "diagnosis": TEXT,
        # example: the diagnosed cause as one concrete case in plain English; probe: a shell command
        # that exits 0 exactly when the cited files show it; counterexample: the edit to one cited file
        # after which it no longer does, where the probe must fail (#1769); untestable: why no command
        # can. A retry needs the example and exactly one of probe / untestable. counterexample is not
        # required here, so reports saved before it still load.
        "example": TEXT,
        "probe": TEXT,
        "counterexample": COUNTEREXAMPLE,
        "untestable": TEXT,
        "cause": {
            "type": "string",
            "enum": ["stage_output", "role_disagreement", "missing_information", "environment", "needs_user", "other"],
        },
        "guidance": TEXT,
        "recommendation": {"type": "string", "enum": ["retry", "pause"]},
        "user_question": TEXT,
        "evidence_refs": TEXTS,
        # Facts about the repository the next task would otherwise pay to discover again (#2943). Not
        # required; kept only when this report's probe shows its cause (autocode_project_notes_lifecycle).
        "project_notes": project_notes.REPORT_SCHEMA,
    },
}


def schema(state: RunState):
    if (state.get("stuck_investigation") or {}).get("mode") != "builder_failure":
        return SCHEMA
    result: dict[str, Any] = copy.deepcopy(SCHEMA)
    result["required"] += ["failure_class", "failure_id"]
    result["properties"].update(
        failure_class={"type": "string", "enum": ["plan", "execution", "operational", "unknown"]}, failure_id=TEXT
    )
    failure = (state.get("stuck_investigation") or {}).get("failure_evidence") or {}
    if (failure.get("diagnosis") or {}).get("changed_code_evidence"):
        # Test gaps are internal repair work. The Investigator can diagnose a
        # bounded stop, but cannot manufacture a question for the person.
        result["properties"]["cause"]["enum"] = [
            value for value in result["properties"]["cause"]["enum"] if value != "needs_user"
        ]
        result["properties"]["user_question"] = {"type": "string", "enum": [""]}
    return result


# Repeated in a report-only repair of this stage (autocode_jobs.repair_rules): without them, both
# repairs of a live run's rejected probe guessed where the cited files would be (2026-09-29).
EVIDENCE_RULES = prompts.get("fragments/stuck-job/evidence-rules.md")
REPAIR_RULES = prompts.get("fragments/stuck-job/repair-rules.md") + EVIDENCE_RULES

PROMPT = (
    prompts.get("stuck-investigation-02.md")
    + project_notes.INVESTIGATOR_RULE
    + EVIDENCE_RULES
    + prompts.get("stuck-investigation.md")
)


def now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def identity(stage: str, status: str) -> str:
    return f"{stage}:{status}"


def source_revision(state: RunState, stage: str):
    """The source the stuck stage's latest attempt saw, or None when it recorded none."""
    last: dict = next(
        (
            row
            for row in reversed(state.get("stages") or [])
            if (row.get("original_stage") or row.get("stage")) == stage
        ),
        {},
    )
    return last.get("source_revision")


def same_problem(entry: dict, key: str, revision) -> bool:
    """One (stage, pause) problem on one source. An entry saved before revisions were recorded, or a
    stage that records none, matches its identity on any source, as before."""
    if entry.get("identity") != key:
        return False
    seen = entry.get("source_revision")
    return seen is None or revision is None or seen == revision


def intercept(state: RunState, status: str, reason: str, *, routed: bool = False) -> bool:
    """Send a run about to pause with a non-convergence ``status`` to the Investigator.

    Returns False (pause as before) when the feature is off, the status is not one of
    ``STATUSES``, a provider attempt is still unreconciled, this problem was already
    investigated, or the run's investigation budget is spent.
    """
    stuck = state.get("next_stage")
    if status in RUNNER_ROUTED and not routed:
        return False
    if progressive.retained_review_budget_pause(state, status) or persons_review_total(state, status):
        return False
    if (
        not enabled(state)
        or status not in STATUSES
        or state.get("active_stage")
        or not isinstance(stuck, str)
        or not stuck
        or stuck in NEVER
    ):
        return False
    history = state.get("stuck_investigations") or []
    key = identity(stuck, status)
    revision = source_revision(state, stuck)
    spent = sum(entry.get("trigger") != "builder_failure" for entry in history)
    seen = [entry for entry in history if same_problem(entry, key, revision)]
    # A stage AutoCode kept retrying itself after the first look gets one more (autocode_rejection_retry).
    again = bool(seen) and rejection_retry.second_look(state, stuck, status, seen)
    if (
        # The same problem again on the same source: once is enough. A Builder that changed the
        # source makes it a new problem, still within the run's budget.
        (seen and not again) or spent >= max_calls(state) or operator_retried(state, stuck)
    ):
        return False
    if again:
        attempts = len(failures.episode_rejections(state, stuck))
        reason += (
            f"\nThis is the second look: after the first retry, {attempts} attempts at this source were each "
            "rejected, with errors that changed instead of repeating. Find what keeps the answers from passing together."
        )
    history = state.setdefault("stuck_investigations", [])
    request: dict[str, Any] = {
        "identity": key,
        "stage": stuck,
        "status": status,
        "reason": reason,
        "phase": state.get("phase"),
        "requested_at": now(),
        **recovery_binding.binding(state, stuck),
    }
    if state.get("pending_report_repair"):
        # The before-stage hook would replay a spent repair ahead of the investigation.
        request["pending_report_repair"] = state.pop("pending_report_repair")
    state["stuck_investigation"] = request
    last: dict = next(
        (row for row in reversed(state.get("stages", [])) if (row.get("original_stage") or row.get("stage")) == stuck),
        {},
    )
    # Written here once; planning_lessons uses the trigger even when repeated
    # rejected reports become PAUSED_REPEATED_FAILURE instead of INVALID_OUTPUT.
    trigger = (
        "rejected_output"
        if (status == "PAUSED_INVALID_OUTPUT" or (status == "PAUSED_REPEATED_FAILURE" and last.get("rejected")))
        else "non_convergence"
    )
    history.append(
        {
            "identity": key,
            "stage": stuck,
            "status": status,
            "reason": reason,
            "requested_at": request["requested_at"],
            "outcome": "investigating",
            "trigger": trigger,
            **({"source_revision": revision} if revision else {}),
            **recovery_binding.binding(state, stuck),
        }
    )
    pause_fields: tuple[Literal["stop_reason", "paused_at"], ...] = ("stop_reason", "paused_at")
    for field in pause_fields:
        state.pop(field, None)
    state.update({"status": "RUNNING", "phase": "INVESTIGATING", "next_stage": STAGE})
    return True


def _seen(state: RunState, stage: str, status: str) -> list[dict]:
    """The investigations of this (stage, pause) problem on the stage's current source."""
    key, revision = identity(stage, status), source_revision(state, stage)
    return [entry for entry in state.get("stuck_investigations") or [] if same_problem(entry, key, revision)]


def retry_rejected(state: RunState, status: str) -> bool:
    """AutoCode runs a rejected stage again itself after its investigation (autocode_rejection_retry)."""
    stage = state.get("next_stage")
    if not isinstance(stage, str) or not stage or stage in NEVER or operator_retried(state, stage):
        return False
    return rejection_retry.admit(state, stage, status, _seen(state, stage, status), at=now())


def stopped(state: RunState, status: str, reason: str) -> str:
    """The pause reason once AutoCode stopped retrying a rejected stage itself."""
    stage = state.get("next_stage")
    if not isinstance(stage, str) or not stage:
        return reason
    return rejection_retry.stop_reason(state, stage, status, _seen(state, stage, status), reason)


def persons_review_total(state: RunState, status: str) -> bool:
    """A planning-budget pause at the review total the person chose in this run (--planning-review-call-limit).

    Nothing there needs investigating: the runner keeps the counter itself and the person set the number, so
    an Investigator call only cost a model call before the same pause (#3094), and its retry would raise the
    person's own limit. The pause goes straight back to them with its typed allowance offer."""
    return (
        status == "PAUSED_PLANNING_BUDGET"
        and (state.get("planning") or {}).get("review_call_limit_origin") == "user_explicit"
    )


def operator_retried(state: RunState, stage: str) -> bool:
    """An operator authorized a retry of this stage's failure (--retry-failed-stage): its
    outcome goes back to the operator, who is already handling that exact failure."""
    keys = {
        row.get("failure_key")
        for row in state.get("stages") or []
        if row.get("stage") == stage and row.get("failure_key")
    }
    return any(grant.get("failure_key") in keys for grant in state.get("failure_retry_authorizations") or [])


def annotate(state: RunState, status: str, reason: str) -> str:
    """A pause reason with the Investigator's diagnosis of the same problem, when there is one."""
    stuck = state.get("next_stage")
    entry = next(
        (
            row
            for row in reversed(state.get("stuck_investigations", []))
            if same_problem(row, identity(stuck or "", status), source_revision(state, stuck or ""))
            and row.get("diagnosis")
            and recovery_binding.applies(state, row, stuck or "")
        ),
        None,
    )
    if not entry or "\nInvestigator (" in reason:
        return reason
    ask = f" Needs you: {entry['user_question']}" if entry.get("user_question") else ""
    return f"{reason}\nInvestigator ({entry['outcome']}): {entry['diagnosis']}{ask}"


# The run's own direct routes a default Investigator may use, most independent judge first. Never the Resolver's
# Astra (user 2026-09-28: too expensive) and never a kilo/ gateway route (user 2026-10-21: direct logins only), so a
# gateway model needs --investigator-model.
CANDIDATE_ROLES = ("plan_reviewer", "planner", "requirements", "completion", "tester", "builder")
GATEWAY_PREFIX = "kilo/"


def family(model) -> str:
    """The model family a route belongs to, so the Investigator never judges its own: the one rule in
    autocode_model_identity (gpt-6-sol and gpt-6-luna are one family, zai-coding-plan/glm-5.3 and
    zhipuai-coding-plan/glm-5.3 are glm, kilo/anthropic/claude-opus-5.5 is claude)."""
    return model_identity.family(str(model or ""))


def direct_default(roles: dict, stuck_model: str) -> str | None:
    """The first configured direct route outside the stuck stage's family, in CANDIDATE_ROLES order; None if none."""
    seen = []
    for role in CANDIDATE_ROLES:
        model = str((roles.get(role) or {}).get("model") or "")
        if not model or model in seen or model.startswith(GATEWAY_PREFIX) or "astra" in model:
            continue
        seen.append(model)
    return next((model for model in seen if family(model) != family(stuck_model)), None)


def route(roles: dict, stuck_model: str, provider: str | None = None) -> dict:
    """The Investigator's default route (user 2026-09-28: no Astra; 2026-10-21: no gateway route by default).

    kilocode runs: the run's own direct routes, the first outside the stuck stage's family (direct_default); the
    Kilo Gateway's Claude, the old default, bills per token and now needs --investigator-model. OpenCode runs: GPT-6
    Sol, or GLM 5.3 when the stuck stage runs on any GPT model (one family). Native Codex runs (GPT only): GPT-6 Sol,
    or Luna when the stuck stage runs on Sol. A custom provider keeps its reviewer model. Always high effort, on the
    Plan Reviewer's engine and provider; --investigator-model overrides all of this (pinned_route)."""
    base = dict(roles.get("plan_reviewer") or roles.get("task_planner") or {})
    model, stuck_model = str(base.get("model") or ""), str(stuck_model or "")
    if provider == "kilocode":
        choice = direct_default(roles, stuck_model) or model
    elif model.startswith(("openai/", "zai-coding-plan/", "kilo/")):
        # All GPT models are one family (2026-10-20): a stuck Luna or Sol stage gets GLM's view.
        choice = "zai-coding-plan/glm-5.3" if "gpt-" in stuck_model else "openai/gpt-6-sol"
    elif model.startswith("gpt-"):
        choice = "gpt-6-luna" if "gpt-6-sol" in stuck_model else "gpt-6-sol"
    else:
        choice = model
    base.update(model=choice, reasoning_effort="high")
    return base


EFFORTS = ("low", "medium", "high", "xhigh", "max")


def add_arguments(parser) -> None:
    """The Investigator's CLI flags (autocode.py): pin its model instead of the automatic choice."""
    parser.add_argument(
        "--investigator-model",
        help="Pin the stuck-stage Investigator's model for this run; a provider/model id "
        "(e.g. openai/gpt-6-sol) runs it through OpenCode. Automatic runs reuse an independent "
        "permitted reviewer route. Fixed-route default: in kilocode runs the run's own direct route outside "
        "the stuck stage's model family (never a kilo/ gateway route), otherwise GPT-6 Sol (GLM 5.3 when the "
        "stuck stage runs on Sol)",
    )
    parser.add_argument(
        "--investigator-reasoning-effort",
        choices=EFFORTS,
        help="Reasoning effort for the pinned Investigator model (default: high)",
    )


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
        "model": model,
        "reasoning_effort": effort or saved.get("reasoning_effort") or "high",
        "provider": None,
        # provider/model ids are OpenCode routes (the repository's convention); bare names use the run's engine.
        "engine": "opencode" if "/" in model else settings.get("engine", "codex"),
    }
    return settings


def pinned_route(settings: dict) -> dict | None:
    route = ((settings or {}).get("stuck_investigation") or {}).get("route")
    return dict(route) if route else None


def packet(state: RunState, state_path, inventory: dict | None = None, engine: str | None = None) -> dict:
    request = state["stuck_investigation"]
    recent = [
        {
            key: row.get(key)
            for key in ("stage", "iteration", "output", "rejected", "rejection_reason")
            if row.get(key) is not None
        }
        for row in state.get("stages", [])[-12:]
    ]
    failures = {
        key: {k: entry.get(k) for k in ("identity", "count", "last_error", "output_probe")}
        for key, entry in (state.get("failure_history") or {}).items()
        if (entry.get("identity") or {}).get("stage") == request["stage"]
    }
    return {
        "stage": STAGE,
        "task": state["task"],
        "workspace": state.get("workspace"),
        "state_file": str(state_path),
        "stuck": {key: request[key] for key in ("stage", "status", "reason")},
        "recent_stages": recent,
        "failure_history": failures,
        "previous_investigations": [
            row for row in state.get("stuck_investigations", []) if row.get("identity") != request["identity"]
        ],
        "execution_engine": engine,
        "workspace_inventory": inventory or {},
        # Present because every provider reads them.
        "goal_contract": state.get("goal_contract"),
        "current_task": state.get("current_task"),
        "saved_answers": state.get("answers", {}),
        **({"builder_failure": request["failure_evidence"]} if request.get("mode") == "builder_failure" else {}),
        # What earlier runs learned about this repository; advisory, never authority (#2943).
        **({"project_notes": notes} if (notes := project_notes.context(state)) else {}),
    }


def prompt(
    state: RunState,
    state_path,
    inventory: dict | None = None,
    soft_budget_tokens: int = 10000,
    engine: str | None = None,
) -> tuple[str, dict]:
    text = (
        PROMPT
        + "\nCURRENT HANDOFF DATA\n"
        + json.dumps(packet(state, state_path, inventory, engine), separators=(",", ":"))
    )
    if state["stuck_investigation"].get("mode") == "builder_failure":
        text = prompts.get("fragments/stuck-job/prompt.md") + text
    return text, {"estimated_prompt_tokens": (len(text.encode()) + 3) // 4, "soft_budget_tokens": soft_budget_tokens}


def check(value: dict, changed_files) -> None:
    stray = stage_access.stray(STAGE, changed_files)
    if stray:
        raise stray_writes.StrayWrites(
            "An investigation must not change the repository; this attempt changed: " + ", ".join(stray), stray
        )
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
            raise ValueError(
                "A retry needs exactly one of probe (a command that exits 0 exactly when the cited "
                "files show the cause) or untestable (why no command can)"
            )
        if not [ref for ref in value["evidence_refs"] if str(ref).strip()]:
            raise ValueError("A retry must cite the files it relied on in evidence_refs")


def cited_files(value: dict, workspace, run_dir) -> dict[str, Path]:
    """Resolve evidence_refs; return the run-directory ones as {run/<file name>: source file}.

    A ref is a repository path, a path under this run's directory (relative or absolute), optionally
    with a ``:line`` suffix. Raises ValueError naming refs that do not exist or lie elsewhere.
    """
    workspace = Path(workspace).resolve()
    run_dir = Path(run_dir).resolve() if run_dir else None
    copies: dict[Any, Any] = {}
    missing: list[Any] = []
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
        raise ValueError(
            "evidence_refs must name files that exist in the repository or this run's directory; "
            f"not found there: {missing}"
        )
    return copies


def release_route(state: RunState) -> dict:
    """The Investigator's route exists only while it runs: saved roles are re-validated on resume
    (e.g. native Codex runs refuse a non-Codex role), and a pinned route may be an OpenCode one."""
    return (state.get("settings") or {}).get("roles", {}).pop(ROUTE, None) or {}


def apply(
    state: RunState, value: dict, record: dict, workspace, run_probe=None, run_counterexample=None
) -> dict | None:
    """``run_probe(command, files)`` runs the probe in a scratch tree with ``files`` copied in (the unit
    passes autocode_verify.scratch_run), and ``run_counterexample(command, counterexample, files)`` runs
    it there with the counterexample made (autocode_counterexample.runner); without them a probed
    diagnosis is rejected rather than trusted."""
    request = state["stuck_investigation"]
    if request.get("mode") == "builder_failure":
        try:
            from . import autocode_builder_failure as builder_failure
        except ImportError:
            import autocode_builder_failure as builder_failure
        builder_failure.guard(state, request, workspace)
        failure = request["failure_evidence"]
        if (
            value.get("failure_id") != failure["failure_id"]
            or not value.get("evidence_refs")
            or not set(value["evidence_refs"]) <= set(failure["evidence_refs"])
        ):
            raise ValueError("Classification must cite the same pinned failure identity and allowed evidence")
        check({**value, "recommendation": "retry", "guidance": value["diagnosis"]}, record.get("changed_files"))
        if value["cause"] == "needs_user" and not value["user_question"].strip():
            raise ValueError("A classification that needs the user must state the decision")
    check(value, record.get("changed_files"))
    run_dir = state.get("run_dir") or (Path(record["output"]).parent if record.get("output") else None)
    cited = cited_files(value, workspace, run_dir)
    probe = value.get("probe", "").strip()
    untestable = value.get("untestable", "").strip()
    if probe and request.get("mode") == "builder_failure":
        # Fail closed where the probe cannot be held read-only (see
        # builder_failure.readonly_probe): do not run an uncontained command and
        # do not reject an otherwise valid classification. Keep it as the
        # explicitly untestable advisory cause the schema already allows.
        reason = builder_failure.probe_containment_error()
        if reason:
            untestable = untestable or reason
            probe = ""
    runner = (
        (lambda command: run_probe(command, cited))
        if run_probe
        else (lambda command: {"error": "no probe runner was given"})
    )
    against = (
        (lambda command, edit: run_counterexample(command, edit, cited))
        if run_counterexample
        else (lambda command, edit: {"error": "no counterexample runner was given"})
    )
    shown = (
        run_probes(
            [
                {
                    "id": "the diagnosed cause",
                    "example": value.get("example", ""),
                    "probe": probe,
                    "counterexample": value.get("counterexample"),
                }
            ],
            runner,
            what="diagnosed cause",
            key="id",
            run_counterexample=against,
        )
        if probe
        else []
    )
    if request.get("mode") == "builder_failure":
        builder_failure.guard(state, request, workspace)
    request = state.pop("stuck_investigation")
    used = release_route(state)
    retry = value["recommendation"] == "retry" and request["status"] in RETRYABLE
    outcome = "retried" if retry else "paused"
    entry = next(row for row in reversed(state["stuck_investigations"]) if row["identity"] == request["identity"])
    entry.update(
        outcome=outcome,
        diagnosis=value["diagnosis"],
        cause=value["cause"],
        guidance=value["guidance"],
        user_question=value["user_question"],
        evidence_refs=value["evidence_refs"],
        example=value.get("example", ""),
        probe=probe,
        untestable=untestable,
        probe_result=shown[0] if shown else None,
        model=used.get("model"),
        engine=used.get("engine"),
        reasoning_effort=used.get("reasoning_effort"),
        output=record.get("output"),
        finished_at=now(),
    )
    noted = project_notes.proposals(value, shown, entry, run_dir=run_dir)
    if noted:
        entry["project_notes"] = noted
    if request.get("mode") == "builder_failure":
        entry.update(outcome="classified", failure_class=value["failure_class"], failure_id=value["failure_id"])
        if value["recommendation"] == "pause" or value["cause"] == "needs_user":
            entry["outcome"] = "paused"
            question = "\nNeeds you: " + value["user_question"] if value["user_question"] else ""
            builder_failure.hold(
                state,
                request["failure_evidence"],
                request["status"],
                request["reason"] + "\nInvestigator: " + value["diagnosis"] + question,
            )
            return None
        state.update({"status": "RUNNING", "next_stage": "builder", "phase": request.get("phase") or "EXECUTING"})
        return {
            **request,
            "diagnosis": {
                key: value[key]
                for key in (
                    "failure_class",
                    "failure_id",
                    "evidence_refs",
                    "diagnosis",
                    "guidance",
                    "example",
                    "recommendation",
                    "cause",
                    "user_question",
                )
            },
        }
    state["next_stage"] = request["stage"]
    if not retry:
        restore(state, request, annotate(state, request["status"], request["reason"]))
        return None
    pending = request.get("pending_report_repair")
    if pending and repairs_saved_report(request, value, pending):
        # The rejected report is kept and repaired once more under the guidance, instead of a fresh
        # attempt redoing everything the earlier repairs already fixed and failing anew (#2066 rerun).
        state["pending_report_repair"] = {**pending, "attempts": pending["attempts"] - 1}
        entry["retry_as"] = "report_repair"
    elif pending:
        state.setdefault("report_repair_archive", []).append(
            {
                "at": now(),
                "reason": "Superseded by a stuck-stage investigation's retry",
                "repair": pending,
            }
        )
    grant_one_attempt(state, request)
    state["stuck_investigation"] = {
        **{k: request[k] for k in ("identity", "stage", "status")},
        **{key: request[key] for key in ("planning_context", "execution_context") if key in request},
        "guidance": value["guidance"],
        "diagnosis": value["diagnosis"],
        "example": value.get("example", ""),
        "in_force": True,
    }
    state.update(
        {
            "status": "RUNNING",
            "phase": cast(
                "str",
                "PLANNING"
                if request["stage"] in PLANNING
                else request.get("phase")
                if request.get("phase") not in (None, run_status.PAUSE_PHASE)
                else "EXECUTING",
            ),
        }
    )
    return None


def repairs_saved_report(request: dict, value: dict, pending: dict) -> bool:
    """Whether the Investigator's retry repairs the stage's saved rejected report rather than starting over.

    Only a rejected answer whose cause the Investigator puts in the report itself (``stage_output``),
    when the stage already has a report-only repair of that same stage on record: one more repair of
    that report, with the guidance in its prompt, is the smallest retry. Any other cause, or no saved
    repair, runs the stage again as before.
    """
    original = pending.get("original")
    attempts = pending.get("attempts")
    return (
        request.get("status") == "PAUSED_INVALID_OUTPUT"
        and value.get("cause") == "stage_output"
        and isinstance(original, dict)
        and original.get("stage") == request.get("stage")
        and type(attempts) is int
        and attempts >= 1
    )


def restore(state: RunState, request: dict, reason: str) -> None:
    """Put back exactly the pause the investigation interrupted."""
    if request.get("pending_report_repair"):
        state["pending_report_repair"] = request["pending_report_repair"]
    run_status.pause(state, request["status"], reason, next_stage=request["stage"], paused_at=now())


def grant_one_attempt(state: RunState, request: dict) -> None:
    """Reset exactly one attempt's worth of whatever stopped the stage; nothing else changes."""
    status, stage = request["status"], request["stage"]
    # PAUSED_REPEATED_FAILURE / PAUSED_INVALID_OUTPUT: nothing to reset. The stage simply runs
    # again; the failure history stays intact, so another failure counts on top of it.
    if status == "PAUSED_PLANNING_BUDGET":
        planning = state.setdefault("planning", {})
        used = max(int(planning.get("review_call_limit", 2)), int(planning.get("plan_review_calls", 0)))
        # One review round: a challenge still owes its finalize.
        planning["review_call_limit"] = used + (2 if stage == "review_plan" else 1)
        if planning.get("review_call_limit_origin") is None:
            # This new allowance is core-issued. Carry it with its spent calls
            # across same-turn replanning, without relabeling an explicit limit.
            planning["review_call_limit_origin"] = "runner_default"
    elif status == "PAUSED_NO_PROGRESS":
        limit = ((state.get("settings") or {}).get("limits") or {}).get("no_progress_batches") or 1
        state["no_progress_batches"] = max(0, limit - 1)
    elif status == "PAUSED_TIMEOUT_RECOVERY":
        # One more automatic recovery on each counter the exhaustion stop reads
        # (autocode_recovery_limits.stop_reason); the recovery history stays intact.
        state["automatic_recoveries_since_resume"] = max(0, recovery_accounting.spent(state) - 1)
        state["consecutive_timeout_recoveries"] = max(0, recovery_accounting.consecutive_timeouts(state) - 1)


def launched(state: RunState, request: dict) -> bool:
    """Whether a Builder classification's Investigator has started since it was queued.

    Only that request waits out a boundary limit: any other investigation, or one that started,
    is abandoned as before."""
    if request.get("mode") != "builder_failure" or not request.get("requested_at"):
        return True
    return any(
        (row.get("original_stage") or str(row.get("stage", "")).removesuffix("_report_repair")) == STAGE
        and row.get("started_at", "") >= request["requested_at"]
        for row in state.get("stages") or []
    )


def timed_out(state: RunState) -> bool:
    """The Investigator itself ran out of time, after AutoCode's own longer deadlines.

    The job runner then pauses for an exact retry of the Investigator (autocode_job_failure), a
    question about AutoCode's machinery; a live run (2026-11-06) stopped on "Continue?" that way.
    The investigation is abandoned instead, so the stuck stage's own pause comes back with its own
    action. A model stop (quota, refusal, output) keeps its model question, and a Builder failure
    classification keeps its exact retry: that classification still needs an Investigator."""
    current = state.get("stuck_investigation") or {}
    failure = state.get("job_failure") or {}
    return (
        bool(current)
        and not current.get("in_force")
        and current.get("mode") != "builder_failure"
        and state.get("status") == "PAUSED_JOB_FAILURE"
        and state.get("next_stage") == STAGE
        and failure.get("stage") == STAGE
        and failure.get("kind") == "timeout"
    )


def abandon(state: RunState, error: str) -> tuple[str, str]:
    """The investigation itself failed: restore the original pause, never a worse one."""
    request = state.pop("stuck_investigation")
    used = release_route(state)
    entry = next(row for row in reversed(state["stuck_investigations"]) if row["identity"] == request["identity"])
    entry.update(outcome="investigation_failed", error=error, model=used.get("model"), finished_at=now())
    reason = f"{request['reason']}\n(The Investigator could not finish: {error})"
    restore(state, request, reason)
    if request.get("mode") == "builder_failure":
        try:
            from . import autocode_builder_failure as builder_failure
        except ImportError:
            import autocode_builder_failure as builder_failure
        builder_failure.hold(state, request["failure_evidence"], request["status"], reason)
        try:
            from . import autocode_classification_recovery as classification_recovery
        except ImportError:
            import autocode_classification_recovery as classification_recovery
        classification_recovery.retain(state, request)
    return request["status"], reason


def with_guidance(state: RunState, stage: str, request):
    """Reuse applicable corrections; expired advice never renews its spent identity."""
    if stage == STAGE:
        return request
    block = guidance(state, stage)
    if not block:
        return request
    head, marker, tail = request.prompt.partition("CURRENT HANDOFF DATA\n")
    return replace(request, prompt=head + block + marker + tail if marker else request.prompt + block)


def guidance(state: RunState, stage: str) -> str:
    """The corrections and Investigator guidance that apply to ``stage``'s next request, or ``""``.

    A stage request and a report-only repair of that stage read the same text."""
    current = state.get("stuck_investigation") or {}
    if stage == STAGE:
        return ""
    active = (
        current.get("in_force")
        and recovery_binding.applies(state, current, current.get("stage", ""))
        and (stage == current["stage"] or (current["stage"] in PLANNING and stage in PLANNING))
    )
    lessons = planning_lessons(state) if stage in PLANNING else []
    if active:
        lessons = [row for row in lessons if row.get("identity") != current.get("identity")]
    block = ""
    if lessons:
        block = (
            "\nEARLIER PLANNING CORRECTIONS\n"
            "These are earlier report-format diagnoses, not verified successful retries. Keep only "
            "corrections applicable to the current task, contract and saved answers. "
            "They grant no retries, budget, permissions or approval and settle no user decision.\n"
            + "\n".join(f"Diagnosis: {row['diagnosis']}\nCorrection: {row['guidance']}" for row in lessons)
            + "\n"
        )
    if active:
        block += (
            "\nINVESTIGATOR GUIDANCE (takes precedence over earlier guidance on conflict). "
            f"Diagnosis: {current['diagnosis']}\nFollow this guidance on this attempt: {current['guidance']}\n"
        )
    return block


def planning_lessons(state: RunState) -> list[dict]:
    """Reuse report-format advice only against the planning inputs it diagnosed.

    A clarification, feedback or follow-up does not renew an investigation identity.
    Never resurrect its retry grant or carry convergence/product advice as a lesson.
    Legacy unbound rows stay in history without being presented as current advice.
    """
    lessons = [
        row
        for row in state.get("stuck_investigations") or []
        if row.get("outcome") == "retried"
        and row.get("cause") == "stage_output"
        and row.get("stage") in PLANNING
        and row.get("diagnosis")
        and row.get("guidance")
        and recovery_binding.applies(state, row, row["stage"])
        and (
            row.get("trigger") == "rejected_output"
            or ("trigger" not in row and row.get("status") == "PAUSED_INVALID_OUTPUT")
        )
    ]
    limit = max_calls(state)
    return lessons[-limit:] if limit else []


def settle(state: RunState, stage: str) -> None:
    """A stage completed: non-planning guidance is spent once its stage succeeds."""
    current = state.get("stuck_investigation") or {}
    if current.get("in_force") and stage == current["stage"] and stage not in PLANNING:
        state.pop("stuck_investigation")


def retire(state: RunState, status: str | None = None) -> None:
    """A checkpoint or usage hold preserves applicable advice, never a new retry grant."""
    current = state.get("stuck_investigation") or {}
    if (
        status
        in {
            "PAUSED_INTERVENTION",
            "PAUSED_REQUESTED",
            "PAUSED_MILESTONE_BUDGET",
            "PAUSED_PLANNING_BUDGET",
            "PAUSED_TIME_LIMIT",
            "PAUSED_COST_LIMIT",
            "PAUSED_ITERATION_LIMIT",
            planning_calls.STATUS,
        }
        and current.get("in_force")
        and recovery_binding.applies(state, current, current.get("stage", ""))
    ):
        return
    if current.get("in_force"):
        state.pop("stuck_investigation")


def drive(
    state: RunState,
    dispatch,
    *,
    apply=None,
    before=None,
    after=None,
    persist=None,
    active,
    skip,
    paused,
    investigate=False,
    queue=True,
):
    """The runner's stage loop (autopilot.drive), with investigation before a non-convergence pause.

    ``paused`` is the runner's pause exception type (with ``status``); ``skip`` its restart sentinel.
    Without ``investigate`` this is exactly the plain loop.

    ``queue=False`` is for a run whose authority cannot launch the Investigator (native visual
    runs): a stage pause is never sent to it, so the pause keeps its own reason and spends no
    investigation. A request queued elsewhere (Builder failure classification) is still restored
    to its own pause when that authority refuses it.

    Only a pause this invocation produced by attempting a stage (a new stage record) is
    investigated: a guard re-asserting a known pause on resume launches nothing, since that
    pause was investigated when it happened or is now the user's decision.
    """
    # Runner-owned records (orchestration, regression proof, AutoResolver receipts) are not attempts.
    attempts = lambda: sum(1 for row in state.get("stages") or [] if not row.get("runner_owned"))
    attempted = attempts()
    fresh = lambda: attempts() > attempted
    owed: set[str] = set()  # PUBLISHED pauses this invocation sent to the Investigator
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
                # A requested checkpoint preserves the queued investigation,
                # including a report repair; it is not an Investigator failure.
                if status == "PAUSED_REQUESTED" or (
                    status in BOUNDARY_LIMITS
                    and (not launched(state, current) or classification_recovery.pending_repair(state, current))
                ):
                    release_route(state)
                    raise
                status, reason = abandon(state, reason)
                if persist:
                    persist(state)
                reraised = paused(status, reason)
                if getattr(error, "quota_worker", None):
                    reraised.quota_worker = error.quota_worker
                raise reraised from error
            retire(state, status)
            if not fresh():
                raise
            if not queue or not intercept(state, status, reason):
                if queue and retry_rejected(state, status):
                    if persist:
                        persist(state)
                    continue
                reraised = paused(status, annotate(state, status, stopped(state, status, reason)))
                if getattr(error, "quota_worker", None):
                    reraised.quota_worker = error.quota_worker
                raise reraised from error
            if status in PUBLISHED:
                owed.add(status)
            if persist:
                persist(state)
            continue
        if not investigate:
            return state
        if timed_out(state):
            # The stop's own cause, without the job runner's offer of an exact retry.
            context = state.get("recovery_context") or {}
            cause = context.pop("reason", "") if context.get("stage") == STAGE else ""
            if context.get("stage") == STAGE:
                state.pop("recovery_context")
            status, reason = abandon(state, cause or state["job_failure"]["reason"])
            state.pop("job_failure", None)
            state.pop("job_retry_authorization", None)
            if persist:
                persist(state)
            raise paused(status, reason)
        retire(state, state.get("status"))
        if queue and fresh() and intercept(state, cast(str, state.get("status")), state.get("stop_reason") or ""):
            if persist:
                persist(state)
            continue
        if state.get("status") in STATUSES and state.get("stop_reason"):
            state["stop_reason"] = annotate(state, state["status"], state["stop_reason"])
        if state.get("status") in owed and state.get("stop_reason"):
            # The Investigator did not retry: publish the restored pause as the runner always has.
            raise paused(state["status"], state["stop_reason"])
        return state


def owns(state: RunState) -> bool:
    """An investigation never completes a run: it retries the stuck stage or restores its pause."""
    return False


def render(state: RunState) -> str:
    return ""
