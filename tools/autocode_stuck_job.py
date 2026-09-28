"""When a stage stops making progress, investigate before pausing for the user.

Today a stage that does not converge (its report is rejected again and again, the
Planner and Plan Reviewer cannot agree within the review budget, the Builder makes no
progress, the Completion Owner will not decide) repairs, loops, then pauses and waits
for a person. Most of those pauses have a cause a stronger model can find by reading
the saved exchange: a rule the stage misunderstands, two roles talking past each other,
a missing fact the runner already has.

So before the runner pauses with one of ``STATUSES``, ``drive`` sends the run to
``STAGE``: a read-only Investigator on a strong OpenAI model (GPT-6 Astra, or GPT-6 Sol
when the stuck stage itself runs on Astra) reads the task, the run's saved state and the
stuck stage's attempts, and returns a diagnosis plus either guidance for one more
attempt or the question only the user can answer. Then:

- ``retry`` (only for ``RETRYABLE`` statuses): the runner resets exactly one attempt's
  worth of the limit that stopped the stage and runs it again with the guidance in its
  prompt (``with_guidance``). Planning guidance stays in force for every planning stage
  until the plan is presented; any other stage's guidance until that stage completes.
- ``pause``, a ``DIAGNOSE_ONLY`` status, or a failed investigation: the original pause
  is restored exactly, with the diagnosis added to its reason.

Bounds: one investigation per distinct (stage, pause) problem and ``MAX_CALLS`` per run
(``settings.stuck_investigation.max_calls_per_run``; 0 disables). The Investigator
cannot approve work, change requirements or criteria, weaken tests, grant permissions,
spend beyond the one attempt, or write anything (it runs read-only). Decisions that belong to the
user (permissions, scope, goal and criteria changes, spend limits) never come here.

Pure module: prompt, schema, transitions and the drive loop. Imports nothing from the
runner; the runner passes its pause exception type in. State keys written:
``stuck_investigation`` (the one in progress or in force) and ``stuck_investigations``
(history).
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import replace

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
    "required": ["diagnosis", "cause", "guidance", "recommendation", "user_question", "evidence_refs"],
    "properties": {
        "diagnosis": TEXT,
        "cause": {"type": "string", "enum": ["stage_output", "role_disagreement", "missing_information",
                                             "environment", "needs_user", "other"]},
        "guidance": TEXT,
        "recommendation": {"type": "string", "enum": ["retry", "pause"]},
        "user_question": TEXT,
        "evidence_refs": TEXTS,
    },
}

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
- evidence_refs: files or saved outputs you relied on.

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
    history.append({"identity": key, "stage": stuck, "status": status, "reason": reason,
                    "requested_at": request["requested_at"], "outcome": "investigating"})
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


def route(roles: dict, stuck_model: str) -> dict:
    """The Investigator's route: the Plan Reviewer's engine and provider on a strong OpenAI model
    different from the stuck stage's, at xhigh effort. A non-OpenAI setup keeps its reviewer model."""
    base = dict(roles.get("plan_reviewer") or roles.get("astra") or {})
    model = str(base.get("model") or "")
    if model.startswith("openai/") or model.startswith("gpt-"):
        family = "gpt-6-sol" if "gpt-6-astra" in (stuck_model or "") else "gpt-6-astra"
        base["model"] = f"openai/{family}" if model.startswith("openai/") else family
    base["reasoning_effort"] = "xhigh"
    return base


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
            "saved_answers": state.get("answers", {})}


def prompt(state: dict, state_path, inventory: dict | None = None, soft_budget_tokens: int = 10000,
           engine: str | None = None) -> tuple[str, dict]:
    text = PROMPT + "\nCURRENT HANDOFF DATA\n" + json.dumps(packet(state, state_path, inventory, engine), indent=2)
    return text, {"estimated_prompt_tokens": (len(text.encode()) + 3) // 4, "soft_budget_tokens": soft_budget_tokens}


def check(value: dict, changed_files) -> None:
    stray = sorted(str(path) for path in (changed_files or []))
    if stray:
        raise ValueError("An investigation must not change the repository; this attempt changed: " + ", ".join(stray))
    if not value["diagnosis"].strip():
        raise ValueError("An investigation must say what it found")
    if value["recommendation"] == "retry" and not value["guidance"].strip():
        raise ValueError("A retry needs concrete guidance for the next attempt")
    if value["recommendation"] == "pause" and value["cause"] == "needs_user" and not value["user_question"].strip():
        raise ValueError("A pause for the user must say what the user has to decide")


def apply(state: dict, value: dict, record: dict, workspace) -> None:
    check(value, record.get("changed_files"))
    request = state.pop("stuck_investigation")
    retry = value["recommendation"] == "retry" and request["status"] in RETRYABLE
    outcome = "retried" if retry else "paused"
    entry = next(row for row in reversed(state["stuck_investigations"]) if row["identity"] == request["identity"])
    entry.update(outcome=outcome, diagnosis=value["diagnosis"], cause=value["cause"], guidance=value["guidance"],
                 user_question=value["user_question"], evidence_refs=value["evidence_refs"],
                 output=record.get("output"), finished_at=now())
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
                                    "guidance": value["guidance"], "diagnosis": value["diagnosis"], "in_force": True}
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
    entry = next(row for row in reversed(state["stuck_investigations"]) if row["identity"] == request["identity"])
    entry.update(outcome="investigation_failed", error=error, finished_at=now())
    reason = f"{request['reason']}\n(The Investigator could not finish: {error})"
    restore(state, request, reason)
    return request["status"], reason


def with_guidance(state: dict, stage: str, request):
    """The stage's model request, with the Investigator's guidance when it is in force for ``stage``."""
    current = state.get("stuck_investigation") or {}
    if not current.get("in_force") or stage == STAGE:
        return request
    if not (stage == current["stage"] or (current["stage"] in PLANNING and stage in PLANNING)):
        return request
    block = ("\nINVESTIGATOR GUIDANCE (this stage stopped making progress; an independent Investigator read the "
             f"saved attempts). Diagnosis: {current['diagnosis']}\nFollow this guidance on this attempt: "
             f"{current['guidance']}\n")
    head, marker, tail = request.prompt.partition("CURRENT HANDOFF DATA\n")
    return replace(request, prompt=head + block + marker + tail if marker else request.prompt + block)


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
                raise paused(status, reason) from error
            retire(state)
            if not fresh():
                raise
            if not intercept(state, status, reason):
                raise paused(status, annotate(state, status, reason)) from error
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
