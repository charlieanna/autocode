"""Which pause holds a run, and whether a budget flag is that pause's own authority.

A protected operational pause is released only by the operator's authority for that pause
(#379, #486): an explicit change to the bound it exhausted, or a recovery action that checks its
request itself (--grant-recovery, --retry-failed-stage, --abandon-stage, a route answer, a
resolver response). A budget flag for any other bound is a settings write, not that authority,
and neither is a pause intervention, queued feedback, a requested pause or enabling joint
planning (autocode_stop records the pause an intervention interrupted). Plan feedback is the
planning budget's own authority (FEEDBACK_ACKNOWLEDGES).

held_origin() names the pause from the run's own records: its live or queued operational
request, the request its answer consumed, else its saved status. held_cause() is the saved stop
reason without the advice an operational request appended to it. changes_held_bound() says
whether explicit budget flags change that pause's bound. operational() says whether a pause is
one AutoResolver asks an operational request for (OPERATIONAL_PAUSES); feedback_refusal() says
why brief feedback may not restart planning past the pause holding the run.

Domain layer: it reads saved state only and imports nothing but autocode_goals (the request
keys) and autocode_provider_refusal (its status name); never the runner, autopilot or a module
in the import cycle.
"""
from __future__ import annotations

try:
    from . import autocode_provider_refusal as provider_refusal
    from .autocode_goals import RESOLVER_PROPOSAL_KEY, RESOLVER_REQUEST_KEY
except ImportError:
    import autocode_provider_refusal as provider_refusal
    from autocode_goals import RESOLVER_PROPOSAL_KEY, RESOLVER_REQUEST_KEY

# Every pause AutoResolver publishes an operational_exhaustion request for
# (autocode_resolver_runtime.record_operational_exhaustion asks for these only).
OPERATIONAL_PAUSES = (
    'PAUSED_RESOLVER_OPERATIONAL', 'PAUSED_TIMEOUT_RECOVERY', 'PAUSED_PROVIDER_CAPACITY',
    'PAUSED_PLANNING_BUDGET', 'PAUSED_RATE_LIMIT', 'PAUSED_TIME_LIMIT', 'PAUSED_ITERATION_LIMIT',
    'PAUSED_BUDGET', 'PAUSED_REPORT_REPAIR_LIMIT', 'PAUSED_REPEATED_FAILURE', 'PAUSED_RESOLVER',
    'PAUSED_ORCHESTRATOR_WORKER', 'PAUSED_BUILDER_RETRY_LIMIT', 'PAUSED_MILESTONE_STALLED',
    'PAUSED_MILESTONE_BUDGET', 'PAUSED_MILESTONE_TIME_LIMIT', 'PAUSED_PROVIDER_UNCERTAIN',
    'PAUSED_UNCERTAIN_STAGE', 'PAUSED_WORKSPACE_BUSY', 'PAUSED_NO_PROGRESS', provider_refusal.STATUS)
# The operational pauses whose own authority includes brief feedback: an exhausted plan-review
# budget is answered with plan feedback or a new review-call limit (docs/task-run.md).
FEEDBACK_ACKNOWLEDGES = ('PAUSED_PLANNING_BUDGET',)

# The bound each budget pause exhausted, when its request does not name one.
PAUSE_BUDGET_KIND = {
    'PAUSED_TIME_LIMIT': 'max_seconds',
    'PAUSED_ITERATION_LIMIT': 'iteration_ceiling',
    'PAUSED_MILESTONE_TIME_LIMIT': 'milestone_max_seconds',
    'PAUSED_MILESTONE_BUDGET': 'milestone_max_seconds',
    'PAUSED_NO_PROGRESS': 'no_progress_batches',
}
# The explicit flags that change each exhaustible bound (a subset of autocode_configure.BUDGET_ARGUMENTS).
BUDGET_FLAGS = {
    'max_seconds': ('max_seconds',),
    'iteration_ceiling': ('max_iterations', 'legacy_iteration_ceiling', 'unlimited_iterations'),
    'milestone_max_seconds': ('max_milestone_seconds',),
    'no_progress_batches': ('no_progress_limit',),
}


def _proposal(state, request_id):
    entry = ((state.get('resolver') or {}).get('human_escalations') or {}).get(request_id) or {}
    return (entry.get('identity') or {}).get('proposal') or {}


def held_origin(state):
    """The origin of the pause holding the run: its pause_status and, for a budget, the bound's kind."""
    queued = state.get(RESOLVER_PROPOSAL_KEY) or {}
    if queued.get('scope') == 'operational_exhaustion' and queued.get('origin'):
        return queued['origin']
    published = state.get(RESOLVER_REQUEST_KEY) or {}
    if published.get('scope') == 'operational_exhaustion':
        origin = _proposal(state, published.get('request_id')).get('origin')
        if origin:
            return origin
    status = state.get('status')
    answered = (state.get('resolver') or {}).get('human_response_frontier') or {}
    if answered.get('pause_status') == status:
        origin = _proposal(state, answered.get('request_id')).get('origin') or {}
        if origin.get('pause_status') == status:
            return origin
    return {'pause_status': status}


def held_cause(state, pause_status):
    """The saved stop reason without the advice an operational request for ``pause_status`` appended.

    record_operational_exhaustion stops with the request's cause followed by its advice. Asking
    again from that whole reason would repeat the advice (#486 review), so a reason that starts with
    such a request's own cause yields that cause (the longest, the request that wrote the reason).
    """
    reason = state.get('stop_reason')
    if not isinstance(reason, str):
        return reason
    entries = ((state.get('resolver') or {}).get('human_escalations') or {}).values()
    proposals = [state.get(RESOLVER_PROPOSAL_KEY) or {},
                 *(((entry or {}).get('identity') or {}).get('proposal') or {} for entry in entries)]
    causes = [str(cause).strip() for proposal in proposals
              if proposal.get('scope') == 'operational_exhaustion'
              and (proposal.get('origin') or {}).get('pause_status') == pause_status
              and (cause := (proposal.get('request') or {}).get('discovered'))
              and str(cause).strip() and reason.startswith(str(cause).strip())]
    return max(causes, key=len) if causes else reason


def operational(status):
    """Whether ``status`` is a pause only its own operational authority releases."""
    return status in OPERATIONAL_PAUSES


def feedback_refusal(state):
    """Why brief feedback may not restart planning past the pause holding ``state``; None when it may."""
    pause = held_origin(state).get('pause_status')
    if operational(pause) and pause not in FEEDBACK_ACKNOWLEDGES:
        return (f'Brief feedback does not acknowledge {pause}; resolve that pause first '
                '(AutoResolver\'s request names how). Queued feedback is applied under the pause.')
    return None


def changes_held_bound(explicit_flags, origin):
    """Whether ``explicit_flags`` (the budget flags typed on this command) change the bound ``origin`` exhausted."""
    kind = (origin.get('budget') or {}).get('kind') or PAUSE_BUDGET_KIND.get(origin.get('pause_status'))
    return any(flag in (explicit_flags or ()) for flag in BUDGET_FLAGS.get(kind, ()))


def held_pause(state, *, own_status):
    """The pause already holding the run, for an intervention landing on it; None when it was running.

    ``own_status`` is the intervention's own pause status, which interrupts nothing. A run waiting on
    an operational request is held at that request's pause, not at the request.
    """
    status = str(state.get('status') or '')
    if status in ('WAITING_FOR_USER', 'RESOLVER_PENDING'):
        status = str(held_origin(state).get('pause_status') or '')
    if not status.startswith('PAUSED_') or status == own_status:
        return None
    return {'status': status, 'stop_reason': held_cause(state, status)}
