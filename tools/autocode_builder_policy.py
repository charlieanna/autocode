"""Runner-owned retry decisions. Models diagnose; configuration selects routes."""
import copy
try:
    from . import autocode_util as s
except ImportError:
    import autocode_util as s

# The stronger attempt is GPT-6 Sol at xhigh: Astra is too expensive and only for the
# Resolver (user 2026-09-28). The default checkers also run GPT-6 Sol, so for the rest of
# that milestone a checker on the strong model moves to checker_model and a different model
# checks the escalated work (user 2026-09-29).
DEFAULTS = {'enabled': True, 'ordinary_retries': 1, 'strong_model': 'openai/gpt-6-sol',
            'strong_reasoning_effort': 'xhigh', 'checker_model': 'zai-coding-plan/glm-5.3'}
# The roles that check the Builder's work (autocode_dispatch._VERIFIER_PAIRS).
CHECKERS = ('sol', 'completion')
# A parallel Builder cannot move the parent's checkers, so when they run its stronger model
# it stops with this result. The parent integrates the other Builders and makes the stronger
# attempt itself, serially, where swap_checkers applies (user 2026-09-29).
SERIAL = 'SERIAL_ESCALATION'
# Efforts a replacement checker keeps; a climbed xhigh/max rung belongs to the GPT ladder.
CHECKER_EFFORTS = ('low', 'medium', 'high')
EXHAUSTED = 'Implementation remains blocked after configured retry/escalation; human decision or replanning required'
CHECKER_STUCK = ('Implementation remains blocked after the ordinary retries; the stronger Builder model {model} '
                 'would be checked by its own model: {why}; human decision or replanning required')
NO_STRONG_MODEL = ("Implementation remains blocked after the ordinary retries; this run's provider offers no stronger "
                   'Builder model (name one, and the model its checkers move to, under [builder_retry] in the provider '
                   'config); human decision or replanning required')


def _bare(model):
    return model.split('/', 1)[1] if isinstance(model, str) and model.startswith('openai/') else model


def configured(strong_model=None, provider=None):
    """A new run's retry policy. Its checkers move to checker_model, so the strong model cannot be it.

    A configured tool may name its own stronger Builder and checker model ([builder_retry] in its
    config); every other provider uses DEFAULTS. A provider config that lists its models without
    the strong one gets none: a live Claude-provider run escalated its Builder to the OpenAI
    default, which that provider cannot serve, so the launch failed and the run waited on a
    person (2026-09-29).
    """
    declared = getattr(provider, 'BUILDER_RETRY', None) or {}
    config = {**DEFAULTS, **{key: declared[name] for key, name in (
        ('strong_model', 'strong_model'), ('checker_model', 'checker_model'),
        ('strong_reasoning_effort', 'strong_effort')) if name in declared}}
    config['strong_model'] = strong_model or config['strong_model']
    offered = getattr(provider, 'LISTED_MODELS', None)
    if offered is not None and config['strong_model'] not in offered:
        if strong_model:
            raise ValueError(f'--builder-strong-model {strong_model} is not a {provider.NAME} model: {", ".join(offered)}')
        config['strong_model'] = None
    if config['strong_model'] and _bare(config['strong_model']) == _bare(config['checker_model']):
        raise ValueError(f"--builder-strong-model {config['strong_model']} is the model the checkers move to "
                         "when the Builder escalates, so the escalated work would be checked by its own model; "
                         "choose another model" + (", or another checker_model under [builder_retry] in the "
                                                   "provider config" if declared else ""))
    return config


def enabled(state):
    return (state.get('settings', {}).get('builder_retry', {}).get('enabled') is True
            and not state.get('settings', {}).get('workflow'))


def key(state, task=None):
    task = task if task is not None else state.get('current_task') or {}
    members = task.get('milestone_ids') or [task.get('milestone_id') or task.get('id')]
    return s.digest([state.get('goal_contract', {}).get('hash'), sorted(members)])


def lane(state):
    ident = key(state)
    lanes = state.setdefault('builder_retries', {})
    previous = lanes.get(state.get('builder_retry_key'))
    if previous and state.get('builder_retry_key') != ident:
        # An operator may pin a new route while a revised contract is reviewed.
        # A prior milestone's retry baseline must never undo that instruction.
        roles = state['settings']['roles']
        for role, route in {'terra': previous['initial_route'], **previous.get('checker_routes', {})}.items():
            if not roles.get(role, {}).get('model_pinned'):
                roles[role] = copy.deepcopy(route)
            state.setdefault('sessions', {}).pop(role, None)
    state['builder_retry_key'] = ident
    return lanes.setdefault(ident, {'initial_route': copy.deepcopy(state['settings']['roles']['terra']),
                                   'failures': [], 'action': None})


