"""One fresh attempt an operator authorizes with ``--resume-paused --retry-failed-stage``.

Two stops accept it:

- a stalled repeated failure (``stalled_target``, #254): the same failure at least three times in a
  row at one source (autocode_failures.stalled), raw or behind its published operational request.
  The incident's spent report repair, if any, is archived with its pins and the role's session
  rotated, because a stalled incident is never repaired again (autocode.reject_completed_stage). At
  the unchanged source autocode.repeated_failure_resume_guard consumes the authorization in the same
  invocation; after an operator edit the attempt simply runs on the edited source, where its failure
  is a new identity with its normal repairs (this closes the #302 dead end for stalled failures).
- a held OpenCode external_directory denial (#301): the same denial again after one workspace-only
  retry, or the denial ceiling (autocode_permission_recovery), status ``PAUSED_REPEATED_FAILURE``.
  Corrective information alone never lifts it, by design: information is not authority.

``target`` (the denial hold) and ``stalled_target`` (the stalled failure) are the one eligibility
checks. A stop names the flag (autocode_resolver_runtime, autocode_run_actions) only where
autocode_stage_recovery.authorize_failure_retry accepts it and the launch guard then admits the
attempt, as autocode_recovery_grants does for ``--grant-recovery`` (#288).

The saved row never lifts a hold by itself. ``record`` writes it once the flag is accepted
(autocode_stage_recovery.authorize_failure_retry); autocode_run_actions.handle ``arm``s it for this
process only after all of that command's other validation has passed; ``lifts`` (the launch guard)
reads only that; the first attempt of the held stage admitted afterwards consumes it (``launched``,
saved with the attempt). A command that is rejected, stopped or killed before its attempt launches
therefore leaves the hold in place for every later command, and re-issuing the flag reuses the stopped
attempt's unlaunched row. Each stopped attempt launches at most one authorized attempt, and nothing is
reset, so the next denial holds again.

The authorized attempt passes the denial hold and the two stops that read ``no_progress_batches``
(spent()'s estimate for a run without a recorded recovery count, and the build loop's unchanged-batch
limit), because each Builder denial adds to that count: the denials being retried would otherwise stop
the one attempt retrying them. The recorded automatic-recovery budget and the consecutive-timeout limit
still apply to it, and every automatic launch keeps all of these stops.

State written only here:
- failure_retry_authorizations: rows {at, actor, kind ('repeated_failure' | 'permission_hold'), stage,
  attempt_id, events and source_revision (the stopped attempt), failure_key, identity, count (its
  failure-history entry, or None), incident_id (the denial, or None), launched_attempt, launched_at}.
  Read by ``target``, ``lifts``, autocode_stuck_job.operator_retried and the status view's
  failure_groups[].authorized_retries (autocode_recovery_view).
- user_events: one ``failure_retry_authorized`` event per row, with the same scope and binding.
"""
from __future__ import annotations

import copy
from pathlib import PurePath
import subprocess

try:
    from . import autocode_failures as failures
    from . import autocode_permission_recovery as permission_recovery
    from . import autocode_recovery_accounting as accounting
    from .autocode_util import digest
except ImportError:
    import autocode_failures as failures
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


def _rows(state):
    return [row for row in state.get("failure_retry_authorizations") or [] if isinstance(row, dict)]


def _binding(row):
    return tuple(row.get(key) for key in ("kind", "events", "source_revision", "failure_key", "count", "incident_id"))


