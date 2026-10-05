"""One fresh attempt an operator authorizes with ``--resume-paused --retry-failed-stage``.

Two stops accept it:

- an unchanged repeated failure (autocode.repeated_failure_resume_guard checks the authorization in
  the same invocation);
- a held OpenCode external_directory denial (#301): the same denial again after one workspace-only
  retry, or the denial ceiling (autocode_permission_recovery.holds), status ``PAUSED_REPEATED_FAILURE``.
  Corrective information alone never lifts it, by design: information is not authority.

``target`` is the one eligibility check for the denial hold. Its stop names the flag
(autocode_resolver_runtime) only where autocode_stage_recovery.authorize_failure_retry accepts it
and the launch guard (autocode_recovery_limits.stop_reason) and the build loop's unchanged-batch limit
then admit the attempt, as autocode_recovery_grants does for ``--grant-recovery`` (#288).

An authorization belongs to the command that carries the flag:

1. autocode_stage_recovery.authorize_failure_retry validates it and writes nothing (``authorization``);
2. once every other check of that command has passed, autocode_run_actions.handle has it saved
   (``record``, through autocode_stage_recovery.record_failure_retry, which also withdraws the
   operational request it answers) and ``arm``s it for this process only;
3. the launch guard lifts the denial hold for an armed authorization alone (``lifts``);
4. the first attempt admitted afterwards consumes it (``launched``, saved with the attempt).

A rejected command therefore saves nothing. A command stopped or killed after step 2 leaves an
unlaunched row that lifts nothing for any later command, and re-issuing the flag for the same stopped
attempt reuses it. Each stopped attempt launches at most one authorized attempt, and no count or limit
is reset, so the next denial holds again.

State written only here:
- failure_retry_authorizations: rows {at, actor, kind ('repeated_failure' | 'permission_hold'), stage,
  attempt_id, events and source_revision (the stopped attempt), failure_key, identity, count (its
  failure-history entry, or None), incident_id (the denial, or None), launched_attempt, launched_at}.
  Read by ``target``, ``lifts`` and autocode_stuck_job.operator_retried.
- user_events: one ``failure_retry_authorized`` event per row, with the same scope and binding.
"""
from __future__ import annotations

import copy
from pathlib import PurePath
import subprocess

try:
    from . import autocode_permission_recovery as permission_recovery
    from . import autocode_recovery_accounting as accounting
    from .autocode_util import digest
except ImportError:
    import autocode_permission_recovery as permission_recovery
    import autocode_recovery_accounting as accounting
    from autocode_util import digest


REPEATED_FAILURE = "repeated_failure"
PERMISSION_HOLD = "permission_hold"
HOLD_STATUS = "PAUSED_REPEATED_FAILURE"
# Anything still owning the boundary must be reconciled first; a fresh attempt never replaces it.
# Pending questions count too, unless they are the operational request's own, which authorizing withdraws.
UNRECONCILED = ("active_stage", "uncertain_artifacts", "pending_report_repair", "job_failure")
NOTICE = ("Retry authorized past the held external_directory denial: one fresh attempt, under existing "
          "permissions; no count or limit is reset.")

# This process's validated authorization (see ``arm``); never saved.
_armed = None


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


def held_denial(state, revision):
    """The latest external_directory recovery when it holds the run at the ``revision`` it stopped at
    (the launch guard's own check, autocode_permission_recovery.holds), else None."""
    recoveries = state.get("automatic_permission_recoveries") or []
    latest = recoveries[-1] if recoveries and isinstance(recoveries[-1], dict) else {}
    context = state.get("recovery_context") or {}
    if (not latest.get("events") or latest.get("next_stage") != state.get("next_stage")
            or any(context.get(key) != latest.get(key) for key in ("attempt_id", "incident_id", "events"))
            or not permission_recovery.holds(latest, state.get("stages") or [], lambda: revision)):
        return None
    return latest


def _rows(state):
    return [row for row in state.get("failure_retry_authorizations") or [] if isinstance(row, dict)]


def _binding(row):
    return tuple(row.get(key) for key in ("kind", "events", "source_revision", "failure_key", "count", "incident_id"))