def guard(state):
    if not enabled(state):
        return
    current = lane(state)
    if current['action'] == 'defer':
        if state.get('parent_run'):
            raise s.Paused(SERIAL, 'This Builder left its stronger attempt to the parent run, which makes it serially')
        _take_deferred(state, current)
    if current['action'] == 'pause':
        raise s.Paused('PAUSED_BUILDER_RETRY_LIMIT',
                       'Builder retry/escalation exhausted for this approved milestone; replan or change policy explicitly')


def failed_before(state, milestone_id):
    """Whether a Builder already failed this milestone; the rest of its attempts are serial."""
    current = state.get('builder_retries', {}).get(key(state, {'milestone_id': milestone_id}))
    return bool(current and current['failures'])


def adopt(state, worker):
    """Carry a parallel worker's deferred retry lane into the parent run.

    Its failures stay counted, so the parent makes only the stronger attempt the worker
    left to it, and a restart cannot add another. The parent's own lane for the milestone
    has no failures (select() never parallelizes one that has), so it is replaced.
    """
    ident = worker.get('builder_retry_key')
    deferred = worker.get('builder_retries', {}).get(ident) or {}
    if deferred.get('action') != 'defer' or ident != key(state, worker.get('current_task')):
        raise s.Paused('PAUSED_ORCHESTRATOR_WORKER', 'Builder deferred its stronger attempt without a saved retry decision')
    lanes = state.setdefault('builder_retries', {})
    if not lanes.get(ident, {}).get('failures'):
        lanes[ident] = copy.deepcopy(deferred)
    decisions = state.setdefault('builder_retry_decisions', [])
    for decision in worker.get('builder_retry_decisions', []):
        if decision not in decisions:
            decisions.append(copy.deepcopy(decision))


def _take_deferred(state, current):
    """Make the stronger attempt a parallel worker deferred, now that this run builds serially."""
    config = state['settings']['builder_retry']
    route = state['settings']['roles']['terra']
    # The route to restore after this milestone is the one this run has now, not the worker's copy.
    current['initial_route'] = copy.deepcopy(route)
    action, checkers, stop_reason = _escalate(state, current, config, route)
    _decide(state, current, action, current['failures'][-1],
            'Stronger attempt deferred by a parallel Builder; making it serially', checkers, stop_reason)


def _tool_route(state, route):
    """A route a configured command tool serves (settings.provider names its config).

    Such a tool spells its models itself (autocode_configure._provider_model): no openai/ alias
    is added, and a bare name is the tool's model, not a native Codex one.
    """
    settings = state['settings']
    return (settings.get('provider') not in (None, 'opencode')
            and route.get('engine', settings.get('engine')) == 'opencode')


def _movable(state, route):
    return not (route.get('model_pinned') or route.get('provider') not in (None, 'openai')
                or ('/' not in str(route.get('model')) and not _tool_route(state, route)))


def _tool_swap_problem(state, config, model):
    """Why a configured tool's checkers cannot all move off the strong model, or None.

    AutoCode cannot ask the tool what it serves here, so a checker moves only when it runs exactly
    the strong model (the tool's own spelling), is neither pinned nor on another provider, and its
    replacement is a different model this run already routes a role to. The reason names the jobs
    as the screen does (autocode_roles), in prose: this module imports only autocode_util.
    """
    roles = state['settings']['roles']
    replacement = config.get('checker_model') or DEFAULTS['checker_model']
    routed = {route.get('model') for route in roles.values() if isinstance(route, dict)}
    for role in colliding_checkers(state, model):
        route = roles[role]
        if route.get('model') != model:
            return f'a Tester or Completion Reviewer route names it {route.get("model")}'
        if not _movable(state, route):
            return 'a Tester or Completion Reviewer on it is pinned or uses another provider'
        if _bare(replacement) == _bare(model):
            return f'the checkers would move to {replacement}, the stronger model itself'
        if replacement not in routed:
            return f'the checkers would move to {replacement}, which no role of this run uses'
    return None


