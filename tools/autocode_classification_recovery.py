"""One explicit retry of an abandoned, failure-bound Builder classification.

This module owns builder_failure_hold.classification_retry and the retry grant
inside stuck_investigation. abandon retains the request and failed history;
run_view projects the offer, run_actions authorizes, and provider admission
consumes it. Existing classification history counts every reservation. No Builder
retry, proposal assignment, approval or spending limit is granted here.
"""

import copy
from pathlib import Path

try:
    from . import autocode_builder_failure as failure
    from . import autocode_builder_policy as policy
    from . import autocode_investigation_policy as investigation_policy
    from . import autocode_util as util
except ImportError:
    import autocode_builder_failure as failure
    import autocode_builder_policy as policy
    import autocode_investigation_policy as investigation_policy
    import autocode_util as util

ACTION = "--resume-paused --retry-failed-stage --job-retry-token TOKEN"
STAGE = "investigate_stuck"


def _attempts(state, request):
    return [
        row
        for row in state.get("stages") or []
        if (row.get("original_stage") or str(row.get("stage", "")).removesuffix("_report_repair")) == STAGE
        and row.get("started_at", "") >= request["requested_at"]
    ]


def retain(state, request):
    """Retain only the request whose finalized failed history abandon just wrote."""
    history = state.get("stuck_investigations") or []
    index = next(i for i in reversed(range(len(history))) if history[i]["identity"] == request["identity"])
    if history[index].get("outcome") != "investigation_failed":
        raise ValueError("Classification recovery requires finalized failed history")
    attempts = copy.deepcopy(_attempts(state, request))
    # A prelaunch refusal produces no new attempt. Preserve earlier evidence pins
    # so abandoning that retry cannot renew authority over a changed leaf.
    previous = request.get("classification_retry") or {}
    pins = copy.deepcopy(previous.get("retained_pins") or {})
    resolver_record = (request.get("resolver_continuation") or {}).get("record") or {}
    for row in [*attempts, resolver_record]:
        for key in ("output", "events", "schema", "before_ref", "after_ref", "diff_ref"):
            path = row.get(key)
            if path and Path(path).is_file():
                pins.setdefault(path, util.file_hash(path))
    state["builder_failure_hold"]["classification_retry"] = {
        "request": copy.deepcopy(request),
        "history_index": index,
        "history": copy.deepcopy(history[index]),
        "history_prefix": copy.deepcopy(previous.get("history_prefix", history[:index])),
        "attempts": attempts,
        "pins": pins,
        "report_repair": copy.deepcopy(state.get("pending_report_repair")),
    }


def _repair_finished(state, context):
    pending = context.get("report_repair")
    if not pending:
        return True
    request = context["request"]
    original = pending.get("original") or {}
    maximum = ((state.get("settings") or {}).get("report_repair") or {}).get("max_attempts", 0)
    return (
        original.get("stage") == STAGE
        and original.get("started_at", "") >= request["requested_at"]
        and bool(pending.get("latest_rejected"))
        and type(pending.get("attempts")) is int
        and type(maximum) is int
        and maximum > 0
        and pending["attempts"] >= maximum
    )


def configuration(state):
    settings = copy.deepcopy(state.get("settings") or {})
    # prepare_stuck derives this temporary role from the bound source roles and
    # Investigator settings; abandon removes it. It is not a user route change.
    (settings.get("roles") or {}).pop("stuck_investigator", None)
    return settings


def _context(state):
    held = state.get("builder_failure_hold") or {}
    context = held.get("classification_retry") or {}
    request = context.get("request") or {}
    history = state.get("stuck_investigations") or []
    index = context.get("history_index")
    bound = (request.get("failure_evidence") or {}).get("binding") or {}
    task, contract = state.get("current_task") or {}, state.get("goal_contract") or {}
    if (
        state.get("status") != "PAUSED_BUILDER_CLASSIFICATION"
        or state.get("status") != held.get("status")
        or request.get("mode") != "builder_failure"
        or not request.get("requested_at")
        or type(index) is not int
        or not 0 <= index < len(history)
        or history[index] != context.get("history")
        or history[:index] != context.get("history_prefix")
        or history[index].get("outcome") != "investigation_failed"
        or history[index].get("identity") != request.get("identity")
        or held.get("failure_id") != (request.get("failure_evidence") or {}).get("failure_id")
        or held.get("binding") != bound
        or bound.get("task_digest") != util.digest(task)
        or bound.get("contract_hash") != contract.get("hash")
        or bound.get("contract_revision") != contract.get("revision")
        or state.get("active_stage")
        or state.get("uncertain_artifacts")
        or state.get("pending_report_repair") != context.get("report_repair")
        or state.get("pending_builder_failure")
        or state.get("stuck_investigation")
        or _attempts(state, request) != context.get("attempts")
    ):
        return None
    return context


