"""One fresh attempt an operator authorizes at an operational hold (#301).

Two stops publish an AutoResolver operational request that neither a plain resume nor corrective
information followed by a resume can lift, by design (information is not authority):

- a repeated OpenCode external_directory denial, held after one workspace-only retry or at the
  denial ceiling (autocode_permission_recovery), status ``PAUSED_REPEATED_FAILURE``;
- AutoResolver's exhausted operational recovery after a stopped attempt, ``PAUSED_RESOLVER_OPERATIONAL``.

``--resume-paused --retry-failed-stage`` lifts either for exactly one fresh attempt. ``target`` is the
one eligibility check: the stop's advice (autocode_resolver_runtime) names the flag only where the
authorization (autocode_stage_recovery.authorize_failure_retry) accepts it, as autocode_recovery_grants
does for ``--grant-recovery`` (#288).

Nothing is reset: permission recoveries, the automatic-recovery budget, failure history and limits stay
as they are, so the next denial holds again and the next exhaustion stops again. ``authorize`` appends
the decision to ``failure_retry_authorizations`` (with ``kind``) and ``user_events``, bound to the
stopped attempt's event log; a stopped attempt is authorized at most once. Readers: ``lifts_permission_hold``
(the launch guard, autocode_recovery_limits.stop_reason), which lets past only the exact denial that
was authorized, and autocode_stuck_job.operator_retried. So an authorization outlives a stop that comes
before its attempt launches (a time limit, say), but no later denial: that one holds again.
"""
from __future__ import annotations

import copy
import subprocess

try:
    from . import autocode_permission_recovery as permission_recovery
    from .autocode_util import digest
except ImportError:
    import autocode_permission_recovery as permission_recovery
    from autocode_util import digest


PERMISSION_HOLD = "permission_hold"
OPERATIONAL_EXHAUSTION = "operational_exhaustion"
CAUSES = {"PAUSED_REPEATED_FAILURE": PERMISSION_HOLD, "PAUSED_RESOLVER_OPERATIONAL": OPERATIONAL_EXHAUSTION}
# Anything still owning the boundary must be reconciled first; a fresh attempt never replaces it.
# Pending questions count too, unless they are the operational request's own, which authorizing withdraws.
UNRECONCILED = ("active_stage", "uncertain_artifacts", "pending_report_repair", "job_failure")
NOTICES = {
    PERMISSION_HOLD: "Retry authorized past the repeated external_directory denial; one fresh attempt "
                     "proceeds under existing permissions, limits and recovery accounting.",
    OPERATIONAL_EXHAUSTION: "Retry authorized for the stopped attempt; one fresh attempt proceeds under "
                            "existing limits and recovery accounting.",
}


def _operational_request(state, published):
    """The proposal behind ``published`` (the saved public request) when it is the resolver's own pending,
    intact operational request, else None. Its binding may be stale: every condition ``target`` checks is
    re-read from ``state``."""
    key = (published or {}).get("request_id")
    entry = ((state.get("resolver") or {}).get("human_escalations") or {}).get(key) or {}
    identity = entry.get("identity") or {}
    proposal = identity.get("proposal") or {}
    if (state.get("status") != "WAITING_FOR_USER" or entry.get("status") != "pending"
            or identity.get("issuer") != "resolver" or proposal.get("scope") != "operational_exhaustion"
            or digest(identity) != key):
        return None
    return proposal


def stop_cause(state, published):
    """The pause a stopped run is in: its status, or the pause behind its pending operational request."""
    status = str(state.get("status") or "")
    if status.startswith("PAUSED_"):
        return status
    return ((_operational_request(state, published) or {}).get("origin") or {}).get("pause_status")