def colliding_checkers(state, model):
    """Checker roles whose route is the given Builder model."""
    roles = state['settings']['roles']
    return [role for role in CHECKERS
            if isinstance(roles.get(role), dict) and _bare(roles[role].get('model')) == _bare(model)]


def swap_checkers(state, current, config, model):
    """Move checkers off the escalated Builder's model so it never checks its own work.

    The replaced routes are kept on the lane and restored at the next milestone. Pinned,
    custom-provider and bare-name (Codex engine) checkers are left alone; the dispatch
    cross-model guard still pauses if one of them collides.
    """
    # Runs saved before checker_model existed use the default rather than check their own work.
    replacement = config.get('checker_model') or DEFAULTS['checker_model']
    swapped = {}
    for role in colliding_checkers(state, model) if _bare(replacement) != _bare(model) else ():
        route = state['settings']['roles'][role]
        if not _movable(state, route):
            continue
        current.setdefault('checker_routes', {}).setdefault(role, copy.deepcopy(route))
        effort = route.get('reasoning_effort')
        route.update(model=replacement, reasoning_effort=effort if effort in CHECKER_EFFORTS else 'high')
        state.setdefault('sessions', {}).pop(role, None)
        swapped[role] = replacement
    return swapped


def _escalate(state, current, config, route):
    """Move the Builder to the strong model, or say why this run cannot: (action, checkers, stop reason)."""
    model = config['strong_model']
    if not model:
        return 'pause', {}, NO_STRONG_MODEL
    engine = route.get('engine', state['settings'].get('engine'))
    # A configured tool whose Builder has a bare model name spells its models itself; a tool
    # that spells them provider/model (kilocode) keeps the policy it had, alias included.
    tool = _tool_route(state, route) and '/' not in str(current['initial_route'].get('model'))
    if engine == 'opencode' and '/' not in model and not tool:
        model = 'openai/' + model
    elif engine == 'codex':
        model = _bare(model)
    # Never undo explicit pins or silently change provider/transport.
    if route.get('model_pinned') or route.get('provider') not in (None, 'openai'):
        return 'pause', {}, EXHAUSTED
    if state.get('parent_run') and colliding_checkers(state, model):
        # The parent run checks this batch with checkers this worker cannot move; the
        # strong model would check its own work. The parent makes the attempt serially.
        return 'defer', {}, (f'Parallel Builder needs the stronger model {model}, which also checks this batch; '
                             'the parent run makes that attempt serially after integrating the other Builders')
    # Such a tool's Builder escalates only when every checker on the strong model can move off it;
    # otherwise nothing changes and the run pauses here, before any route is rewritten.
    problem = tool and _tool_swap_problem(state, config, model)
    if problem:
        return 'pause', {}, CHECKER_STUCK.format(model=model, why=problem)
    route.update(model=model, reasoning_effort=config['strong_reasoning_effort'])
    return 'escalate', swap_checkers(state, current, config, model), None


def _decide(state, current, action, evidence, reason, checkers, stop_reason):
    route = state['settings']['roles']['terra']
    current['action'] = action
    state.setdefault('sessions', {}).pop('terra', None)
    state.setdefault('builder_retry_decisions', []).append({
        'at': s.now(), 'owner': 'autoresolver', 'action': action, 'failure': evidence,
        'reason': reason, 'milestone_key': key(state), 'attempt': len(current['failures']),
        'selected_model': route['model'], 'selected_effort': route.get('reasoning_effort'),
        **({'checker_models': checkers} if checkers else {})})
    if action == 'pause':
        state.update(status='PAUSED_BUILDER_RETRY_LIMIT', phase='PAUSED_OR_BLOCKED', stop_reason=stop_reason)
    elif action == 'defer':
        state.update(status=SERIAL, stop_reason=stop_reason)


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
    n = len(current['failures'])
    # With no stronger model the escalation slot is spent as one more ordinary retry: a live Claude run
    # paused on its second failure with correct code and two tests left to strengthen (2026-09-30).
    count += 0 if config['strong_model'] else 1
    action = 'retry' if n <= count else 'escalate' if n == count + 1 else 'pause'
    checkers, stop_reason = {}, EXHAUSTED
    if action == 'escalate':
        action, checkers, stop_reason = _escalate(state, current, config, state['settings']['roles']['terra'])
    _decide(state, current, action, evidence, reason, checkers, stop_reason)
    return action