def offer(state):
    """Read-only recovery projection; admission rechecks files and source."""
    context = _context(state)
    if not context:
        return None
    used, limit = len(failure.classified(state)), policy.classification_limit(state)
    repair_finished = _repair_finished(state, context)
    enabled = investigation_policy.enabled(state)
    if not enabled or used >= limit or not repair_finished:
        return {
            "kind": "resume",
            "reason": state.get("stop_reason"),
            "edit_required": True,
            "action": "--edit-goal FILE",
            "feedback_action": None,
            "classification_attempts": {"used": used, "limit": limit},
            "recovery_hint": (
                "The Investigator is disabled for this run. Review and change the approved plan, or start a new run with investigation enabled; retrying cannot override the saved policy."
                if not enabled
                else "The classification allowance is spent. Review and change the approved plan, or start a new run; resuming cannot refill it."
                if used >= limit
                else "The retained report is still held by its existing failure or repair limits. Inspect the artifacts and change the approved plan, or start a new run; a classification retry cannot bypass that gate."
            ),
        }
    token = "bc:" + util.digest(
        {"run_dir": state.get("run_dir"), "context": context, "configuration": configuration(state)}
    )
    return {
        "kind": "retry_job",
        "reason": state.get("stop_reason"),
        "stage": STAGE,
        "classification_id": context["request"]["failure_evidence"]["failure_id"],
        "attempt_id": (context["attempts"][-1] if context["attempts"] else {}).get("attempt_id"),
        "job_retry_token": token,
        "archive": str(Path(context["attempts"][-1]["output"]).parent)
        if context["attempts"] and context["attempts"][-1].get("output")
        else None,
        "source_identity": context["request"]["failure_evidence"]["binding"]["source_revision"],
        "retained_evidence": sorted(set(context["pins"]) | set((context.get("report_repair") or {}).get("pins") or {})),
        "resolver_proposal_pending": bool(context["request"].get("resolver_continuation")),
        "resolver_proposal_output": ((context["request"].get("resolver_continuation") or {}).get("record") or {}).get(
            "output"
        ),
        "classification_attempts": {"used": used, "limit": limit},
        "action": ACTION,
        "recovery_hint": "Inspect the retained failed classification before retrying. This grants one read-only Investigator under the remaining limits; the Resolver proposal stays unassigned until normal policy accepts a fresh diagnosis.",
    }


def gate(state, *, resume, retry, token, correction=False):
    held = state.get("builder_failure_hold") or {}
    if (
        resume
        and not (retry or token or correction)
        and state.get("status") == "PAUSED_BUILDER_CLASSIFICATION"
        and (_context(state) or "(The Investigator could not finish:" in (held.get("reason") or ""))
    ):
        return "Inspect current status for the bound classification retry or a plan correction; plain resume cannot release this hold."
    if not ((state.get("builder_failure_hold") or {}).get("classification_retry") and (retry or token)) and not (
        isinstance(token, str) and token.startswith("bc:")
    ):
        return None
    if not (resume and retry):
        return "Classification retry requires --resume-paused --retry-failed-stage and its current --job-retry-token."
    return "authorize"


