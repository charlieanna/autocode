"""Which pause holds a run, and whether a budget flag is that pause's own authority.

A protected operational pause is released only by the operator's authority for that pause
(#379, #486): an explicit change to the bound it exhausted, or a recovery action that checks its
request itself (--grant-recovery, --retry-failed-stage, --abandon-stage, a route answer, a
resolver response). A budget flag for any other bound is a settings write, not that authority,
and neither is a pause intervention, queued feedback, a requested pause or enabling joint
planning (autocode_stop records the pause an intervention interrupted). A correction (brief
feedback, an edited goal, a design reference revision) is a pause's own authority only where
that pause offers feedback (feedback_acknowledges): the exhausted plan-review budget, and the
validation-only stop, whose request names --feedback. Elsewhere it would put a human gate in
place of the pause, which an answer or approval with --resume-paused then dispatches past at
once (#509).

held_origin() names the pause from the run's own records: its live or queued operational
request, the request its answer consumed, else its saved status. interrupted() is the pause an
unresumed pause intervention interrupted. held_cause() is the saved stop reason without the advice
an operational request appended to it. changes_held_bound() says whether explicit budget flags
change that pause's bound. operational() says whether a pause is one AutoResolver asks an
operational request for (OPERATIONAL_PAUSES); correction_refusal() and feedback_refusal() say
why a correction may not restart planning past the pause holding the run.

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
# The status a pause intervention applies (autocode_stop.STOP_STATUS; that module imports this one).
INTERVENTION_STATUS = 'PAUSED_INTERVENTION'
# The operational pauses whose own authority includes brief feedback: an exhausted plan-review
# budget is answered with plan feedback or a new review-call limit (docs/task-run.md). A request
# can offer it too (feedback_acknowledges).
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


def _entry_proposal(entry):
    return ((entry or {}).get('identity') or {}).get('proposal') or {}


def _proposal(state, request_id):
    return _entry_proposal(((state.get('resolver') or {}).get('human_escalations') or {}).get(request_id))


def _held_proposal(state):
    """The operational proposal behind the pause holding the run: live, queued or consumed; else {}."""
    queued = state.get(RESOLVER_PROPOSAL_KEY) or {}
    if queued.get('scope') == 'operational_exhaustion' and queued.get('origin'):
        return queued
    published = state.get(RESOLVER_REQUEST_KEY) or {}
    if published.get('scope') == 'operational_exhaustion':
        proposal = _proposal(state, published.get('request_id'))
        if proposal.get('origin'):
            return proposal
    status = state.get('status')
    answered = (state.get('resolver') or {}).get('human_response_frontier') or {}
    if answered.get('pause_status') == status:
        proposal = _proposal(state, answered.get('request_id'))
        if (proposal.get('origin') or {}).get('pause_status') == status:
            return proposal
    return {}


def held_origin(state):
    """The origin of the pause holding the run: its pause_status and, for a budget, the bound's kind."""
    return _held_proposal(state).get('origin') or {'pause_status': state.get('status')}


def interrupted(state):
    """The pause an applied pause intervention interrupted, until --resume-paused acknowledges it; else None.

    autocode_stop records it as ``pause_intent.held_pause``; a later intervention keeps it.
    """
    intent = state.get('pause_intent') or {}
    held = intent.get('held_pause') or {}
    if state.get('status') == INTERVENTION_STATUS and not intent.get('acknowledged_at') and held.get('status'):
        return dict(held)
    return None


def feedback_acknowledges(state, pause_status):
    """Whether brief feedback is ``pause_status``'s own authority.

    It is for the exhausted plan-review budget, and where the request asked for that pause offers
    it: the validation-only stop (autocode_validation_rounds) names --feedback as the way to
    continue, and it alone carries ``finding_ids``. The request is the one holding the run, else
    the latest asked for that pause (a withdrawn one, while queued input is applied under it).
    """
    if pause_status in FEEDBACK_ACKNOWLEDGES:
        return True
    proposal = _held_proposal(state)
    if (proposal.get('origin') or {}).get('pause_status') != pause_status:
        asked = [entry for entry in ((state.get('resolver') or {}).get('human_escalations') or {}).values()
                 if isinstance(entry, dict)
                 and (_entry_proposal(entry).get('origin') or {}).get('pause_status') == pause_status
                 and _entry_proposal(entry).get('scope') == 'operational_exhaustion']
        latest = max(asked, key=lambda entry: str(entry.get('issued_at') or ''), default={})
        proposal = _entry_proposal(latest)
    return bool((proposal.get('request') or {}).get('finding_ids'))


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
    proposals = [state.get(RESOLVER_PROPOSAL_KEY) or {}, *(_entry_proposal(entry) for entry in entries)]
    causes = [str(cause).strip() for proposal in proposals
              if proposal.get('scope') == 'operational_exhaustion'
              and (proposal.get('origin') or {}).get('pause_status') == pause_status
              and (cause := (proposal.get('request') or {}).get('discovered'))
              and str(cause).strip() and reason.startswith(str(cause).strip())]
    return max(causes, key=len) if causes else reason


def operational(status):
    """Whether ``status`` is a pause only its own operational authority releases."""
    return status in OPERATIONAL_PAUSES


def correction_refusal(state, correction):
    """Why ``correction`` (feedback, an edited goal, a design revision) may not restart planning past the held pause.

    None when it may. A correction asks for a fresh approval in place of the pause, and an approval
    with --resume-paused dispatches the Planner at once (#509), so it is refused at an operational
    pause that does not offer feedback, including one a pause intervention interrupted.
    """
    pause = (interrupted(state) or {}).get('status') or held_origin(state).get('pause_status')
    if operational(pause) and not feedback_acknowledges(state, pause):
        return (f'{correction} does not acknowledge {pause}; resolve that pause first '
                '(AutoResolver\'s request names how).')
    return None


def feedback_refusal(state):
    """Why brief feedback may not restart planning past the pause holding ``state``; None when it may."""
    refusal = correction_refusal(state, 'Brief feedback')
    return refusal and refusal + ' Queued feedback is applied under the pause.'


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
    # feedback: whether queued feedback is that pause's own authority (read by autocode_stop).
    return {'status': status, 'stop_reason': held_cause(state, status),
            'feedback': feedback_acknowledges(state, status)}
