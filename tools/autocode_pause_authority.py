"""Which pause holds a run, and whether a budget flag is that pause's own authority.

A protected operational pause is released only by the operator's authority for that pause
(#379, #486): an explicit change to the bound it exhausted, or a recovery action that checks its
request itself (--grant-recovery, --retry-failed-stage, --abandon-stage, a route answer, a
resolver response). A budget flag for any other bound is a settings write, not that authority,
and neither is a pause intervention (autocode_stop records the pause it interrupted).

held_origin() names the pause from the run's own records: its live or queued operational
request, the request its answer consumed, else its saved status. changes_held_bound() says
whether explicit budget flags change that pause's bound.

Domain layer: it reads saved state only and imports nothing but autocode_goals (the request
keys); never the runner, autopilot or a module in the import cycle.
"""
from __future__ import annotations

try:
    from .autocode_goals import RESOLVER_PROPOSAL_KEY, RESOLVER_REQUEST_KEY
except ImportError:
    from autocode_goals import RESOLVER_PROPOSAL_KEY, RESOLVER_REQUEST_KEY

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


def _request_origin(state, request_id):
    entry = ((state.get('resolver') or {}).get('human_escalations') or {}).get(request_id) or {}
    return ((entry.get('identity') or {}).get('proposal') or {}).get('origin') or {}


def held_origin(state):
    """The origin of the pause holding the run: its pause_status and, for a budget, the bound's kind."""
    queued = state.get(RESOLVER_PROPOSAL_KEY) or {}
    if queued.get('scope') == 'operational_exhaustion' and queued.get('origin'):
        return queued['origin']
    published = state.get(RESOLVER_REQUEST_KEY) or {}
    if published.get('scope') == 'operational_exhaustion':
        origin = _request_origin(state, published.get('request_id'))
        if origin:
            return origin
    status = state.get('status')
    answered = (state.get('resolver') or {}).get('human_response_frontier') or {}
    if answered.get('pause_status') == status:
        origin = _request_origin(state, answered.get('request_id'))
        if origin.get('pause_status') == status:
            return origin
    return {'pause_status': status}


def changes_held_bound(explicit_flags, origin):
    """Whether ``explicit_flags`` (the budget flags typed on this command) change the bound ``origin`` exhausted."""
    kind = (origin.get('budget') or {}).get('kind') or PAUSE_BUDGET_KIND.get(origin.get('pause_status'))
    return any(flag in (explicit_flags or ()) for flag in BUDGET_FLAGS.get(kind, ()))


def held_pause(state, *, own_status):
    """The pause already holding the run, for a pause intervention landing on it; None when it was running.

    ``own_status`` is the intervention's own pause status, which interrupts nothing. A run waiting on
    an operational request is held at that request's pause, not at the request.
    """
    status = str(state.get('status') or '')
    if status in ('WAITING_FOR_USER', 'RESOLVER_PENDING'):
        status = str(held_origin(state).get('pause_status') or '')
    if not status.startswith('PAUSED_') or status == own_status:
        return None
    return {'status': status, 'stop_reason': state.get('stop_reason')}