def target(state, *, cause, revision, published=None, maximum=accounting.MAX_AUTOMATIC_RECOVERIES):
    """The held denial recovery when one authorized fresh attempt may launch past it, else None.

    ``revision`` returns the current source revision; it is read only for a stop this can lift. The
    attempt must not have launched for this stopped attempt yet, and the launch guard's budget check
    must admit it.
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
    # The same budget the launch guard applies to this attempt (stop_reason).
    if accounting.exhausted(state, accounting.recorded(state), maximum):
        return None
    return held


def stalled_target(state, *, cause, revision, published=None):
    """(record, entry, changed) for the stalled failure one authorized fresh attempt may retry.

    Raises ValueError with the refusal. ``record`` is the latest failed attempt and ``entry`` its
    stalled failure-history entry; a report repair still pending must be that incident's own (it is
    spent: a stalled incident is never repaired again). The current source may differ from the
    failure's: an edit is new evidence (#302). ``revision`` is read only to report that as
    ``changed`` (None when unreadable); it never refuses.
    """
    reconcile = 'Reconcile the active attempt or pending report repair before authorizing a retry'
    if cause != HOLD_STATUS:
        raise ValueError('--retry-failed-stage requires a run paused for repeated failure')
    if any(state.get(key) for key in UNRECONCILED if key != 'pending_report_repair'):
        raise ValueError(reconcile)
    questions = state.get('pending_questions') or []
    if questions and not (_operational_request(state, published) and questions == published.get('questions')):
        raise ValueError('Answer the pending questions before authorizing a retry')
    record = next((row for row in reversed(state.get('stages') or []) if row.get('failure_key')), None)
    pending = state.get('pending_report_repair')
    if pending and not (record and isinstance(pending, dict)
                        and (pending.get('original') or {}).get('failure_key') == record['failure_key']):
        raise ValueError(reconcile)
    entry = failures.repeated(state, record) if record else None
    if not entry:
        raise ValueError('No unchanged repeated failure to authorize; fix the cause, then resume')
    identity = entry['identity']
    if ((state.get('failure_history') or {}).get(record['failure_key']) is not entry
            or failures.key(identity) != record['failure_key']
            or identity['artifact_hash'] != record.get('source_revision')
            or identity['stage'] != (record.get('original_stage') or record.get('stage'))
            or identity['stage'] != state.get('next_stage')):
        raise ValueError('Retry authorization requires the exact recorded source, stage and failure identity')
    try:
        changed = revision() != record['source_revision']
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        changed = None
    return record, entry, changed


def retryable(state, *, cause, revision, published=None, maximum=accounting.MAX_AUTOMATIC_RECOVERIES):
    """Whether authorize_failure_retry accepts --retry-failed-stage at this stop (``target`` or
    ``stalled_target``); the one check behind every place that advertises the flag."""
    if target(state, cause=cause, revision=revision, published=published, maximum=maximum) is not None:
        return True
    try:
        stalled_target(state, cause=cause, revision=revision, published=published)
    except ValueError:
        return False
    return True


def record(state, kind, stopped, *, now, failure_key=None, identity=None, count=None):
    """Save the operator's authorization for ``stopped`` and return a copy for this invocation.

    An unlaunched row with the same binding is reused, so re-issuing the flag for the same stopped
    attempt neither fails nor adds a second authorization.
    """
    row = {"at": now, "actor": "user_cli", "kind": kind, "stage": state.get("next_stage"),
           "attempt_id": stopped.get("attempt_id"), "events": stopped.get("events"),
           "source_revision": stopped.get("source_revision"), "failure_key": failure_key,
           "identity": copy.deepcopy(identity), "count": count,
           "incident_id": stopped.get("incident_id") if kind == PERMISSION_HOLD else None,
           "launched_attempt": None, "launched_at": None}
    saved = next((old for old in _rows(state) if not old.get("launched_attempt")
                  and old.get("stage") == row["stage"] and _binding(old) == _binding(row)), None)
    if saved is None:
        saved = row
        state.setdefault("failure_retry_authorizations", []).append(row)
        state.setdefault("user_events", []).append({
            "kind": "failure_retry_authorized", "actor": "user_cli", "at": now, "scope": kind,
            "stage": row["stage"], "attempt_id": row["attempt_id"], "failure_key": failure_key, "count": count})
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