def target(state, *, cause, revision, published=None, maximum=accounting.MAX_AUTOMATIC_RECOVERIES):
    """The held denial recovery when one authorized fresh attempt may launch past it, else None.

    ``revision`` returns the current source revision; it is read only for a stop this can lift. The
    attempt must not have launched for this stopped attempt yet, and the launch guard's budget check
    and the build loop's unchanged-batch limit must admit it.
    """
    if cause != HOLD_STATUS or any(state.get(key) for key in UNRECONCILED):
        return None
    questions = state.get("pending_questions") or []
    if questions and not (_operational_request(state, published) and questions == published.get("questions")):
        return None
    try:
        current = revision()
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return None
    held = held_denial(state, current)
    if not held or any(row.get("kind") == PERMISSION_HOLD and row.get("events") == held["events"]
                       and row.get("launched_attempt") for row in _rows(state)):
        return None
    # The authorized attempt meets the launch guard's budget (stop_reason) and the build loop's
    # unchanged-batch limit like any other launch; a denial adds to neither, so a hold rarely trips them.
    limit = ((state.get("settings") or {}).get("limits") or {}).get("no_progress_batches")
    if (accounting.exhausted(state, accounting.spent(state), maximum)
            or (limit and state.get("no_progress_batches", 0) >= limit)):
        return None
    return held


def authorization(state, kind, stopped, *, now, failure_key=None, identity=None, count=None):
    """The row an operator's authorization for ``stopped`` saves, without saving it."""
    return {"at": now, "actor": "user_cli", "kind": kind, "stage": state.get("next_stage"),
            "attempt_id": stopped.get("attempt_id"), "events": stopped.get("events"),
            "source_revision": stopped.get("source_revision"), "failure_key": failure_key,
            "identity": copy.deepcopy(identity), "count": count,
            "incident_id": stopped.get("incident_id") if kind == PERMISSION_HOLD else None,
            "launched_attempt": None, "launched_at": None}


def record(state, authorization):
    """Save a row built by ``authorization`` and its event; return a copy of the saved row.

    An unlaunched row with the same binding is reused, so re-issuing the flag for the same stopped
    attempt neither fails nor adds a second authorization.
    """
    row = {key: copy.deepcopy(authorization.get(key)) for key in (
        "at", "actor", "kind", "stage", "attempt_id", "events", "source_revision", "failure_key",
        "identity", "count", "incident_id", "launched_attempt", "launched_at")}
    saved = next((old for old in _rows(state) if not old.get("launched_attempt")
                  and old.get("stage") == row["stage"] and _binding(old) == _binding(row)), None)
    if saved is None:
        saved = row
        state.setdefault("failure_retry_authorizations", []).append(row)
        state.setdefault("user_events", []).append({
            "kind": "failure_retry_authorized", "actor": row["actor"], "at": row["at"], "scope": row["kind"],
            "stage": row["stage"], "attempt_id": row["attempt_id"], "failure_key": row["failure_key"],
            "count": row["count"]})
    return copy.deepcopy(saved)


def arm(authorization):
    """Let this process launch the attempt ``authorization`` names; None clears it."""
    global _armed
    _armed = ({key: authorization.get(key) for key in ("kind", "stage", "events", "source_revision",
                                                        "failure_key", "count", "incident_id")}
              if authorization else None)


def disarm():
    arm(None)


def _armed_row(state):
    if not _armed:
        return None
    return next((row for row in _rows(state) if not row.get("launched_attempt")
                 and row.get("stage") == _armed["stage"] and _binding(row) == _binding(_armed)), None)


def lifts(state):
    """Whether this invocation's authorization admits the next attempt past the held denial."""
    context = state.get("recovery_context") or {}
    row = _armed_row(state)
    return bool(row and row["kind"] == PERMISSION_HOLD and state.get("next_stage") == row["stage"]
                and all(context.get(key) == row.get(key) for key in ("events", "incident_id", "source_revision")))


def launched(state, attempt):
    """Consume this invocation's authorization at the first attempt admitted after it.

    The row records that attempt when it is the authorized stage's own; the caller saves ``state``
    with the attempt itself, so the row is used exactly when it launches. Any other first attempt
    only disarms it: the saved row stays unlaunched and the flag can be re-issued.
    """
    global _armed
    row = _armed_row(state)
    _armed = None
    if row and not attempt.get("report_only") and (attempt.get("original_stage") or attempt.get("stage")) == row["stage"]:
        row.update(launched_attempt=f"{attempt['iteration']:03d}/{PurePath(attempt['output']).stem}",
                   launched_at=attempt.get("started_at"))
