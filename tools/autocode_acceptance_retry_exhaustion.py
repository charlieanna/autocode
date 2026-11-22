"""End a run whose retained acceptance re-check has none left, with a recorded outcome (#4037).

A completed Tester's report whose local verification failed is kept and re-checked without a model call
(autocode_acceptance_verification_retry). When ``autocode_acceptance_retry_next.decide`` answers
``exhausted`` (the bounded re-checks are spent and no Investigator result grants another), the Investigator
has already had its look when the pause was raised (autocode_stuck_job.intercept). Nothing automatic
remains. The run
used to stay ``PAUSED_INVALID_OUTPUT`` with no next command, no chat input and a recovery card that offered
only inspection: a pause no client could get past, which hands the person AutoCode's machinery (AGENTS.md
"What a person is asked").

``conclude`` instead ends the run with a recorded ``could_not_finish`` outcome (``autocode_stop.end``,
#2485, the owner's choice on #4037) whose evidence names the re-checks spent and allowed, every
verification failure and the Investigator's look. A ``blocked`` answer is not this: it is a hold that
another recovery settles (an attempt still being reconciled, a status read that timed out, changed inputs). A raised pause ends at once (``conclude_pause``); a
checkpoint saved at that pause is ``pending``: the view names ``continue`` and the next resume ends it
(``settle``).

Pure policy over saved state; imports only lower-level modules.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_acceptance_retry_next as retry_next
    from . import autocode_person_text as person_text
    from . import autocode_stop as stop
except ImportError:
    import autocode_acceptance_retry_next as retry_next
    import autocode_person_text as person_text
    import autocode_stop as stop

STATUS = retry_next.PAUSE
# What a saved checkpoint shows until its next resume ends it.
NOTICE = "AutoCode will end this task with a report: no re-check of the Tester's saved tests is left."


def pending(state: RunState, answer: dict | None) -> bool:
    """A saved pause at a spent retained re-check that the next resume ends."""
    return (
        stop.applied_stop(state) is None
        and bool(answer)
        and (answer or {}).get("next") == retry_next.EXHAUSTED
        and retry_next.exhausted_pause(state, answer)
    )


def _outcome(state: RunState, answer: dict) -> tuple[str, dict[str, Any]]:
    retry = retry_next.pending(state) or {}
    failures = [copy.deepcopy(row) for row in retry.get("failures") or [] if isinstance(row, dict)]
    last = next((str(row["reason"]) for row in reversed(failures) if row.get("reason")), None)
    reason = "The Tester's saved tests could not be verified. " + retry_next.sentence(answer)
    if last:
        reason += " Last failure: " + (person_text.for_person(last) or last)
    evidence: dict[str, Any] = {
        "pause_status": STATUS,
        "stage": retry_next.STAGE,
        "verification_retry": retry_next.public(answer),
        "failures": failures,
        "investigations": [
            copy.deepcopy(row)
            for row in state.get("stuck_investigations") or []
            if isinstance(row, dict) and row.get("stage") == retry_next.STAGE
        ],
    }
    return reason, evidence


def conclude(state: RunState, answer: dict | None, *, at: str, cause: str | None = None) -> bool:
    """End the run with the spent re-check as its recorded outcome; True when it ended."""
    if not answer or not pending(state, answer):
        return False
    reason, evidence = _outcome(state, answer)
    if cause:
        evidence["paused_with"] = cause
    stop.end(state, reason, evidence, at=at)
    return True


def settle(state: RunState, workspace, *, at: str) -> bool:
    """At resume, end a saved pause whose retained re-check has none left."""
    if retry_next.pending(state) is None:
        return False
    return conclude(state, retry_next.decide(state, workspace), at=at)


def conclude_pause(state: RunState, error, workspace, *, at: str) -> bool:
    """End the run at a raised re-check pause the Investigator has already taken or declined."""
    if getattr(error, "status", None) != STATUS or retry_next.pending(state) is None:
        return False
    answer = retry_next.decide(state, workspace)
    # The pause is being raised: judge it as the saved pause it would otherwise become.
    probe: Any = {**state, "status": STATUS}
    if not conclude(probe, answer, at=at, cause=str(error)):
        return False
    state.update(probe)
    return True
