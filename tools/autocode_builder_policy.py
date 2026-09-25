"""Runner-owned retry decisions. Models diagnose; configuration selects routes."""
import copy
try:
    from . import autocode_support as s
except ImportError:
    import autocode_support as s

DEFAULTS = {'enabled': True, 'ordinary_retries': 1, 'strong_model': 'gpt-6-sol',
            'strong_reasoning_effort': 'high'}


def enabled(state):
    return (state.get('settings', {}).get('builder_retry', {}).get('enabled') is True
            and not state.get('settings', {}).get('workflow'))


def key(state):
    task = state.get('current_task') or {}
    members = task.get('milestone_ids') or [task.get('milestone_id') or task.get('id')]
    return s.digest([state.get('goal_contract', {}).get('hash'), sorted(members)])


def lane(state):
    ident = key(state)
    lanes = state.setdefault('builder_retries', {})
    previous = lanes.get(state.get('builder_retry_key'))
    if previous and state.get('builder_retry_key') != ident:
        state['settings']['roles']['terra'] = copy.deepcopy(previous['initial_route'])
        state.setdefault('sessions', {}).pop('terra', None)
    state['builder_retry_key'] = ident
    return lanes.setdefault(ident, {'initial_route': copy.deepcopy(state['settings']['roles']['terra']),
                                   'failures': [], 'action': None})


def guard(state):
    if not enabled(state):
        return
    current = lane(state)
    if current['action'] == 'pause':
        raise s.Paused('PAUSED_BUILDER_RETRY_LIMIT',
                       'Builder retry/escalation exhausted for this approved milestone; replan or change policy explicitly')


def failure(state, evidence, reason):
    """One persisted resolver decision per failure; restart cannot add authority."""
    if not enabled(state):
        state.update(status='PAUSED_NO_PROGRESS', phase='PAUSED_OR_BLOCKED', stop_reason=reason)
        return 'pause'
    config = state['settings']['builder_retry']
    count = config['ordinary_retries']
    if type(count) is not int or not 0 <= count <= 3:
        raise ValueError('builder_retry.ordinary_retries must be between 0 and 3')
    current = lane(state)
    if evidence in current['failures']:
        return current['action']
    current['failures'].append(evidence)
    route = state['settings']['roles']['terra']
    n = len(current['failures'])
    action = 'retry' if n <= count else 'escalate' if n == count + 1 else 'pause'
    if action == 'escalate':
        # Never undo explicit pins or silently change provider/transport.
        if route.get('model_pinned') or route.get('provider') not in (None, 'openai'):
            action = 'pause'
        else:
            model = config['strong_model']
            if route.get('engine', state['settings'].get('engine')) == 'opencode' and '/' not in model:
                model = 'openai/' + model
            route.update(model=model, reasoning_effort=config['strong_reasoning_effort'])
    current['action'] = action
    state.setdefault('sessions', {}).pop('terra', None)
    state.setdefault('builder_retry_decisions', []).append({
        'at': s.now(), 'owner': 'autoresolver', 'action': action, 'failure': evidence,
        'reason': reason, 'milestone_key': key(state), 'attempt': n,
        'selected_model': route['model'], 'selected_effort': route.get('reasoning_effort')})
    if action == 'pause':
        state.update(status='PAUSED_BUILDER_RETRY_LIMIT', phase='PAUSED_OR_BLOCKED',
                     stop_reason='Implementation remains blocked after configured retry/escalation; human decision or replanning required')
    return action
