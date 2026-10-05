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

It is given the runner module (autocode) and uses the runner's own services as runner.X, the
way autocode_run_actions does; it never imports autocode, autopilot or a cycle member.
"""
from __future__ import annotations

import copy
import sys

try:
    from . import autocode_job_failure as job_failure, autocode_quota_route as quota_route, autocode_roles as roles
except ImportError:
    import autocode_job_failure as job_failure
    import autocode_quota_route as quota_route
    import autocode_roles as roles


def _routes(args) -> bool:
    return any(item.partition('=')[0].startswith(quota_route.PREFIX) for item in [*args.answer, *args.delegate])


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
            raise ValueError(f"The stopped {roles.screen_name(failure.get('stage'), state)} did not stop on "
                             "quota or a content-filter refusal, so it takes no other model; inspect it and "
                             "retry it with --resume-paused --retry-failed-stage --job-retry-token TOKEN")
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
