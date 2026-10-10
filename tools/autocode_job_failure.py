"""Pause stopped workflow jobs under their owner, with one exact explicit retry.

Runtime services are passed by callers so this policy never imports a controller
or a cycle member. This module alone writes state.job_failure and
state.job_retry_authorization; run_view reads the former and provider admission
consumes the latter. The archive retains fragments, usage and source captures.
The failure source_paths preserve the captured inventory scope for authorize,
reroute and admission; legacy failures retain their original Git-only scope.

A job stopped on quota or by its provider's content filter (#463) also keeps, on
state.job_failure: ``pause_status`` (PAUSED_BUDGET or PAUSED_CONTENT_FILTER; read by
quota_route.stopped_attempt and autocode_job_route), ``route`` (the job's model question,
quota_route.question; read by run_view's retry_job need and autocode_job_route) and, once a
person named a model, ``route_assignment`` (that recorded change). reroute() binds the exact
retry to the named model with a new token; authorize and admit check it unchanged.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

try:
    from . import autocode_job_source as source
    from . import autocode_jobs as jobs
    from . import autocode_provider_refusal as provider_refusal
    from . import autocode_quota_route as quota_route
    from . import autocode_roles as roles
    from . import autocode_source_scope as scope
    from . import autocode_util as util
except ImportError:
    import autocode_job_source as source
    import autocode_jobs as jobs
    import autocode_provider_refusal as provider_refusal
    import autocode_quota_route as quota_route
    import autocode_roles as roles
    import autocode_source_scope as scope
    import autocode_util as util

RETRY_ACTION = "--resume-paused --retry-failed-stage --job-retry-token TOKEN"
PAUSES = ("PAUSED_JOB_FAILURE", "PAUSED_STAGE_ABANDONED")
# A stop about the model, not the work: a person may name another model for the job (#463).
_ROUTE_STOPS = {"content_filter": quota_route.REFUSAL_STATUS, "quota": quota_route.QUOTA_STATUS}


def owner(record):
    stage = record.get("stage")
    return stage if stage in jobs.STAGES and not record.get("report_only") else None


def configuration(state):
    settings = state.get("settings") or {}
    return copy.deepcopy(
        {
            k: settings.get(k)
            for k in (
                "engine",
                "provider",
                "roles",
                "limits",
                "transport_identity",
                "transport_identities",
                "headroom",
                "output_transport",
                "test_command",
                "regression_command",
            )
        }
    )


def _reason(runtime, record, error):
    role = roles.screen_name(owner(record))
    if record.get("timed_out"):
        return "timeout", role + ": " + (record.get("timeout_reason") or str(error))
    path = Path(record.get("events", ""))
    raw = path.read_text(errors="replace") if path.is_file() else ""
    status = runtime.support.failure_status(path)
    if getattr(error, "status", None) == "PAUSED_UNCERTAIN_STAGE" and status in quota_route.STATUSES:
        # The runner did not trust this response (a session it did not expect, #464): name that, not a refusal
        # or a quota stop, so no model question is asked for it (#463).
        status = "PAUSED_PROVIDER_UNCERTAIN"
    diagnostic = raw.strip()
    try:
        event = json.loads(diagnostic)
        if isinstance(event, dict) and event.get("type") in ("error", "turn.failed"):
            diagnostic = (event.get("error") or {}).get("message", diagnostic)
    except (ValueError, TypeError, AttributeError):
        pass
    if re.fullmatch(
        r"(?:(?:Error|SQLiteError|SQLITE_BUSY|SQLITE_LOCKED):\s*)?database(?: table)? is locked\.?", diagnostic, re.I
    ):
        return "startup_lock", role + ": provider database is locked"
    if "external_directory" in raw:
        return "external_directory", role + ": external-directory permission denied"
    if status == "PAUSED_RATE_LIMIT":
        return "rate_limit", role + ": provider rate limit"
    if status == "PAUSED_PROVIDER_CAPACITY":
        return "capacity", role + ": provider capacity failure"
    model = (record.get("launch_route") or {}).get("model") or "its configured model"
    if status == quota_route.REFUSAL_STATUS:
        return "content_filter", (
            provider_refusal.explain(runtime.support.events(path), job=role, model=model)
            or f"{role}: the provider's content filter refused the response on {model}; "
            "the same model is likely to refuse it again"
        )
    if status == quota_route.QUOTA_STATUS:
        message = next(
            (
                str((row.get("error") or {}).get("message") or "").strip()
                for row in runtime.support.events(path)
                if row.get("type") in ("error", "turn.failed") and isinstance(row.get("error"), dict)
            ),
            "",
        )
        return "quota", (
            f"{role}: the provider reported its quota, usage limit or credits used up on {model}"
            + (f" ({message})" if message else "")
        )
    return "exit", f"{role}: provider exited {record.get('exit_code')} without a terminal report; {error or ''}"


def _route_reason(failure):
    """The stop reason once a person named another model for the stopped job; tokens stay placeholders."""
    route, assignment = failure["route"], failure["route_assignment"]
    job = route.get("job")
    stopped = (
        f"the provider's content filter refused the response on {route.get('stopped_model')}"
        if failure.get("pause_status") == quota_route.REFUSAL_STATUS
        else f"the provider reported its quota used up on {route.get('stopped_model')}"
    )
    return (
        f"{job}: {stopped}. The {job} now runs on {assignment['to']} (was {assignment['from']}); the stopped "
        "attempt was set aside without replay. Retry it once with --resume-paused --retry-failed-stage "
        "--job-retry-token TOKEN, using the new token."
    )


def _question(runtime, state, attempt):
    """The stopped job's model question (quota_route.question) with the runtime's launch rules, when it has them.

    ``runtime`` is the runner, or anything with its ``dispatch`` and ``opencode``. Without them
    (a pure caller) the question lists no candidates.
    """
    return quota_route.question(
        state,
        attempt,
        cross_check=getattr(getattr(runtime, "dispatch", None), "enforce_cross_model_verification", None),
        configured_tool=getattr(getattr(runtime, "opencode", None), "CONFIGURED", False),
    )


def _reasked(runtime, state, failure):
    """The job's model question asked again against the configuration its retry is now bound to.

    Its candidates then never list the model the job now runs on (quota_route.validate refuses
    that one). Without the cross-model rule only that model is dropped from the saved candidates.
    """
    route, model = failure["route"], failure["route_assignment"]["to"]
    if getattr(getattr(runtime, "dispatch", None), "enforce_cross_model_verification", None) is not None:
        return _question(
            runtime,
            state,
            {
                "role": route["route_role"],
                "stage": failure["stage"],
                "model": route.get("stopped_model"),
                "pause_status": failure.get("pause_status"),
            },
        )
    route = dict(route, current_model=model)
    if "candidates" in route:
        route["candidates"] = [candidate for candidate in route["candidates"] if candidate != model]
        route.pop("recommendation", None)
    return route


def recover(runtime, state, run_dir, workspace, error=None, *, abandoned=False, launch=None):
    """Pause a stopped workflow job for one exact retry; False when the active stage is not one.

    ``launch`` carries the launch rules (``dispatch``, ``opencode``) a job stopped on its model
    lists its question's candidates with; it defaults to ``runtime``. A person setting the attempt
    aside (``abandoned``, autocode_stage_recovery.abandon_stage) passes the run records as
    ``runtime`` and the runner as ``launch``, so that stop is the one a direct stop makes.
    """
    record = state.get("active_stage") or {}
    stage = owner(record)
    if getattr(error, "status", None) in ("PAUSED_PROCESS_CLEANUP", "PAUSED_WORKSPACE_BUSY"):
        return False
    if not stage or (not abandoned and runtime.stage_completed(state, record)):
        return False
    if not record.get("pid") and not record.get("finished_at"):
        return False
    try:
        runtime.assert_stage_stopped(record)
    except util.Paused:
        return False
    kind, reason = _reason(runtime, record, error)
    original_attempt = runtime.attempt_id(record)
    if abandoned:
        note = (
            f"Operator abandoned {roles.screen_name(stage)} attempt {original_attempt}; "
            "inspect its retained work before an explicit fresh retry."
        )
        # A cause read from the attempt (a refusal or quota with its model and the provider's words,
        # a limit) stays first. The generic exit fallback has no error to name on this path.
        reason = note if kind == "exit" else reason + ("" if reason.endswith(".") else ".") + " " + note
    restoration = source.restore(workspace, record)
    if restoration["unrestored"]:
        reason += "; unrestored source: " + ", ".join(restoration["unrestored"])
    record["metrics"] = runtime.support.event_metrics(record["events"])
    runtime.account_stage(state, record)
    record.update(job_failure_kind=kind, write_diagnosis=restoration, abandoned=abandoned)
    config = record.get("job_configuration") or configuration(state)
    captured = record.get("job_source") or {}
    source_paths = captured.get("source_paths", scope.paths(state))
    source_identity = captured.get("before_identity")
    if not source_identity and not (record.get("changed_files") or []):
        # Crashed before capturing its source binding, changed nothing: anchor the
        # retry gate on the live workspace identity so an exact retry stays possible
        # (authorize still refuses if the workspace changes before the retry).
        source_identity = util.digest(source.identity(workspace, source_paths=source_paths))
    token = "jr:" + util.digest(
        {
            "run": str(run_dir),
            "attempt": original_attempt,
            "started_at": record.get("started_at"),
            "source": source_identity,
            "configuration": config,
        }
    )
    originals = runtime.archive_rejected_stage(state, run_dir, record, reason)
    state.setdefault("sessions", {}).pop(record.get("route_role") or record.get("role"), None)
    failure = state["job_failure"] = {
        "stage": stage,
        "attempt_id": original_attempt,
        "job_retry_token": token,
        "reason": reason,
        "kind": kind,
        "source_identity": source_identity,
        "source_paths": source_paths,
        "configuration": config,
        "archive": str(Path(record["events"]).parent),
        "write_diagnosis": restoration,
        "unrestored": restoration["unrestored"],
    }
    if kind in _ROUTE_STOPS:
        failure["pause_status"] = _ROUTE_STOPS[kind]
    state.pop("job_retry_authorization", None)
    state["recovery_context"] = {
        "kind": "job_failure",
        "stage": stage,
        "attempt_id": original_attempt,
        "events": record["events"],
        "reason": reason,
        "write_diagnosis": restoration,
    }
    state.update(
        status="PAUSED_STAGE_ABANDONED" if abandoned else "PAUSED_JOB_FAILURE",
        phase="PAUSED_OR_BLOCKED",
        next_stage=stage,
        stop_reason=reason,
        pending_questions=[],
    )
    state.pop("user_request", None)
    # The model question is kept with the failure, never published: the job's answer carries its
    # retry token (autocode_job_route).
    attempt = "pause_status" in failure and quota_route.stopped_attempt(
        state, failure_status=runtime.support.failure_status
    )
    if attempt and attempt["kind"] == "job":
        failure["route"] = _question(launch or runtime, state, attempt)
        failure["reason"] = state["stop_reason"] = (
            reason
            + ("" if reason.endswith(".") else ".")
            + " "
            + quota_route.advice(failure["route"], original_attempt, kind="job")
        )
    runtime.write_json(Path(run_dir) / "state.json", state)
    for artifact in originals:
        artifact.unlink(missing_ok=True)
    return True


def authorize(runtime, state, run_dir, workspace, token):
    failure = state.get("job_failure") or {}
    if state.get("status") not in PAUSES or not failure:
        raise ValueError("Exact job retry requires a paused failed workflow job")
    if not token or token != failure.get("job_retry_token"):
        raise ValueError("Job retry token does not match the current failed attempt")
    if not failure.get("source_identity"):
        raise ValueError(
            "Exact retry is unavailable: this attempt has no saved original source identity. "
            "Inspect the archived attempt and current changes before starting a new run."
        )
    # The restoration diagnosis describes the failed attempt. Missing capture
    # files or a later exact manual restoration must not make it a permanent
    # veto: recheck the saved full identity, then check it again at admission.
    if not source.matches_original(workspace, failure["source_identity"], source_paths=failure.get("source_paths", ())):
        if failure.get("unrestored"):
            raise ValueError("Unrestored source blocks job retry: " + ", ".join(failure["unrestored"]))
        raise ValueError("Source changed since the failed attempt; retry is stale")
    if configuration(state) != failure["configuration"]:
        raise ValueError("Provider configuration or limits changed; retry is stale")
    state["job_retry_authorization"] = {"token": token, "stage": failure["stage"], "consumed": False}
    state.setdefault("user_events", []).append(
        {
            "kind": "job_retry_authorized",
            "actor": "user_cli",
            "at": util.now(),
            "attempt_id": failure["attempt_id"],
            "token": token,
        }
    )
    state.update(status="RUNNING", phase="EXECUTING", next_stage=failure["stage"])
    state.pop("stop_reason", None)
    runtime.write_json(Path(run_dir) / "state.json", state)


def reroute(runtime, state, run_dir, workspace, assignment):
    """Bind the stopped job's exact retry to the model a person named for it (#463); return the new token.

    ``assignment`` is the route_assignment quota_route.assign just applied for the failure's own
    attempt. Nothing else may have changed: the source must still be the original, and the run's
    configuration must equal the failure's except that role's model, which is now ``to``. A route
    already moved off the bound model (a --<role>-model flag saved at a job stop before #463) is
    rebound by the answer too, so that run keeps a way forward. The failure then binds the new
    configuration under a new token, so the shown token stops matching and authorize/admit keep
    their exact checks. The job's model question is asked again against the new configuration
    with ``runtime``'s launch rules, so its candidates never list the model the job now runs on.
    Writes nothing: the caller commits the state.
    """
    failure = state.get("job_failure") or {}
    route = failure.get("route") or {}
    if state.get("status") not in PAUSES or not route:
        raise ValueError("Only a workflow job stopped on quota or a content-filter refusal takes another model")
    role = route.get("route_role")
    if assignment.get("role") != role or assignment.get("attempt_id") != failure.get("attempt_id"):
        raise ValueError(
            f"The model answer does not name the stopped {route.get('job')} attempt {failure.get('attempt_id')}"
        )
    if not failure.get("source_identity"):
        raise ValueError("Exact retry is unavailable: this attempt has no saved original source identity")
    if not source.matches_original(workspace, failure["source_identity"], source_paths=failure.get("source_paths", ())):
        if failure.get("unrestored"):
            raise ValueError("Unrestored source blocks job retry: " + ", ".join(failure["unrestored"]))
        raise ValueError("Source changed since the failed attempt; retry is stale")
    expected = copy.deepcopy(failure["configuration"])
    bound = (expected.get("roles") or {}).get(role)
    current = configuration(state)
    if not isinstance(bound, dict):
        raise ValueError("Provider configuration or limits changed; retry is stale")
    bound["model"] = assignment.get("to")
    if current != expected:
        raise ValueError("Provider configuration or limits changed; retry is stale")
    previous = failure["job_retry_token"]
    failure.update(configuration=current, route_assignment=copy.deepcopy(assignment))
    failure["route"] = _reasked(runtime, state, failure)
    failure["job_retry_token"] = "jr:" + util.digest(
        {
            "run": str(run_dir),
            "attempt": failure["attempt_id"],
            "previous": previous,
            "source": failure["source_identity"],
            "configuration": current,
            "route_assignment": failure["route_assignment"],
        }
    )
    state.pop("job_retry_authorization", None)
    failure["reason"] = state["stop_reason"] = _route_reason(failure)
    return failure["job_retry_token"]


def admit(state, record, workspace):
    if not owner(record):
        return
    record["job_configuration"] = configuration(state)
    capture = record.get("job_source") or {}
    if capture.get("before_identity") != util.digest(
        source.identity(workspace, source_paths=capture.get("source_paths", ()))
    ):
        raise util.Paused("PAUSED_STALE_VALIDATION", "Source changed before workflow-job admission")
    if not state.get("job_failure"):
        return
    failure = state["job_failure"]
    auth = state.get("job_retry_authorization") or {}
    capture = record.get("job_source") or {}
    if (
        auth.get("consumed")
        or auth.get("token") != failure["job_retry_token"]
        or auth.get("stage") != owner(record)
        or configuration(state) != failure["configuration"]
        or capture.get("before_identity") != failure["source_identity"]
    ):
        raise util.Paused("PAUSED_JOB_FAILURE", "Exact job retry admission changed; no provider launched")
    auth["consumed"] = True  # persisted with active_stage immediately before Popen
    record["job_retry_token"] = auth["token"]


def supersede(state):
    """Drop a job failure a later non-job pause replaced. Its token must not retry the old stage."""
    state.pop("job_failure", None)
    state.pop("job_retry_authorization", None)


def resume_gate(state, *, resume, retry, token):
    """How ``--resume-paused`` meets ``job_failure``.

    ``'authorize'`` checks the token and retries that job. An error string stops
    the invocation. None continues. A failure whose recovery record is a later
    attempt is removed, so a plain resume continues that later stage (#567).
    """
    if state.get("job_failure") and state.get("status") in PAUSES and not quota_route.job_pause_current(state):
        if token or retry:
            return (
                "this pause is not the stopped workflow job. The retry token belongs to an earlier "
                "attempt. Resume with --resume-paused to continue."
            )
        supersede(state)
    if state.get("job_failure") and (quota_route.job_pause_current(state) or token):
        if not (resume and retry):
            return state.get("stop_reason") or "Inspect the retained workflow-job attempt before retrying."
        return "authorize"
    return None


def completed(state, stage):
    if stage == (state.get("job_failure") or {}).get("stage"):
        state.pop("job_failure", None)
        state.pop("job_retry_authorization", None)
