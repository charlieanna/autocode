"""Saved recovery facts and exact-pause identity; no execution or file access.

The public status view and dashboard use this projection. Actions describe the
existing CLI controls, not permission to execute them. The runner rechecks its
normal approval, liveness, scope and allowance gates under its lock.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import hashlib
import json
from pathlib import PurePath

try:
    from . import autocode_accepted_source as accepted_source
    from . import autocode_builder_policy as builder_policy
    from . import autocode_member_stop as member_stop
    from . import autocode_provider_auth as provider_auth
    from . import autocode_quota_route as quota_route
    from .autocode_role_names import role_name
except ImportError:
    import autocode_accepted_source as accepted_source
    import autocode_builder_policy as builder_policy
    import autocode_member_stop as member_stop
    import autocode_provider_auth as provider_auth
    import autocode_quota_route as quota_route
    from autocode_role_names import role_name

ACTIVE = {"RUNNING", "DISCOVERING", "EXECUTING", "TASK_COMPLETE", "COMPLETE"}
# These inputs bind the displayed stopped frontier. Polling timestamps and
# separately written status summaries cannot expire an otherwise current card.
BOUND_FIELDS = (
    "workspace",
    "run_dir",
    "task_id",
    "created_at",
    "task",
    "status",
    "next_stage",
    "iteration",
    "active_seconds",
    "current_task",
    "goal_contract",
    "settings",
    "planning",
    "automatic_recoveries_since_resume",
    "consecutive_timeout_recoveries",
    "automatic_timeout_recoveries",
    "no_progress_batches",
    "recovery_grants",
    "active_stage",
    "active_runner_check",
    "stages",
    "pending_report_repair",
    "job_failure",
    "failure_history",
    "builder_retries",
    "builder_retry_decisions",
    "orchestration_batch",
    "resolution_request",
    "recovery_context",
    "pending_questions",
    "user_request",
    "resolver_human_request",
    "resolver",
    "user_events",
    "applied_interventions",
    "progressive",
    "stop_reason",
)


def token(state: RunState):
    if state.get("status") in ACTIVE or not state.get("status"):
        return None
    value = {key: state.get(key) for key in BOUND_FIELDS}
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return "recovery-v1:" + hashlib.sha256(encoded).hexdigest()


def require_token(state: RunState, expected):
    if expected is not None and (not isinstance(expected, str) or not expected or expected != token(state)):
        raise ValueError("The saved pause changed. Refresh and inspect the current recovery card before retrying.")


def _dict(value):
    return value if isinstance(value, dict) else {}


def _rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _cause(state: RunState):
    published = _dict(state.get("resolver_human_request"))
    entry = _dict(_dict(_dict(state.get("resolver")).get("human_escalations")).get(published.get("request_id")))
    origin = _dict(_dict(_dict(entry.get("identity")).get("proposal")).get("origin"))
    return (
        origin.get("pause_status")
        if entry.get("status") == "pending" and published.get("scope") == "operational_exhaustion"
        else None
    )


def _explanation(status, role):
    exact = {
        "WAITING_FOR_DEPENDENCY": "This task is waiting for the required accepted delivery from another task.",
        "AWAITING_GOAL_APPROVAL": "The plan is at a saved review checkpoint before implementation can begin.",
        "WAITING_FOR_USER": "The task is waiting at a saved review or response checkpoint.",
        "RESOLVER_PENDING": "Resolver has not yet published a decision request for you.",
        "PAUSED_REQUESTED": "The task finished and saved its current step, then paused as requested.",
        "PAUSED_INTERVENTION": "The task paused at a saved boundary for your review.",
        "PAUSED_BUILDER_RETRY_LIMIT": "The Builder used its configured attempts for this task and stopped.",
        "PAUSED_REPEATED_FAILURE": "The same step repeatedly failed. An ordinary resume cannot grant another attempt.",
        "PAUSED_JOB_FAILURE": "The step failed. Its saved attempt and any recovery diagnosis need inspection.",
        "PAUSED_STAGE_ABANDONED": "The interrupted attempt has been reconciled. Continuing is a separate action.",
        "PAUSED_PROVIDER_UNCERTAIN": "The provider ended without a confirmed result. Its partial work needs inspection.",
        "PAUSED_OUTPUT_CAP": "The response exhausted its output limit without a complete report. Name another model before retrying.",
        "PAUSED_CONTEXT_WINDOW": "The request no longer fit the model's context window, or the provider compacted the conversation, so the response is not accepted. Name a model with a larger window, or give the job a smaller task.",
        "PAUSED_UNCERTAIN_STAGE": "The step ended without a confirmed result. Its partial work needs inspection.",
        "PAUSED_PLANNING_BUDGET": "Planning used its configured review allowance.",
        "PAUSED_COMPONENT_PLAN": "The saved component plan conflicts with its caller-owned scope or executable child flow. "
        "An unchanged resume cannot repair the contract.",
        "PAUSED_RATE_LIMIT": "The provider temporarily refused more requests.",
        "PAUSED_PROVIDER_CAPACITY": "The provider could not accept the request with its current capacity.",
        "PAUSED_CONTENT_FILTER": "The provider's content filter refused the response. The same model is likely to refuse it again; another model is needed.",
    }
    if status in exact:
        return exact[status]
    groups = [
        (("TIMEOUT", "TIME_LIMIT", "NO_PROGRESS"), "The step reached a configured time or progress limit."),
        (
            ("BUDGET", "ITERATION_LIMIT", "USAGE_UNKNOWN"),
            "A configured usage limit or unavailable usage evidence stopped the task.",
        ),
        (
            ("INVALID_OUTPUT", "REPORT_REPAIR", "METADATA"),
            "The saved report could not be accepted in its current form.",
        ),
        (
            ("STALE_", "INVALID_PREDECESSOR", "EVIDENCE", "COMPLETION", "TRANSPORT_"),
            "The saved evidence or input is not currently sufficient to continue.",
        ),
        (
            ("APPROVAL", "GOAL_", "CRITERIA_CHANGE", "AUTHORITY"),
            "The current work needs the correct scope or approval before it can continue.",
        ),
        (
            ("WORKSPACE_BUSY", "RUN_BUSY", "PROCESS_", "ORCHESTRATOR_WORKERS"),
            "Worker ownership or process cleanup needs to be resolved before another step can run.",
        ),
        (
            ("CONFLICT", "DIRTY", "OWNERSHIP", "ASSIGNMENT_SCOPE", "ORCHESTRATOR_"),
            "The task stopped to protect work whose integration or ownership is unresolved.",
        ),
        (
            ("DESIGN_", "PREFLIGHT", "BLOCKED_ENV", "BILLING_ROUTE", "CROSS_MODEL", "REVIEWER_FALLBACK"),
            "A required environment, design input or saved model route needs correction.",
        ),
        (("RESOLVER",), "Resolver paused while working out a safe next step."),
    ]
    for fragments, text in groups:
        if any(part in status for part in fragments):
            return text
    return f"{role or 'The task'} stopped before starting another step. Inspect the saved reason before continuing."


# The stop of a job_failure saved before #463, which has its kind but no pause_status.
_JOB_STOP_CAUSE = {
    "content_filter": "PAUSED_CONTENT_FILTER",
    "quota": "PAUSED_BUDGET",
    "output_limit": "PAUSED_OUTPUT_CAP",
    "context_window": "PAUSED_CONTEXT_WINDOW",
}


AUTH_STOP = (
    "The model provider rejected the saved credentials (HTTP 401): the key or sign-in has expired or "
    "been revoked. Sign in to the backend again (in the terminal chat: /connect NAME login), then reply here."
)


def _auth_stop(state: RunState) -> str | None:
    """A stop on the core's own authentication diagnostic, in plain words; never the provider's message."""
    reason = str(state.get("stop_reason") or "")
    return AUTH_STOP if reason.startswith(provider_auth.AUTH_FAILED) else None


def _job_model_stop(state: RunState, role):
    """What happened to a workflow job its provider refused or ran out of quota on (#463), or None.

    Once a person named another model (``route_assignment``), the next step is only the retry
    with the token issued for it; a job without a route keeps only its exact retry.
    """
    if not quota_route.job_pause_current(state):
        return None
    failure = _dict(state.get("job_failure"))
    route = _dict(quota_route.job_question(state))
    cause = quota_route.job_stop_status(failure) or _JOB_STOP_CAUSE.get(failure.get("kind"))
    job = route.get("job") or role or "job"
    on = f" on {route['stopped_model']}" if route.get("stopped_model") else ""
    assigned = _dict(failure.get("route_assignment")).get("to") if route else None
    now = f"The {job} now runs on {assigned}; retry it once with the new token." if assigned else ""
    if cause == "PAUSED_CONTENT_FILTER":
        refused = f"The provider's content filter refused the {job}'s response{on}. The same model is likely to refuse it again"
        if now:
            return f"{refused}. {now}"
        return refused + (
            "; name another model for this job, then retry it with the new token."
            if route
            else "; this stop keeps only its exact retry."
        )
    if cause == "PAUSED_OUTPUT_CAP":
        stopped = f"The {job} exhausted its response output limit{on}; the attempt is incomplete."
        if now:
            return f"{stopped} {now}"
        return stopped + (
            " Name another model for this job, then retry it with the new token."
            if route
            else " This stop keeps only its exact retry."
        )
    if cause == "PAUSED_CONTEXT_WINDOW":
        stopped = (
            f"The {job}'s request no longer fit the model's context window{on}, or the provider compacted the "
            "conversation; the response is not accepted."
        )
        if now:
            return f"{stopped} {now}"
        return stopped + (
            " Name a model with a larger context window for this job, then retry it with the new token."
            if route
            else " This stop keeps only its exact retry."
        )
    if cause == quota_route.AUTH_STATUS:
        stopped = f"The {job}'s provider authentication failed{on}."
        if now:
            return f"{stopped} {now}"
        return stopped + (
            " Repair the provider login and retry unchanged, or name another model for this exact job."
            if route
            else " Repair the provider login before retrying this exact job."
        )
    if cause == "PAUSED_BUDGET":
        spent = f"The {job}'s provider reported its quota, usage limit or credits used up{on}."
        if now:
            return f"{spent} {now}"
        return spent + (
            " Name another model for this job, or retry it unchanged once the quota resets."
            if route
            else " Retry it once the quota resets."
        )
    return None


def _awaits_model(state: RunState, need):
    """A workflow job its provider's content filter refused, with no other model named yet (#463).

    Its exact retry would replay the refused model, so the card offers it only once a person named
    another model (``route_assignment``, retried with the new token). A quota stop keeps it (the
    quota resets), and so does a refusal with no model question (the exact retry is its only way on).
    """
    return _dict(need.get("route")).get("cause") in ("content_filter", "output_limit") and not isinstance(
        _dict(state.get("job_failure")).get("route_assignment"), dict
    )


def _args(kind, fields):
    """The exact CLI flags an execution action runs, after the caller's --expected-recovery-token; else None."""
    if kind == "abandon":
        return ["--abandon-stage", fields["attempt_id"]]
    if kind == "resume":
        return ["--resume-paused"]
    if kind == "retry_failed_stage":
        return ["--resume-paused", "--retry-failed-stage"]
    if kind == "accept_source_edit":
        return accepted_source.COMMAND.split()
    if kind == "retry_job":
        return ["--resume-paused", "--retry-failed-stage", "--job-retry-token", fields["job_retry_token"]]
    if kind == "retry_report":
        return ["--resume-paused", "--retry-report", fields["attempt_id"]]
    if kind == "retry_builder":
        return [
            "--resume-paused",
            *(word for member in fields["milestone_ids"] for word in ("--retry-builder", member)),
        ]
    return None


def _action(kind, label, effect, **fields):
    row = {
        "id": kind + (":" + ",".join(fields["milestone_ids"]) if fields.get("milestone_ids") else ""),
        "kind": kind,
        "label": label,
        "effect": effect,
        **fields,
    }
    args = _args(kind, fields)
    if args is not None:
        # Clients run exactly these flags instead of mapping the action's kind (#730).
        row["args"] = args
    return row


def native_cleanup_need(state: RunState):
    """Pure request eligibility; the original native owner still authenticates.

    Generic uncertain-stage advice cannot grant a native control. Use the same
    original-input parser and request predicate as the actual native caller,
    without reading its private journal or reconstructing source authority.
    """
    if state.get("status") != "PAUSED_PROCESS_CLEANUP":
        return None
    try:
        from . import autocode_native_stage_recovery as native_recovery
        from . import autocode_roles as roles
        from . import autocode_util as util
        from . import autocode_visual_profile as profile
        from . import autocode_workflows as workflows
    except ImportError:
        import autocode_native_stage_recovery as native_recovery
        import autocode_roles as roles
        import autocode_util as util
        import autocode_visual_profile as profile
        import autocode_workflows as workflows
    valid_intent = True
    try:
        native = profile.initial_intent(state.get("task") or "") is not None
    except ValueError:
        native = True  # A malformed native directive cannot grant ordinary recovery.
        valid_intent = False
    if not native:
        return None
    active = _dict(state.get("active_stage"))
    attempt = (
        f"{active['iteration']:03d}/{PurePath(active['output']).stem}"
        if type(active.get("iteration")) is int and isinstance(active.get("output"), str) and active["output"]
        else None
    )
    if attempt is not None and valid_intent:
        try:
            native_recovery.request(state, attempt, {*roles.PLANNING_V2_STAGES, workflows.STAGE})
        except util.Paused:
            pass
        else:
            # Display origin only; the original owner still authenticates the request.
            return {
                "native_cleanup": True,
                "kind": "resume",
                "reason": state.get("stop_reason"),
                "abandon_stage": attempt,
                "action": f"--abandon-stage {attempt}",
                "recovery_hint": "Request reconciliation of this exact native planning cancellation. The original owner "
                "must still authenticate source, supervision and stopped identities. Resume is a separate action after acceptance.",
            }
    return {
        "native_cleanup": True,
        "kind": "recover_source",
        "reason": state.get("stop_reason"),
        "action": None,
        "recovery_hint": "Native planning cleanup is held: this saved stop has no supported abandonment request. "
        "Inspect its original source, supervision and retained attempt; ordinary resume is unavailable.",
    }


def _builder_members(state: RunState, cause):
    if cause != "PAUSED_BUILDER_RETRY_LIMIT" or state.get("next_stage") != "terra":
        return []
    if any(state.get(key) for key in ("active_stage", "active_runner_check", "pending_report_repair", "parent_run")):
        return []
    task = _dict(state.get("current_task"))
    members = task.get("milestone_ids") or [task.get("milestone_id") or task.get("id")]
    if not members or any(not isinstance(mid, str) or not mid for mid in members):
        return []
    lane = _dict(_dict(state.get("builder_retries")).get(builder_policy.key(state)))
    if not builder_policy.enabled(state) or lane.get("action") != "pause" or not lane.get("failures"):
        return []
    return list(members)


def _parallel_members(state: RunState):
    batch = _dict(state.get("orchestration_batch"))
    if (
        state.get("next_stage") != "orchestrator"
        or batch.get("status") != "BUILDING"
        or batch.get("contract_hash") != _dict(state.get("goal_contract")).get("hash")
        or state.get("active_stage")
        or state.get("active_runner_check")
    ):
        return []
    workers = _rows(batch.get("workers"))
    if any(row.get("status") in ("RUNNING", "PENDING") for row in workers):
        return []
    # A member stopped on its model (quota or its provider's content filter) is asked route-terra, never
    # offered a retry on the same model while that question is open (#465). Once a person answered it
    # without a model (information, or leaving it paused), its own control is the one way on (#541,
    # autocode_member_stop): a quota-stopped member reruns unchanged, a refused one is asked again.
    answered = cast(dict, (member_stop.answered(state) or ({},))[0]).get("milestone_id")
    return [
        row["milestone_id"]
        for row in workers
        if isinstance(row.get("milestone_id"), str)
        and row.get("milestone_id")
        and (row["milestone_id"] == answered or row.get("status") not in quota_route.STATUSES)
        and (
            str(row.get("status", "")).startswith(("PAUSED_", "FAILED", "BLOCKED"))
            or row.get("status") == "INTERRUPTED"
        )
    ]


def _failures(state: RunState):
    mode = _dict(_dict(state.get("settings")).get("workflow")).get("mode")
    groups = []
    for key, entry in _dict(state.get("failure_history")).items():
        entry = _dict(entry)
        identity = _dict(entry.get("identity"))
        if not entry.get("count"):
            continue
        groups.append(
            {
                "id": key,
                "role": role_name(identity.get("stage"), mode),
                "count": entry["count"],
                "source_revision": identity.get("artifact_hash"),
                "last_reason": entry.get("last_error"),
                "last_seen": entry.get("last_seen"),
                "attempts": list(entry.get("attempts") or []),
            }
        )
    groups.sort(key=lambda row: str(row.get("last_seen") or ""), reverse=True)
    return groups


def project(state: RunState, need=None):
    status = state.get("status") or ""
    if status in ACTIVE or not status:
        return None
    need = _dict(need)
    native_need = native_cleanup_need(state)
    if native_need is not None and need.get("kind") not in ("answer", "review", "approve_plan"):
        need = native_need
    stage = _dict(state.get("active_stage")).get("stage") or state.get("next_stage")
    role = role_name(stage, _dict(_dict(state.get("settings")).get("workflow")).get("mode"))
    cause = _cause(state) or status
    terminal = any(row.get("kind") == "stop" for row in _rows(state.get("applied_interventions")))
    decision = need.get("kind") in ("answer", "review", "approve_plan")
    finished = sum(bool(row.get("finished_at")) for row in _rows(state.get("stages")))
    result = {
        "version": 1,
        "token": token(state),
        "status": status,
        "cause": cause,
        "role": role,
        "title": "Stopped at your request" if terminal else "Review checkpoint" if decision else "Task paused",
        "category": "stopped" if terminal else "request" if decision else "recovery",
        "what_happened": "The current step finished and saved. This conversation will launch no more stages."
        if terminal
        else (_auth_stop(state) or _job_model_stop(state, role) or _explanation(cause, role)),
        "retained": f"{finished} finished attempt(s) remain in the history. Saved plan, messages and recorded evidence remain available; partial work still needs verification.",
        "saved_reason": state.get("stop_reason"),
        "context": {
            "role": role,
            "task": _dict(state.get("current_task")),
            "attempt": {
                key: _dict(state.get("active_stage")).get(key)
                for key in ("iteration", "started_at", "finished_at", "exit_code", "events", "output")
            },
        },
        "failure_groups": _failures(state),
        "actions": [],
    }
    actions = result["actions"]
    actions.append(
        _action(
            "inspect",
            "Inspect saved work",
            "Read the saved plan, changes, checks and attempt history. This does not run a step.",
        )
    )
    if terminal:
        actions.append(
            _action(
                "new_conversation",
                "Start a new conversation",
                "Keep this stopped conversation and start separate work in the same project.",
            )
        )
        return result
    if status == "PAUSED_COMPONENT_PLAN":
        if need.get("new_run_required"):
            result["title"] = "Caller binding needs a fresh run"
            result["what_happened"] = "The saved component caller binding is invalid and immutable in this run."
            actions.append(_action("new_run", "Start a fresh component run", need["recovery_hint"]))
            return result
        actions.append(
            _action(
                "feedback",
                "Correct the component plan",
                "Use --edit-goal FILE or --feedback TEXT to request a corrected draft. It needs fresh approval; "
                "this action neither edits nor approves the saved contract.",
            )
        )
        return result
    if need.get("kind") == "recover_source":
        result["title"] = "Original source needs inspection"
        result["what_happened"] = (
            need["recovery_hint"]
            if native_need is not None
            else "This saved attempt has no verifiable original source identity, so an exact retry is unavailable."
        )
        result["saved_reason"] = need.get("recovery_hint") or need.get("reason")
        actions.append(
            _action(
                "feedback",
                "Explain what should change",
                "Draft corrective information in chat after inspecting the archived attempt and current source. This does not authorize a retry.",
            )
        )
        return result
    active = _dict(state.get("active_stage"))
    attempt = (
        f"{active['iteration']:03d}/{PurePath(active['output']).stem}"
        if isinstance(active.get("iteration"), int) and active.get("output")
        else None
    )
    uncertain = status in ("PAUSED_PROVIDER_UNCERTAIN", "PAUSED_UNCERTAIN_STAGE")
    # The public status also prescribes abandonment after quota/limit stops,
    # as a resume or, once the core has classified the attempt, as a continue.
    # Bind that advice to this exact saved attempt; pending decisions and the
    # more specific report-repair path must keep their existing precedence.
    prescribed = (
        need.get("kind") in ("resume", "continue")
        and attempt is not None
        and need.get("abandon_stage") == attempt
        and not need.get("retry_report_attempt")
    )
    if attempt is not None and (uncertain or prescribed):
        actions.append(
            _action(
                "abandon",
                "Recover saved work",
                need["recovery_hint"]
                if native_need is not None
                else "Reconcile this exact interrupted attempt and keep its partial work. Resume is a separate action.",
                attempt_id=attempt,
            )
        )
    elif need.get("kind") == "retry_job" and need.get("job_retry_token"):
        if not _awaits_model(state, need):
            actions.append(
                _action(
                    "retry_job",
                    "Retry the inspected step",
                    "Allow one fresh attempt at this recorded failure. Existing scope, model settings and verification gates still apply.",
                    job_retry_token=need["job_retry_token"],
                )
            )
    elif members := _builder_members(state, cause):
        actions.append(
            _action(
                "retry_builder",
                "Retry the stopped Builder",
                "Grant one attempt for the listed task after inspection, preserving failure history and model pins. This does not approve a different plan.",
                milestone_ids=members,
            )
        )
    elif need.get("action") == "--resume-paused" and state.get("pending_report_repair"):
        actions.append(
            _action(
                "resume",
                "Validate the edited source",
                "Archive the stale report repair and collect fresh evidence. Existing limits and approvals still apply.",
            )
        )
    elif need.get("action") == accepted_source.COMMAND:
        actions.append(
            _action(
                "accept_source_edit",
                "Accept the edited source",
                "Hand the paused repair the source as it is now. The read-only Resolver diagnoses that source next; the approved plan, limits and verification gates still apply.",
            )
        )
    elif need.get("retry_report_attempt"):
        actions.append(
            _action(
                "retry_report",
                "Re-run validation",
                "Collect fresh validation evidence after this exact rejected attempt. Existing limits and approvals still apply.",
                attempt_id=need["retry_report_attempt"],
            )
        )
    elif need.get("kind") == "resume" and need.get("retry_action") == " ".join(_args("retry_failed_stage", {}) or ()):
        # A novelty hold whose request is not yet published re-holds on a plain resume (#1770).
        actions.append(
            _action(
                "retry_failed_stage",
                "Retry the held step once",
                "Authorize exactly one more attempt of the held step; a plain resume would hold again. Saved attempts and evidence are kept; existing limits, approvals and verification gates still apply.",
            )
        )
    elif cause == "PAUSED_REPEATED_FAILURE" and not any(
        state.get(key)
        for key in ("active_stage", "active_runner_check", "uncertain_artifacts", "pending_report_repair")
    ):
        actions.append(
            _action(
                "retry_failed_stage",
                "Retry the failed step once",
                "Grant one fresh attempt for the recorded repeated failure, subject to the existing limits and verification gates.",
            )
        )
    elif need.get("kind") in ("answer", "review", "approve_plan"):
        actions.append(
            _action(
                "decision",
                "Inspect the current request",
                "Only a currently authorized question, plan or evidence card can accept a response in chat. This action does not answer or approve it.",
            )
        )
        # A recovery novelty hold also admits one explicit retry; needs names it only when it applies (#1012).
        if need.get("retry_action") == " ".join(_args("retry_failed_stage", {}) or ()):
            actions.append(
                _action(
                    "retry_failed_stage",
                    "Retry the held step once",
                    "Authorize one more attempt of the held step without new information. Saved attempts and evidence are kept; existing limits, approvals and verification gates still apply.",
                )
            )
    elif (
        cause
        in (
            "PAUSED_REQUESTED",
            "PAUSED_INTERVENTION",
            "PAUSED_INTERRUPTED",
            "PAUSED_RATE_LIMIT",
            "PAUSED_PROVIDER_CAPACITY",
            "PAUSED_STAGE_ABANDONED",
            "PAUSED_INVALID_OUTPUT",
        )
        and not active
        and not state.get("active_runner_check")
    ):
        requested = cause in ("PAUSED_REQUESTED", "PAUSED_INTERVENTION")
        actions.append(
            _action(
                "resume",
                "Resume" if requested else "Resume after correction",
                "Continue from where the run paused. Existing model pins, limits and approval gates still apply."
                if requested
                else "Continue from this saved pause after its cause is corrected. Existing model pins, limits and approval gates still apply.",
            )
        )
    for member in _parallel_members(state):
        label, effect = member_stop.card(state, member) or (
            "Retry Builder task " + member,
            "Retry only this stopped batch member. Completed members and their work are kept. The runner rechecks worker liveness and the approved batch.",
        )
        actions.append(_action("retry_builder", label, effect, milestone_ids=[member]))
    actions.append(
        _action(
            "feedback",
            "Explain what should change",
            "Draft corrective information in chat. Sending it follows the existing confirmation or current-request path.",
        )
    )
    return result
