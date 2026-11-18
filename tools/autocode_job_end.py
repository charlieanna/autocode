"""When a stopped workflow job is AutoCode's to settle, not a person's (pure policy).

A job its provider stopped used to pause for a person to press "retry" (``retry_job``) or, with no
saved source identity, to "inspect the archive before a new run" (``recover_source``). Neither is a
decision: the retry is AutoCode's own machinery (AGENTS.md "What a person is asked"). The job retries
itself once (autocode_job_provider_retry), and when that is spent, the denial would repeat, or there
is no saved source to retry from, autocode_job_failure ends the run with a recorded outcome.

A person's own stop or abandonment, a model question (quota, refusal and the other route stops) and
a source the person changed after the attempt stay with the person, as long as the attempt saved
its original source identity. Without it no exact retry can be checked, so every current job pause
ends with that recorded cause. Read by the status view and the
resume gate; imports nothing above the policy layer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_job_provider_retry as provider_retry
    from . import autocode_quota_route as quota_route
    from . import autocode_stop as stop
except ImportError:
    import autocode_job_provider_retry as provider_retry
    import autocode_quota_route as quota_route
    import autocode_stop as stop

# Stops that AutoCode settles: the provider stops it retries once, and a denial a retry would repeat.
SETTLED_KINDS = provider_retry.KINDS | {"external_directory"}
# What a saved checkpoint shows until its next resume retries the job or records the run's end.
NOTICE = "AutoCode will retry the stopped job itself or record why it cannot."


def investigation_owned(state: RunState) -> bool:
    """The failed job is an investigation's own (the Investigator or the repair Resolver).

    Its failure is the investigation's to settle: it restores the original pause, never a worse one
    (autocode_stuck_job.abandon, autocode_resolve_repair_job.abandon), so it never ends the run here.
    """
    stage = (state.get("job_failure") or {}).get("stage")
    return bool(
        (stage == "investigate_stuck" and state.get("stuck_investigation"))
        or (stage == "resolve_repair" and state.get("resolve_repair_request"))
    )


def end_cause(state: RunState) -> str | None:
    """Why a paused failed job can go no further automatically, or None while it can or a person decides.

    An attempt with no saved original source identity ends, whatever stopped it: no retry of it can
    be checked. Otherwise a person's own stop or abandonment, a model question (quota, refusal and
    the other route stops) and a source the person changed after the attempt stay with the person.
    Every other stopped job either still has its one automatic retry (``provider_retry.plan``) or ends.
    """
    failure = state.get("job_failure") or {}
    kind = failure.get("kind")
    if stop.applied_stop(state) is not None or investigation_owned(state) or not quota_route.job_pause_current(state):
        return None
    if not failure.get("source_identity"):
        # Without the original source identity no exact retry can be checked, whoever stopped the job
        # and whatever model question it carries, so nothing is left for a person to decide.
        return "no_source_identity"
    if state.get("status") != "PAUSED_JOB_FAILURE" or kind == "person":
        return None
    if failure.get("route") or failure.get("unrestored"):
        return None
    if kind == "external_directory":
        return "external_directory"
    if kind in provider_retry.KINDS and provider_retry.plan(state, failure, abandoned=False) is None:
        return "retries_spent"
    return None


def pending(state: RunState) -> bool:
    """A saved failed-job pause the next plain resume settles: it retries once or ends. Nothing for a person."""
    failure = state.get("job_failure") or {}
    if end_cause(state):
        return True
    return bool(
        state.get("status") == "PAUSED_JOB_FAILURE"
        and not investigation_owned(state)
        and stop.applied_stop(state) is None
        and quota_route.job_pause_current(state)
        and not failure.get("route")
        and provider_retry.plan(state, failure, abandoned=False) is not None
    )