def authorize(runtime, state, run_dir, workspace, token, *, supersede_job):
    need = offer(state)
    if not need or need.get("kind") != "retry_job" or not token or token != need["job_retry_token"]:
        raise ValueError(
            "Classification retry token is missing, stale, or its allowance is exhausted; inspect current status."
        )
    context = _context(state)
    if context is None:
        raise ValueError("Failed classification ownership changed")
    previous = context["request"].get("classification_retry") or {}
    failed_requested_at = previous.get("failed_requested_at") or context["request"]["requested_at"]
    failed_attempts = copy.deepcopy([*(previous.get("failed_attempts") or []), *context["attempts"]])
    if _attempts(state, {"requested_at": failed_requested_at}) != failed_attempts:
        raise ValueError("Retained failed Investigator attempts changed")
    failure.guard(state, context["request"], workspace)
    if any(not Path(path).is_file() or util.file_hash(path) != digest for path, digest in context["pins"].items()):
        raise ValueError("Retained classification artifacts changed")
    repair_pins = (context.get("report_repair") or {}).get("pins") or {}
    if any(not Path(path).is_file() or util.file_hash(path) != digest for path, digest in repair_pins.items()):
        raise ValueError("Retained report-repair inputs changed")
    for record in context["attempts"]:
        runtime.assert_stage_stopped(record)
        if record.get("exit_code") is None and not record.get("finished_at"):
            raise ValueError("An unfinished Investigator attempt must be reconciled first")
    pending = runtime.intervention_metadata(workspace, run_dir, state)
    if pending["pending_count"] or pending["inbox_error"]:
        raise ValueError("Apply queued interventions before retrying the classification")
    request = copy.deepcopy(context["request"])
    request.update(identity="builder-failure-retry:" + token[3:], requested_at=util.now())
    request["classification_retry"] = {
        "token": token,
        "run_dir": str(Path(run_dir).resolve()),
        "configuration": configuration(state),
        "consumed": False,
        "retained_pins": {**context["pins"], **repair_pins},
        "failed_requested_at": failed_requested_at,
        "failed_attempts": failed_attempts,
        "history_prefix": copy.deepcopy(state.get("stuck_investigations") or []),
        # Bind the fresh request without nesting this grant or its predecessor.
        "request_digest": util.digest({key: value for key, value in request.items() if key != "classification_retry"}),
    }
    if context.get("report_repair"):
        state.setdefault("report_repair_archive", []).append(
            {
                "reason": "Explicit retry of the failed Builder classification",
                "classification_retry_token": token,
                "repair": state.pop("pending_report_repair"),
            }
        )
    state["stuck_investigation"] = request
    state.setdefault("stuck_investigations", []).append(
        {
            "identity": request["identity"],
            "stage": request["stage"],
            "status": request["status"],
            "reason": request["reason"],
            "requested_at": request["requested_at"],
            "outcome": "investigating",
            "trigger": "builder_failure",
            "milestone_key": policy.key(state),
            "retry_of": context["history"]["identity"],
        }
    )
    state.pop("builder_failure_hold")
    supersede_job(state)  # Its archived attempt remains; an earlier jr: token cannot own this retry.
    state.setdefault("user_events", []).append(
        {"kind": "classification_retry_authorized", "at": util.now(), "token": token, "identity": request["identity"]}
    )
    state.update(status="RUNNING", phase="INVESTIGATING", next_stage=STAGE)
    state.pop("stop_reason", None)
    runtime.write_json(Path(run_dir) / "state.json", state)


def admit(state, record, workspace):
    """Consume with active_stage immediately before Popen, never on report repair."""
    request = state.get("stuck_investigation") or {}
    authorization = request.get("classification_retry")
    if record.get("stage") != STAGE or record.get("report_only") or not authorization:
        return
    # A later stopped physical job uses its existing exact jr: admission. It has
    # already been validated by job_failure.admit; this grant remains historical.
    if record.get("job_retry_token"):
        return
    failure.guard(state, request, workspace)
    history = state.get("stuck_investigations") or []
    if (
        not investigation_policy.enabled(state)
        or authorization.get("consumed")
        or authorization.get("run_dir") != str(Path(state["run_dir"]).resolve())
        or authorization.get("configuration") != configuration(state)
        or authorization.get("request_digest")
        != util.digest({key: value for key, value in request.items() if key != "classification_retry"})
        or not isinstance(authorization.get("failed_requested_at"), str)
        or not isinstance(authorization.get("retained_pins"), dict)
        or history[:-1] != authorization.get("history_prefix")
        or _attempts(state, {"requested_at": authorization["failed_requested_at"]})
        != authorization.get("failed_attempts")
        or any(
            not Path(path).is_file() or util.file_hash(path) != digest
            for path, digest in authorization["retained_pins"].items()
        )
        or not history
        or history[-1].get("identity") != request.get("identity")
        or history[-1].get("outcome") != "investigating"
        or len(failure.classified(state)) > policy.classification_limit(state)
    ):
        raise util.Paused(
            "PAUSED_BUILDER_CLASSIFICATION", "Classification retry admission changed; no provider launched"
        )
    authorization["consumed"] = True
    record["classification_retry_token"] = authorization["token"]
