"""What the next resume of a retained acceptance verification retry does: the one answer (#3927).

A completed Tester's report whose local verification failed is kept (autocode_acceptance_verification_retry)
and re-checked without a model call. Dispatch, the status view, the recovery card, the chat input and the
progress headline all read ``decide``, so none of them can offer a "Tester attempt" that only re-checks the
saved tests, or a resume that an exhausted bound would refuse.

``next`` is one of:

- ``recheck``: the next resume runs the saved tests again, without a new Tester attempt. It is the
  one local re-check (``LIMIT``), one more for a bound Investigator result whose recorded cause is
  ``environment`` or none (once per advice identity), the one authenticated provenance re-check, or a
  runner that never started (retried on each resume, unbounded: ``allowed`` is None).
- ``tester``: bound, in-force Investigator advice for the Tester's stage places the cause in the written
  tests or report (a recorded cause other than ``environment``). Re-reading the pinned report cannot edit
  it, so the retained retry is retired and the ordinary test-writing step runs with that guidance. One
  Investigator result, its (identity, requested_at), retires at most one retained retry.
- ``exhausted``: the bounded recovery has no re-check left and reports the unresolved failure.

``rechecks`` is the local re-checks spent and ``allowed`` how many this retry may spend. A changed
report, source, plan or task is blocked by the same admission the dispatcher uses.

Pure over the run state; imports only lower-level modules. The record is the acceptance_tests record's
``verification_retry`` (written by autocode_acceptance_verification_retry) and the Investigator records
``stuck_investigation`` and ``stuck_investigations`` (written by autocode_stuck_job).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_acceptance_provenance as provenance
    from . import autocode_acceptance_retry_admission as admission
    from . import autocode_acceptance_tests as acceptance
    from . import autocode_planning_advice as recovery_binding
except ImportError:
    import autocode_acceptance_provenance as provenance
    import autocode_acceptance_retry_admission as admission
    import autocode_acceptance_tests as acceptance
    import autocode_planning_advice as recovery_binding

STAGE: Final = acceptance.STAGE
KEY: Final = "verification_retry"
LIMIT: Final = 1
RUNNER_NOT_STARTED: Final = "runner_not_started"
RECHECK: Final = "recheck"
TESTER: Final = "tester"
EXHAUSTED: Final = "exhausted"
BLOCKED: Final = "blocked"
PAUSE: Final = "PAUSED_INVALID_OUTPUT"
PUBLIC: Final = ("next", "rechecks", "allowed")
# The reconciliation note each local re-check records, which the recovery history reads to say what ran.
RECHECK_REASON: Final = "Retrying local acceptance verification without another model call"


def pending(state: RunState) -> dict | None:
    record = (state.get(acceptance.KEY) or {}).get(KEY)
    return record if isinstance(record, dict) else None


def _advice(state: RunState) -> dict | None:
    """In-force Investigator advice for the Tester's rejected answer, bound to the current inputs."""
    advice = state.get("stuck_investigation") or {}
    identity = advice.get("identity")
    if (
        advice.get("in_force") is True
        and advice.get("stage") == STAGE
        and advice.get("status") == PAUSE
        and isinstance(identity, str)
        and identity
        and recovery_binding.applies(state, advice, STAGE)
    ):
        return advice
    return None


def _result(state: RunState, identity: str) -> dict | None:
    """The Investigator result behind in-force advice, found as autocode_stuck_job.apply records it."""
    return next(
        (
            row
            for row in reversed(state.get("stuck_investigations") or [])
            if isinstance(row, dict) and row.get("identity") == identity and row.get("outcome") == "retried"
        ),
        None,
    )


def _retired(state: RunState, key: dict) -> bool:
    """Whether this Investigator result already retired a retained retry."""
    archived = (state.get(acceptance.KEY) or {}).get("verification_retries") or []
    return any(isinstance(row, dict) and row.get("retired_by") == key for row in archived)


def _answer(kind: str, rechecks: int, allowed: int | None, **internal) -> dict:
    return {"next": kind, "rechecks": rechecks, "allowed": allowed, **internal}


def decide(state: RunState, workspace) -> dict | None:
    """The next resume of the pending retained retry, or None without one (or with an unreadable count).

    Besides the public ``next``, ``rechecks`` and ``allowed``, a ``recheck`` names the advice identity it
    spends (``grant``) and whether it is the provenance re-check (``provenance``); a ``tester`` names the
    Investigator result that retires the retry (``retired_by``)."""
    saved = state.get(acceptance.KEY) or {}
    raw = saved.get(KEY) if isinstance(saved, dict) else None
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return _answer(BLOCKED, 0, None, reason="The saved acceptance verification retry is malformed")
    retry = raw
    attempts = retry.get("attempts")
    if refusal := admission.problem(state, retry, workspace):
        return _answer(BLOCKED, attempts if type(attempts) is int and attempts >= 0 else 0, None, reason=refusal)
    if type(attempts) is not int or attempts < 0:
        return None
    if retry.get("kind") == RUNNER_NOT_STARTED:
        return _answer(RECHECK, attempts, None, grant=None, provenance=False)
    investigations = [row for row in retry.get("investigations") or [] if isinstance(row, str)]
    granted = LIMIT + len(investigations) + bool(retry.get("provenance_retry"))
    advice = _advice(state)
    result = _result(state, advice["identity"]) if advice else None
    cause = (result or {}).get("cause")
    if advice and result and isinstance(cause, str) and cause and cause != "environment":
        key = {"identity": advice["identity"], "requested_at": result.get("requested_at")}
        if not _retired(state, key):
            return _answer(TESTER, attempts, granted, retired_by=key)
        advice = None  # A spent test-cause result grants no re-check of the saved tests.
    if attempts < LIMIT:
        return _answer(RECHECK, attempts, granted, grant=None, provenance=False)
    if advice and advice["identity"] not in investigations:
        return _answer(RECHECK, attempts, granted + 1, grant=advice["identity"], provenance=False)
    value = retry.get("value")
    if not isinstance(value, dict):
        value = {}
    passing = value.get("already_passing") or []
    passing_ids = {row.get("case_id") for row in passing if isinstance(row, dict)}
    if (
        not retry.get("provenance_retry")
        and passing_ids
        and len(passing_ids) == len(passing)
        and passing_ids <= provenance.inherited_cases(state, reports=value.get("tests"), workspace=workspace)
    ):
        return _answer(RECHECK, attempts, granted + 1, grant=None, provenance=True)
    return _answer(EXHAUSTED, attempts, granted)


def public(answer: dict | None) -> dict | None:
    """The status view's ``acceptance_verification_retry``: the answer without its internal keys."""
    return {key: answer[key] for key in PUBLIC} if answer else None


def exhausted_pause(state: RunState, answer: dict | None) -> bool:
    """A paused retained retry cannot recheck; preserve separate questions or attempt actions."""
    active = state.get("active_stage") or {}
    abandonable = isinstance(active, dict) and active.get("output") and isinstance(active.get("iteration"), int)
    return bool(
        answer
        and answer.get("next") in (EXHAUSTED, BLOCKED)
        and state.get("status") == PAUSE
        and state.get("next_stage") == STAGE
        and (not active or (answer.get("next") == BLOCKED and not abandonable))
        and (answer.get("next") == BLOCKED or not state.get("active_runner_check"))
        and not state.get("pending_questions")
        and not state.get("user_request")
    )


def sentence(answer: dict) -> str:
    """What the retained re-checks did, in the person's words."""
    return (
        f"AutoCode re-checked the saved tests {answer['rechecks']} of {answer['allowed']} allowed times "
        "without a new Tester attempt."
    )
