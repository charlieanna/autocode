"""Name another model for a workflow job its provider refused or ran out of quota on (#463).

A content-filter refusal (PAUSED_CONTENT_FILTER) or a quota stop (PAUSED_BUDGET) in a
workflow job (autocode_jobs.STAGES) pauses under autocode_job_failure with one exact retry,
bound to the run's configuration, and keeps the job's model question (``route``) on that
failure instead of publishing it. This module answers that question:

    --answer route-ROLE=MODEL --job-retry-token - (reads AUTOCODE_JOB_RETRY_TOKEN)

``MODEL@LEVEL`` also names the reasoning effort, which the model must declare (#1902): at a time-limit
stop the question offers the same model at its next lower declared variant first.

The token is the one the stop shows, so the answer applies only to the failure a person
inspected. The model must pass the rules a launch applies (engine format, OpenCode
availability, cross-model; never the model that was just refused or the one already set).
Applying it is the usual recorded ``route_assignment`` (autocode_quota_route.assign); then
autocode_job_failure.reroute binds the exact retry to the new configuration under a new token.
Nothing launches: the person retries with --resume-paused --retry-failed-stage and the new token.
The answer changes nothing else: settings_refusal (called by autocode_run_setup before it saves
an invocation's settings) refuses any model answer at a job stop that also changes the bound
configuration, and autocode_args refuses the answer next to the retry it cannot make, so a
rejected answer leaves the saved run as it was.

It is given the runner module (autocode) and uses the runner's own services as runner.X, the
way autocode_run_actions does; it never imports autocode, autopilot or a cycle member.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import copy
import sys
from pathlib import Path

try:
    from . import autocode_job_failure as job_failure
    from . import autocode_quota_route as quota_route
    from . import autocode_role_transport as role_transport
    from . import autocode_roles as roles
    from . import autocode_stuck_job as stuck_job
except ImportError:
    import autocode_job_failure as job_failure
    import autocode_quota_route as quota_route
    import autocode_role_transport as role_transport
    import autocode_roles as roles
    import autocode_stuck_job as stuck_job

_RETRY = "--resume-paused --retry-failed-stage --job-retry-token - (reads AUTOCODE_JOB_RETRY_TOKEN)"
# A job_failure saved before #463 has the stop's kind but no pause_status (autocode_job_failure._reason).
_CAUSE = {
    "content_filter": quota_route.REFUSAL_STATUS,
    "quota": quota_route.QUOTA_STATUS,
    "output_limit": quota_route.OUTPUT_STATUS,
    "context_window": quota_route.CONTEXT_STATUS,
}


def _routes(args) -> bool:
    return any(item.partition("=")[0].startswith(quota_route.PREFIX) for item in [*args.answer, *args.delegate])


def _answering(args, state: RunState) -> bool:
    """Whether answer() handles this invocation: any route answer or delegation at a stopped job.

    Every such form counts, with or without a token, so no form answer() refuses or applies can
    save other settings first (the parser refuses the answer next to --resume-paused/--retry-failed-stage).
    """
    failure = state.get("job_failure") or {}
    return bool(failure) and quota_route.job_pause_current(state) and _routes(args)


def settings_refusal(args, state: RunState, selected) -> str | None:
    """Why this invocation's settings cannot be saved with a stopped job's model answer, or None.

    The answer changes only the job's model and rebinds its exact retry. Any other configuration
    change in the same invocation (a limit, another role's model) would be saved before the answer
    is checked and leave the retry stale, so the run setup refuses it before writing anything,
    whatever else the answer gets wrong.
    """
    current = job_failure.configuration(state)
    chosen = job_failure.configuration({**state, "settings": selected})
    failure = state.get("job_failure") or {}
    if (
        quota_route.job_pause_current(state)
        and failure.get("stage") == stuck_job.STAGE
        and (getattr(args, "investigator_model", None) or getattr(args, "investigator_reasoning_effort", None))
        and chosen != current
        and chosen != failure.get("configuration")
    ):
        return (
            "The stopped Investigator's preparation pin is bound to its exact retry; nothing is saved. "
            "Answer without other settings flags: --answer route-stuck_investigator=MODEL "
            "--job-retry-token - (reads AUTOCODE_JOB_RETRY_TOKEN), then retry with the new token."
        )
    answering = _answering(args, state)
    if (
        quota_route.job_pause_current(state)
        and not answering
        and chosen != current
        and chosen != failure.get("configuration")
    ):
        return (
            "Provider configuration or limits at a stopped job are bound to its exact retry; nothing is saved. "
            "Use the token-bound model answer to change only the failed role, or restore the captured configuration."
        )
    if not answering:
        return None
    if current == chosen:
        return None
    return (
        "A stopped job's model answer changes only that job's model; this invocation also changes the "
        "configuration its exact retry is bound to, so nothing is saved. Answer without other settings "
        "flags: --answer route-ROLE=MODEL --job-retry-token - (reads AUTOCODE_JOB_RETRY_TOKEN)"
    )


def _cause(failure, state: RunState, classify) -> str | None:
    """The model stop that paused the job (quota_route.STATUSES), '' for another stop, None when unknown.

    A failure saved since #463 carries ``pause_status``. One saved before it has only its kind: a
    refusal kept the kind ``content_filter``, but a quota stop was saved as ``exit``, like any other
    provider exit. Such an ``exit`` is classified from its archived events with ``classify``
    (autocode_support.failure_status), the way the stop itself was; without them the cause is unknown.
    """
    cause = quota_route.job_stop_status(failure) or _CAUSE.get(failure.get("kind"))
    if cause:
        return cause
    if failure.get("kind") != "exit":
        return ""  # a timeout, lock, rate limit or capacity stop: classified when it stopped, not the model
    archive = failure.get("archive")
    events = next(
        (
            row.get("events")
            for row in reversed(state.get("stages") or [])
            if isinstance(row, dict)
            and row.get("stage") == failure.get("stage")
            and row.get("events")
            and archive
            and str(Path(row["events"]).parent) == archive
        ),
        None,
    )
    if not classify or not events or not Path(events).is_file():
        return None
    try:
        status = classify(events)
    except (OSError, ValueError, TypeError):
        return None
    return status if status in quota_route.STATUSES else ""


def _no_route(failure, state: RunState, classify=None) -> str:
    """Why the stopped job takes no other model, naming its real cause (#463 review)."""
    job = roles.screen_name(failure.get("stage"), state)
    cause = _cause(failure, state, classify)
    if cause is None:
        return (
            f"The stopped {job} takes no other model: only a job stopped on quota, a content-filter "
            f"refusal, output limit, context window, provider authentication or a stopped-job timeout does, and this stop saved no model question; inspect its saved reason and retry it "
            f"with {_RETRY}"
        )
    if cause not in quota_route.JOB_STATUSES:
        return (
            f"The stopped {job} did not stop on quota, a content-filter refusal, output limit, context window, provider authentication or a stopped-job timeout, so it takes no other "
            f"model; inspect it and retry it with {_RETRY}"
        )
    stopped = (
        "was refused by its provider's content filter"
        if cause == quota_route.REFUSAL_STATUS
        else "exhausted its response output limit"
        if cause == quota_route.OUTPUT_STATUS
        else "ran out of its model's context window"
        if cause == quota_route.CONTEXT_STATUS
        else "failed provider authentication"
        if cause == quota_route.AUTH_STATUS
        else "exceeded its execution time limit"
        if cause == quota_route.TIMEOUT_STATUS
        else "stopped on quota"
    )
    if "pause_status" not in failure:
        why = "this stop was saved before a stopped job could take another model"
    elif failure.get("stage") == stuck_job.STAGE:
        why = "its saved source, launch configuration and archived attempt do not establish a bound model question"
    else:
        why = "this run has no route for it that a person can name"
    retry = (
        "once the quota resets, retry it unchanged with "
        if cause == quota_route.QUOTA_STATUS
        else "only its exact retry applies, on the same model, which may exhaust its output limit again: "
        if cause == quota_route.OUTPUT_STATUS
        else "only its exact retry applies, on the same model, whose context window the same request may exceed again: "
        if cause == quota_route.CONTEXT_STATUS
        else "after repairing provider authentication, retry it unchanged with "
        if cause == quota_route.AUTH_STATUS
        else "only its exact retry applies, on the same model, which may time out again: "
        if cause == quota_route.TIMEOUT_STATUS
        else "only its exact retry applies, on the same model, which is likely to refuse it again: "
    )
    return f"The stopped {job} {stopped}, but {why}, so it takes no other model; {retry}{_RETRY}"


def answer(runner, args, state: RunState, run_dir, workspace):
    """Exit code when this invocation names a stopped job's model, None when it asks something else.

    A rejection prints ``Input rejected: ...`` and leaves the saved run unchanged.
    """
    failure = state.get("job_failure") or {}
    at_job = bool(failure) and quota_route.job_pause_current(state)
    if not (args.answer or args.delegate) or not _routes(args):
        return None
    if not at_job and not args.job_retry_token:
        return None  # a build stage's model question (autocode_action_resolver.answer_quota_question)
    candidate = copy.deepcopy(state)
    try:
        if not at_job:
            raise ValueError("--job-retry-token names a stopped workflow job; this run has none")
        route = quota_route.job_question(state)
        if not route:
            raise ValueError(
                _no_route(failure, state, getattr(getattr(runner, "support", None), "failure_status", None))
            )
        if not args.job_retry_token:
            raise ValueError(
                f"Name the model with the job retry token the stop shows: "
                f"--answer {route['id']}=MODEL --job-retry-token - (reads AUTOCODE_JOB_RETRY_TOKEN)"
            )
        if args.job_retry_token != failure.get("job_retry_token"):
            raise ValueError("Job retry token does not match the current failed attempt")
        if args.delegate or args.delegate_all:
            raise ValueError("A model question has no default to delegate; name the model yourself")
        candidate["job_failure"]["route"] = copy.deepcopy(route)
        candidate["job_failure"]["pause_status"] = quota_route.job_stop_status(failure)
        asked, model = quota_route.parse_answer(
            args.answer, [route], {"pause_status": quota_route.job_stop_status(failure)}
        )
        role = asked["route_role"]
        # MODEL@LEVEL also names the effort (#1902): the same model at a lighter variant is a change.
        model, effort = quota_route.split_effort(model)
        bound = ((failure.get("configuration") or {}).get("roles") or {}).get(role) or {}
        if effort == bound.get("reasoning_effort"):
            effort = None
        if failure.get("pause_status") in (
            quota_route.REFUSAL_STATUS,
            quota_route.OUTPUT_STATUS,
            quota_route.CONTEXT_STATUS,
        ) and model == route.get("stopped_model"):
            if failure["pause_status"] == quota_route.REFUSAL_STATUS:
                raise ValueError(f"The {asked['job']}'s provider refused {model}; name another model")
            if failure["pause_status"] == quota_route.CONTEXT_STATUS:
                raise ValueError(
                    f"The {asked['job']}'s request did not fit {model}'s context window; name a model with a larger one"
                )
            raise ValueError(f"The {asked['job']} exhausted its output limit on {model}; name another model")
        quota_route.validate(
            candidate,
            role,
            model,
            configured_tool=getattr(runner.opencode, "CONFIGURED", False),
            cross_check=runner.dispatch.enforce_cross_model_verification,
            job=asked.get("job"),
            allow_unchanged=effort is not None or quota_route.job_stop_status(failure) == quota_route.TIMEOUT_STATUS,
        )
        if effort is not None:
            quota_route.check_effort(candidate, role, model, effort, job=asked.get("job"))
        if quota_route.engine(candidate["settings"], role) == "opencode":
            try:
                facade = role_transport.adapter(candidate["settings"], role, runner.opencode, workspace)
                facade.check_models({role: {"model": model}}, workspace)
            except RuntimeError as error:
                raise ValueError(str(error)) from None
        if runner.interventions.pending(run_dir):
            raise ValueError("Apply the queued intervention before answering")
        attempt = quota_route.stopped_attempt(candidate, failure_status=runner.support.failure_status)
        if not attempt or attempt["kind"] != "job" or attempt["role"] != role:
            raise ValueError("The stopped job attempt is no longer current; run with --status to see it")
        record = quota_route.assign(
            candidate, role, model, at=runner.now(), via="answer", attempt=attempt, effort=effort
        )
        token = job_failure.reroute(runner, candidate, run_dir, workspace, record)
    except (ValueError, KeyError) as error:
        print(f"Input rejected: {error}", file=sys.stderr)
        return 2
    runner.commit_user_action(state, candidate, run_dir)
    named = record.get("effort") or {}
    effort_note = f" at {named['to']} effort (was {named['from']})" if named.get("named") else ""
    print(
        f"{state['status']}: The {record['job']} now runs on {model}{effort_note} (was {record['from']}). Retry it with "
        f"--resume-paused --retry-failed-stage --job-retry-token {token}. Saved; no agent launched."
    )
    return 0