def permission_hold(state, revision):
    """The latest external_directory recovery when it holds the run at its unchanged frontier, else None."""
    recoveries = state.get("automatic_permission_recoveries") or []
    latest = recoveries[-1] if recoveries and isinstance(recoveries[-1], dict) else {}
    context = state.get("recovery_context") or {}
    if (not latest.get("denied_operation") or not latest.get("events")
            or any(context.get(key) != latest.get(key) for key in ("attempt_id", "incident_id", "events"))
            or latest.get("source_revision") != revision or latest.get("next_stage") != state.get("next_stage")):
        return None
    # The two conditions automatically_recover_external_directory_denial pauses on.
    if not (latest.get("repeat_count", 0) >= 2
            or latest.get("denied_since_accepted", 0) >= permission_recovery.MAX_PERMISSION_RECOVERIES):
        return None
    return latest


def stopped_attempt(state, revision, is_planning):
    """The recovery record of the stopped attempt behind an exhausted operational recovery, else None.

    Planning has its own explicit allowance (--planning-review-call-limit), so it is never retried here.
    """
    context = state.get("recovery_context") or {}
    stage = state.get("next_stage")
    if (not context.get("events") or context.get("source_revision") != revision
            or not isinstance(stage, str) or not stage or is_planning(state, stage)):
        return None
    row = next((row for row in reversed(state.get("stages") or [])
                if row.get("events") == context["events"] and not row.get("runner_owned")), None)
    if not row or not (row.get("abandoned") or row.get("rejected")) or row.get("source_revision") != revision:
        return None
    return context


def _authorization(state, recovery):
    return next((row for row in state.get("failure_retry_authorizations") or []
                 if row.get("kind") in CAUSES.values() and row.get("events") == recovery.get("events")), None)


def target(state, *, cause, revision, is_planning, published=None):
    """``(kind, recovery record)`` when one fresh attempt may lift this stop, else None.

    ``revision`` returns the current source revision; it is read only for a stop this can lift.
    """
    kind = CAUSES.get(cause)
    if kind is None or any(state.get(key) for key in UNRECONCILED):
        return None
    questions = state.get("pending_questions") or []
    if questions and not (_operational_request(state, published) and questions == published.get("questions")):
        return None
    try:
        current = revision()
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return None
    found = (permission_hold(state, current) if kind == PERMISSION_HOLD
             else stopped_attempt(state, current, is_planning))
    if not found or _authorization(state, found):
        return None
    return kind, found


def authorize(state, kind, recovery, *, now, failure_key=None, failure=None):
    """Record the operator's one fresh attempt and return it for this invocation's resume guard.

    ``failure``, the failure-history entry of the latest failed record (``failure_key``), lets
    autocode.repeated_failure_resume_guard accept this authorization once that history is stalled.
    """
    authorization = {"at": now, "kind": kind, "actor": "user_cli", "events": recovery["events"],
                     "attempt_id": recovery.get("attempt_id"), "stage": state.get("next_stage"),
                     "source_revision": recovery["source_revision"]}
    if kind == PERMISSION_HOLD:
        authorization.update(incident_id=recovery.get("incident_id"), repeat_count=recovery.get("repeat_count"),
                             denied_since_accepted=recovery.get("denied_since_accepted"))
    if failure_key and failure:
        authorization.update(failure_key=failure_key, identity=copy.deepcopy(failure.get("identity")),
                             count=failure.get("count"))
    state.setdefault("failure_retry_authorizations", []).append(authorization)
    state.setdefault("user_events", []).append({
        "kind": "failure_retry_authorized", "actor": "user_cli", "at": now, "scope": kind,
        "stage": authorization["stage"], "attempt_id": authorization["attempt_id"],
        "failure_key": authorization.get("failure_key"), "count": authorization.get("count")})
    return copy.deepcopy(authorization)


def lifts_permission_hold(state, context):
    """Whether an operator authorized one fresh attempt past exactly this denial hold."""
    row = _authorization(state, context)
    return bool(row and row.get("kind") == PERMISSION_HOLD and row.get("incident_id") == context.get("incident_id")
                and row.get("source_revision") == context.get("source_revision"))
