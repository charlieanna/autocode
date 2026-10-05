"""Name another model for a workflow job its provider refused or ran out of quota on (#463).

A content-filter refusal (PAUSED_CONTENT_FILTER) or a quota stop (PAUSED_BUDGET) in a
workflow job (autocode_jobs.STAGES) pauses under autocode_job_failure with one exact retry,
bound to the run's configuration, and keeps the job's model question (``route``) on that
failure instead of publishing it. This module answers that question:

    --answer route-ROLE=MODEL --job-retry-token TOKEN

The token is the one the stop shows, so the answer applies only to the failure a person
inspected. The model must pass the rules a launch applies (engine format, OpenCode
availability, cross-model; never the model that was just refused or the one already set).
Applying it is the usual recorded ``route_assignment`` (autocode_quota_route.assign); then
autocode_job_failure.reroute binds the exact retry to the new configuration under a new token.
Nothing launches: the person retries with --resume-paused --retry-failed-stage and the new token.
The answer changes nothing else: settings_refusal (called by autocode_run_setup before it saves
an invocation's settings) refuses an answer that also changes the bound configuration, so a
rejected answer leaves the saved run as it was.

It is given the runner module (autocode) and uses the runner's own services as runner.X, the
way autocode_run_actions does; it never imports autocode, autopilot or a cycle member.
"""
from __future__ import annotations

import copy
import sys

try:
    from . import autocode_job_failure as job_failure, autocode_quota_route as quota_route, autocode_roles as roles
    from . import autocode_stuck_job as stuck_job
except ImportError:
    import autocode_job_failure as job_failure
    import autocode_quota_route as quota_route
    import autocode_roles as roles
    import autocode_stuck_job as stuck_job

_RETRY = '--resume-paused --retry-failed-stage --job-retry-token TOKEN'
# A job_failure saved before #463 has the stop's kind but no pause_status (autocode_job_failure._reason).
_CAUSE = {'content_filter': quota_route.REFUSAL_STATUS, 'quota': quota_route.QUOTA_STATUS}


def _routes(args) -> bool:
    return any(item.partition('=')[0].startswith(quota_route.PREFIX) for item in [*args.answer, *args.delegate])


def _answering(args, state) -> bool:
    failure = state.get('job_failure') or {}
    return (bool(failure) and state.get('status') in job_failure.PAUSES and bool(args.job_retry_token)
            and bool(args.answer or args.delegate) and _routes(args) and not args.resume_paused)


def settings_refusal(args, state, selected) -> str | None:
    """Why this invocation's settings cannot be saved with a stopped job's model answer, or None.

    The answer changes only the job's model and rebinds its exact retry. Any other configuration
    change in the same invocation (a limit, another role's model) would be saved before the answer
    is checked and leave the retry stale, so the run setup refuses it before writing anything.
    """
    if not _answering(args, state):
        return None
    if job_failure.configuration(state) == job_failure.configuration({'settings': selected}):
        return None
    return ("A stopped job's model answer changes only that job's model; this invocation also changes the "
            "configuration its exact retry is bound to, so nothing is saved. Answer without other settings "
            "flags: --answer route-ROLE=MODEL --job-retry-token TOKEN")


def _no_route(failure, state) -> str:
    """Why the stopped job takes no other model, naming its real cause (#463 review)."""
    job = roles.screen_name(failure.get('stage'), state)
    cause = failure.get('pause_status') or _CAUSE.get(failure.get('kind'))
    if cause not in quota_route.STATUSES:
        return (f"The stopped {job} did not stop on quota or a content-filter refusal, so it takes no other "
                f"model; inspect it and retry it with {_RETRY}")
    stopped = ("was refused by its provider's content filter" if cause == quota_route.REFUSAL_STATUS
               else "stopped on quota")
    if 'pause_status' not in failure:
        why = "this stop was saved before a stopped job could take another model"
    elif failure.get('stage') == stuck_job.STAGE:
        why = ("the stuck-stage Investigator's route is rebuilt for every investigation, so a model named "
               "for it would not stick")
    else:
        why = "this run has no route for it that a person can name"
    retry = ('once the quota resets, retry it unchanged with ' if cause == quota_route.QUOTA_STATUS
             else 'only its exact retry applies, on the same model, which is likely to refuse it again: ')
    return f"The stopped {job} {stopped}, but {why}, so it takes no other model; {retry}{_RETRY}"


def answer(runner, args, state, run_dir, workspace):
    """Exit code when this invocation names a stopped job's model, None when it asks something else.

    A rejection prints ``Input rejected: ...`` and leaves the saved run unchanged.
    """
    failure = state.get('job_failure') or {}
    at_job = bool(failure) and state.get('status') in job_failure.PAUSES
    if not (args.answer or args.delegate) or not _routes(args):
        return None
    if not at_job and not args.job_retry_token:
        return None  # a build stage's model question (autocode_run_actions.answer_quota_question)
    candidate = copy.deepcopy(state)
    try:
        if not at_job:
            raise ValueError('--job-retry-token names a stopped workflow job; this run has none')
        route = failure.get('route')
        if not route:
            raise ValueError(_no_route(failure, state))
        if not args.job_retry_token:
            raise ValueError(f"Name the model with the job retry token the stop shows: "
                             f"--answer {route['id']}=MODEL --job-retry-token TOKEN")
        if args.job_retry_token != failure.get('job_retry_token'):
            raise ValueError('Job retry token does not match the current failed attempt')
        if args.delegate or args.delegate_all:
            raise ValueError('A model question has no default to delegate; name the model yourself')
        asked, model = quota_route.parse_answer(args.answer, [route], {'pause_status': failure.get('pause_status')})
        role = asked['route_role']
        if failure.get('pause_status') == quota_route.REFUSAL_STATUS and model == route.get('stopped_model'):
            raise ValueError(f"The {asked['job']}'s provider refused {model}; name another model")
        quota_route.validate(candidate, role, model, configured_tool=getattr(runner.opencode, 'CONFIGURED', False),
                             cross_check=runner.dispatch.enforce_cross_model_verification, job=asked.get('job'))
        if quota_route.engine(candidate['settings'], role) == 'opencode':
            try:
                runner.opencode.check_models({role: {'model': model}}, workspace)
            except RuntimeError as error:
                raise ValueError(str(error)) from None
        if runner.interventions.pending(run_dir):
            raise ValueError('Apply the queued intervention before answering')
        attempt = quota_route.stopped_attempt(candidate, failure_status=runner.support.failure_status)
        if not attempt or attempt['kind'] != 'job' or attempt['role'] != role:
            raise ValueError('The stopped job attempt is no longer current; run with --status to see it')
        record = quota_route.assign(candidate, role, model, at=runner.now(), via='answer', attempt=attempt)
        token = job_failure.reroute(runner, candidate, run_dir, workspace, record)
    except (ValueError, KeyError) as error:
        print(f'Input rejected: {error}', file=sys.stderr)
        return 2
    runner.commit_user_action(state, candidate, run_dir)
    print(f"{state['status']}: The {record['job']} now runs on {model} (was {record['from']}). Retry it with "
          f"--resume-paused --retry-failed-stage --job-retry-token {token}. Saved; no agent launched.")
    return 0
