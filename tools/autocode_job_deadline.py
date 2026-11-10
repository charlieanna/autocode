"""A workflow job stopped by its hard stage deadline retries itself with a longer one (#2605).

A hard stage limit is AutoCode's own machinery: the model was working, and nothing about its
route needs deciding. So instead of pausing on a person question about models, the stopped job
(the Investigator of a stuck stage, the Design Reviewer, ...) is retried once more on the same route with its stage
deadline doubled, never past the run's remaining total time, at most ``MAX_EXTENSIONS`` times for
that job in a run. The run's money cap still applies before every launch. Each extension is
recorded as a runner ``user_events`` entry (``EVENT``) with its reason, so the report and the
verification view show the decision.

The longer deadline belongs to that job's exact retry, not to the run (#2823): it is saved as the
failure's ``retry_stage_timeout_seconds`` and ``launch_limit`` gives it only to the authorized retry
of that job. The run's own ``stage_timeout_seconds`` stays as the person set it, so every other
stage, and the job's next run after it completes, keep their deadline.

``plan`` and ``launch_limit`` are pure; autocode_job_failure applies them. Imports nothing from AutoCode.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

MAX_EXTENSIONS = 2
EVENT = "automatic_job_stage_deadline_extended"


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def used(state: RunState, stage) -> int:
    """Automatic deadline extensions already spent on ``stage`` in this run."""
    return sum(
        1
        for event in state.get("user_events") or []
        if isinstance(event, dict) and event.get("kind") == EVENT and event.get("stage") == stage
    )


def _seconds(value):
    return value if type(value) is int and value > 0 else None


def plan(state: RunState, failure: dict, recovery: dict, *, abandoned: bool, deadline=None) -> dict | None:
    """The extension to apply for this stopped job, or None when the stop is not one AutoCode settles.

    Only an authenticated hard stage-deadline stop qualifies: not a person setting the attempt
    aside, not another limit (an idle or exploration-budget stop), not a stage whose deadline is
    unset or already at the run's remaining time, and not a job that spent its extensions.
    ``deadline`` is the deadline the stopped attempt ran under (a retry may already have run on an
    extended one); without it, the run's saved stage limit.
    """
    if (
        abandoned
        or failure.get("kind") != "timeout"
        or recovery.get("timeout_kind") != "stage"
        or recovery.get("attempt_id") != failure.get("attempt_id")
    ):
        return None
    limits = (state.get("settings") or {}).get("limits") or {}
    old = _seconds(limits.get("stage_timeout_seconds") if deadline is None else deadline)
    if old is None:
        return None
    spent = used(state, failure.get("stage"))
    if spent >= MAX_EXTENSIONS:
        return None
    new = old * 2
    total = _number(limits.get("max_seconds"))
    if total is not None and total > 0:
        new = min(new, int(total - (_number(state.get("active_seconds")) or 0)))
    if new <= old:
        return None
    return {"from": old, "to": new, "extension": spent + 1, "of": MAX_EXTENSIONS}


def launch_limit(state: RunState, stage, configured, *, report_only=False):
    """The hard deadline a launch of ``stage`` runs under.

    The authorized, not yet admitted retry of the stopped job runs on the deadline saved with its
    failure (an automatic extension, or the deadline the stopped attempt had). Every other launch,
    report-only repairs included, runs on the run's ``configured`` stage limit.
    """
    failure = state.get("job_failure") or {}
    authorization = state.get("job_retry_authorization") or {}
    retry = _seconds(failure.get("retry_stage_timeout_seconds"))
    if (
        report_only
        or retry is None
        or failure.get("stage") != stage
        or authorization.get("stage") != stage
        or authorization.get("consumed") is not False
        or not failure.get("job_retry_token")
        or authorization.get("token") != failure["job_retry_token"]
    ):
        return configured
    return retry


def reason(job: str, extension: dict) -> str:
    """What AutoCode decided, for the stop record and the report."""
    return (
        f"{job} reached its {extension['from']}-second stage deadline while working. AutoCode retries it on the "
        f"same model with a {extension['to']}-second deadline (extension {extension['extension']} of "
        f"{extension['of']}), within the run's time and cost caps."
    )


def exhausted(job: str, spent: int) -> str:
    """Why a timed-out job paused after AutoCode's own extensions: the retry is all that is left."""
    return (
        f"AutoCode already retried the {job} on {spent} longer deadline{'s' if spent != 1 else ''} on the same "
        "model, so it now waits for its exact retry; no other model is needed"
    )
