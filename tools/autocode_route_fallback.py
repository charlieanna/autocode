"""Model fallback for a role whose provider quota ran out (issue #184): the policy.

Plain functions over the saved settings and run state. They decide whether a stopped
attempt may continue on a model the user listed with ``--fallback ROLE=MODEL[@EFFORT]``,
record the switch, and give the next dispatch of that role its route. This module never
launches a provider, archives an attempt, reads ``state['status']`` or writes
``settings['roles']``. A switch is an overlay that ends by itself at the next dispatch
once the milestone, the role's saved route, its pin or the list changes, so escalations,
checker swaps, lane restores and pins are never undone: nothing is ever restored.

Only the Builder (terra), Tester (sol) and Completion Reviewer (completion) fall back,
and only after an attempt that stopped on a quota (PAUSED_BUDGET, the first time) or on a
rate limit for the second time in the milestone (the first is archived and pauses as
before), changed no source and ran on the saved route. Each role switches at most once
per milestone (autocode_builder_policy.key). A candidate keeps the role's engine and
provider; it is skipped when it would check, or be checked by, its own model family
(the Builder's effective route, and for a checker every model that built in this run).
Listing a model is the user's billing consent; its plan and billing labels are recorded.

Keys this module owns (it is their only writer):
- settings['route_fallback']: {'version': 1, 'roles': {role: [{'model', 'reasoning_effort',
  'plan', 'billing'}]}}, the value configure() returns for a new run. Absent without the
  flag, so an unconfigured run is unchanged.
- state['route_fallbacks']: switch entries. record_switch() appends one; dispatch_route()
  sets its ended_at and end_reason (milestone_complete, route_changed, pinned, list_changed).
- user_events kinds 'route_fallback', 'route_fallback_refused' and 'route_fallback_ended'
  (actor 'runner'). Existing keys: sessions[role] is popped and a session_rotations row is
  added when an overlay starts or ends.
Readers: this module; and, as the runtime is wired to it (nothing calls it yet), the
in-run switch (decide, prior_limit_stops, record_switch, record_refusal, message),
autocode.run_role (dispatch_route), autocode_dispatch (effective_routes),
autocode_escalation (active), autocode_run_view (view), autocode_goals (summary_lines)
and autocode_configure (configure, creation_lines, preflight_roles).
Malformed saved fallback state raises Paused(STATUS); no provider launches.
"""
from __future__ import annotations

import copy
import re

try:
    from . import autocode_builder_policy as builder_policy, autocode_roles as names
    from . import autocode_util as util, model_catalogue as catalogue
except ImportError:
    import autocode_builder_policy as builder_policy
    import autocode_roles as names
    import autocode_util as util
    import model_catalogue as catalogue

ROLES = ('terra', 'sol', 'completion')
# Stage -> the role whose saved route it runs. units.autoplanner.route_for runs astra_checkpoint
# on the completion route whenever the run has one, also under reviewer routing (glm_first_v1),
# where its job is the Tester: it is eligible as the completion role and printed by its job name.
STAGES = {'terra': 'terra', 'sol': 'sol', 'astra_review': 'completion', 'astra_checkpoint': 'completion'}
TRIGGERS = ('PAUSED_BUDGET', 'PAUSED_RATE_LIMIT')
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
ROUTE_KEYS = ('engine', 'provider', 'model', 'reasoning_effort')
STATUS = 'PAUSED_ROUTE_FALLBACK'
SCOPE = 'provider_quota_route_fallback'
# Matched against provider error message text only (failure_text), never whole events. 'quota'
# must stand alone (letters may not touch it): Azure OpenAI's per-minute 429 links to
# .../quotaincrease, which support.failure_status reads as PAUSED_BUDGET, and a transient
# throttle must not use the role's one switch. Codes such as insufficient_quota still match.
QUOTA_MARKERS = re.compile(r'(?<![a-z])quota(?![a-z])|usage[ _]limit|insufficient[ _]credit|add credits|credit balance')
RATE_MARKERS = re.compile(r'rate[ _]limit|\b429\b')
AUTH_MARKERS = re.compile(r'\b401\b|\b403\b|unauthori[sz]ed|unauthenticated|authentication|invalid[ _]api[ _]key'
                          r'|not logged in|log ?in required|please log ?in|expired token|token expired'
                          r'|forbidden|credential')
# The same capacity text autocode_support.failure_status classifies; capacity has its own retry.
CAPACITY_MARKERS = ('selected model is at capacity', 'model is at capacity', 'model capacity exceeded',
                    'model_overloaded', 'resource_exhausted')
FAILURE_EVENTS = ('turn.failed', 'error')
_NUMBER_KEYS = ('code', 'status', 'statusCode')
_ROLE_STAGE = {'terra': 'terra', 'sol': 'sol', 'completion': 'astra_review'}


def _stop(message):
    raise util.Paused(STATUS, f'{message}; no provider will launch')


def _norm(model):
    return str(model or '').strip().lower().removeprefix('openai/')


def _route(config):
    return {key: (config if isinstance(config, dict) else {}).get(key) for key in ROUTE_KEYS}


def _label(role):
    return names.screen_name(_ROLE_STAGE.get(role, role))


def _engine(settings, role):
    route = (settings.get('roles') or {}).get(role) or {}
    return route.get('engine') or settings.get('engine') or 'codex'


def _custom_provider(settings, role):
    """The name of the provider config a non-Codex role runs through, or None for Codex and built-in OpenCode.

    Such a provider (claude, kilocode) names its own models, bare or provider/model, the
    split autocode_configure.configure_joint makes; only built-in OpenCode needs provider/model.
    """
    provider = settings.get('provider')
    if _engine(settings, role) == 'codex' or provider in (None, 'opencode'):
        return None
    return str(provider)


def _plan(settings, role, model):
    """(plan, billing) labels: none on Codex, the provider's name for a provider config, else the catalogue's."""
    if _engine(settings, role) == 'codex':
        return None, None
    provider = _custom_provider(settings, role)
    return (provider, None) if provider else catalogue.plan(model)


def _members(task):
    task = task or {}
    return list(task.get('milestone_ids') or ([task['milestone_id']] if task.get('milestone_id') else []))


def _milestone_key(state):
    if not (state.get('goal_contract') or {}).get('hash') or not _members(state.get('current_task')):
        return None
    return builder_policy.key(state)


def _partners(role):
    """(producer, checker) pairs of autocode_dispatch's cross-model rule that contain the role."""
    return [(producer, checker) for producer, checker in catalogue.CHECKS if role in (producer, checker)]


def _valid_row(row):
    return (isinstance(row, dict) and isinstance(row.get('model'), str) and bool(row['model'].strip())
            and row.get('reasoning_effort') in (None, *EFFORTS)
            and all(row.get(key) is None or isinstance(row[key], str) for key in ('plan', 'billing')))


def _config(state):
    """The saved list, None when the run has none; Paused when it is not the documented shape."""
    config = (state.get('settings') or {}).get('route_fallback')
    if config is None:
        return None
    if not (isinstance(config, dict) and config.get('version') == 1 and isinstance(config.get('roles'), dict)
            and all(role in ROLES and isinstance(rows, list) and rows and all(_valid_row(row) for row in rows)
                    for role, rows in config['roles'].items())):
        _stop('Saved model fallback list (settings.route_fallback) is malformed')
    return config


def _valid_entry(entry):
    return (isinstance(entry, dict) and isinstance(entry.get('id'), str) and entry.get('role') in ROLES
            and isinstance(entry.get('milestone_key'), str)
            and (entry.get('ended_at') is None or isinstance(entry['ended_at'], str))
            and all(isinstance(entry.get(side), dict) and isinstance(entry[side].get('model'), str)
                    for side in ('from', 'to')))


def _entries(state):
    entries = state.get('route_fallbacks', [])
    if not isinstance(entries, list) or not all(_valid_entry(entry) for entry in entries):
        _stop('Saved model fallback switches (route_fallbacks) are malformed')
    return entries


# --- Configuration (new runs) ---------------------------------------------------------

def parse(specs):
    """--fallback values -> [{'role', 'model', 'reasoning_effort'}] in the order given."""
    rows = []
    for spec in specs or ():
        text = spec.strip() if isinstance(spec, str) else ''
        role, sep, rest = text.partition('=')
        role, model, effort = role.strip(), rest.strip(), None
        if not sep:
            raise ValueError(f'--fallback {spec!r}: expected ROLE=MODEL[@EFFORT], for example sol=gpt-6-luna')
        if role not in ROLES:
            raise ValueError(f'--fallback {spec!r}: {role or "an empty role"} cannot fall back. Use terra (Builder), '
                             'sol (Tester) or completion (Completion Reviewer); planning roles (requirements, glm, '
                             'plan_reviewer) and the Resolver (astra) are not supported')
        if '@' in model:
            model, effort = (part.strip() for part in model.rsplit('@', 1))
            if effort not in EFFORTS:
                raise ValueError(f'--fallback {spec!r}: effort must be one of {", ".join(EFFORTS)}')
        if not model or any(c.isspace() for c in model):
            raise ValueError(f'--fallback {spec!r}: name one model after "="')
        rows.append({'role': role, 'model': model, 'reasoning_effort': effort})
    return rows


def configure(specs, settings, *, offered=None):
    """The settings['route_fallback'] value for a new run, or None when no --fallback was given.

    Raises ValueError with the text to show; never mutates settings. ``offered`` is a custom
    provider's LISTED_MODELS. A candidate keeps the role's engine and provider: on Codex it is
    a bare name ('openai/' is dropped), since another prefix would change the billing route;
    built-in OpenCode needs provider/model; a provider config takes the names it lists, bare
    ones included (claude-sonnet-5-5), and its name is the recorded plan.
    """
    rows = parse(specs)
    if not rows:
        return None
    if settings.get('conversation_profile') == 'continuous-v1':
        raise ValueError('--fallback cannot be used with the continuous-v1 conversation profile, '
                         "which fixes every role's model")
    roles, result, seen = settings.get('roles') or {}, {}, set()
    for row in rows:
        role, model = row['role'], row['model']
        given, label, saved = f'--fallback {role}={model}', _label(role), roles.get(role)
        if not isinstance(saved, dict) or not saved.get('model'):
            raise ValueError(f'{given}: this run has no {label} route to fall back from')
        if saved.get('model_pinned'):
            raise ValueError(f'{given}: the {label} model is pinned (--pin-model-role {role}); '
                             'a pinned role never changes model')
        if _engine(settings, role) == 'codex':
            model = model.removeprefix('openai/')
            if '/' in model:
                raise ValueError(f'{given}: on the Codex engine a fallback is a bare model name; a provider '
                                 'prefix would change the billing route')
        elif _custom_provider(settings, role) is None and not catalogue.usable([model]):
            raise ValueError(f'{given}: on the OpenCode engine name the model as provider/model, '
                             'for example openai/gpt-6-luna')
        if _norm(model) == _norm(saved['model']):
            raise ValueError(f'{given}: that is already the {label} model; list a different one')
        if (role, _norm(model)) in seen:
            raise ValueError(f'{given} is listed twice')
        seen.add((role, _norm(model)))
        if offered is not None and model not in offered:
            raise ValueError(f'{given}: this provider does not list that model: {", ".join(sorted(offered))}')
        plan, billing = _plan(settings, role, model)
        result.setdefault(role, []).append({'model': model, 'reasoning_effort': row['reasoning_effort'],
                                            'plan': plan, 'billing': billing})
    return {'version': 1, 'roles': result}


def creation_lines(settings):
    """What a new run prints about its fallback list, with advisory warnings that never refuse."""
    config = _config({'settings': settings})
    if config is None:
        return []
    roles = settings.get('roles') or {}
    lines = ['Model fallback: when a role stops on a provider quota, or on a rate limit again after the first was '
             'archived, the rest of that milestone runs on the first usable model listed:']
    for role, rows in config['roles'].items():
        saved, label = roles.get(role) or {}, _label(role)
        for row in rows:
            effort = row['reasoning_effort'] or f"{saved.get('reasoning_effort') or 'default'} (inherited)"
            billing = (f"{row['plan']}, {row['billing'] or 'billing unknown'}" if row['plan']
                       else f"same sign-in as the {label}")
            lines.append(f"  {label}: {saved.get('model')} -> {row['model']}, {effort} effort ({billing})")
            for producer, checker in _partners(role):
                partner = checker if role == producer else producer
                other = (roles.get(partner) or {}).get('model')
                pair = (row['model'], other) if role == producer else (other, row['model'])
                if other and not independent(*pair):
                    lines.append(f"    Note: skipped while the {_label(partner)} runs {other}, "
                                 'because one would check the other')
            if row['plan'] and saved.get('model') and _plan(settings, role, saved['model'])[0] == row['plan']:
                lines.append(f"    Note: on the same plan as {saved['model']} ({row['plan']}); "
                             'its quota may run out too')
        if _engine(settings, role) == 'codex':
            lines.append(f'    Note: a Codex fallback uses the {label} sign-in, so it helps only with per-model limits')
    return lines


def preflight_roles(config, settings=None):
    """Pseudo-roles {'sol#fallback1': {'model': m}} for opencode.check_models and check_subscription_routes.

    With settings, roles on the Codex engine are left out.
    """
    result = {}
    for role, rows in ((config or {}).get('roles') or {}).items():
        if settings is not None and _engine(settings, role) == 'codex':
            continue
        for index, row in enumerate(rows, 1):
            result[f'{role}#fallback{index}'] = {'model': row['model']}
    return result


# --- Decision -------------------------------------------------------------------------

def enabled(state):
    return _config(state) is not None


def independent(producer, checker):
    """model_catalogue.independent over lowercased names without 'openai/'.

    At least as strict as autocode_dispatch.enforce_cross_model_verification (a test pins
    that): a pair it refuses is never independent here. A missing model has no conflict.
    """
    if not all(isinstance(model, str) and model.strip() for model in (producer, checker)):
        return True
    return catalogue.independent(_norm(producer), _norm(checker))


def failure_text(events):
    """Lowercased message text of the provider's turn.failed and error events.

    Only string leaves, plus code/status/statusCode numbers; 'usage' is skipped, so a token
    count such as 14290 can never read as a 429.
    """
    parts = []

    def visit(value):
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                if key == 'usage':
                    continue
                if key in _NUMBER_KEYS and isinstance(item, int) and not isinstance(item, bool):
                    parts.append(str(item))
                else:
                    visit(item)
    for event in events or ():
        if isinstance(event, dict) and event.get('type') in FAILURE_EVENTS:
            visit(event)
    return '\n'.join(parts).lower()


def _record_role(record):
    """The fallback role a stage record ran as, or None when its route role is another one."""
    role = STAGES.get(names.base_stage(record.get('stage')))
    return role if role and role == (record.get('route_role') or record.get('role')) else None


def _record_key(state, record):
    tasks = [state.get('current_task'), *(state.get('task_archive') or [])]
    task = next((t for t in tasks if isinstance(t, dict) and t.get('id') == record.get('task_id')), None)
    if task is None or not record.get('contract_hash') or not _members(task):
        return None
    return builder_policy.key({'goal_contract': {'hash': record['contract_hash']}}, task)


def prior_limit_stops(state, role, status, *, classify):
    """Archived, non-repair attempts of this role in the current milestone whose events classify as status.

    ``classify`` is autocode_support.failure_status, passed in. A rate limit falls back only on
    the second stop: the first is the attempt reconcile_rate_limited_stage archived.
    """
    key = _milestone_key(state)
    count = 0
    for record in state.get('stages') or []:
        if (key is None or not isinstance(record, dict) or record.get('rejected') is not True
                or record.get('report_only') or record.get('runner_owned') or not record.get('events')
                or _record_role(record) != role or _record_key(state, record) != key):
            continue
        try:
            count += classify(record['events']) == status
        except (OSError, ValueError):
            continue
    return count


def _launch_model(record):
    route = record.get('launch_route')
    if isinstance(route, dict) and isinstance(route.get('model'), str) and route['model']:
        return route['model']
    command = record.get('command')
    if isinstance(command, list) and '--model' in command[:-1]:
        return str(command[command.index('--model') + 1])
    return None


def _builder_models(state):
    """Every model that ran a Builder attempt in this run, parallel workers included."""
    models = []
    for record in state.get('stages') or []:
        if (isinstance(record, dict) and names.base_stage(record.get('stage')) == 'terra'
                and not record.get('report_only') and not record.get('runner_owned')):
            model = _launch_model(record)
            if model and model not in models:
                models.append(model)
    return models


def decide(state, record, *, raised, classified, events, source_changed, prior_rate_limit_stops=0, available=None):
    """Whether this stopped attempt switches to a listed model: {'action': 'switch'|'pause', 'reason', ...}.

    Pure. ``classified`` is support.failure_status recomputed over the attempt's events;
    ``source_changed`` whether the attempt changed source; ``available(model)`` an optional
    probe. Any 'pause' means the caller re-raises the original Paused untouched.
    """
    record = record if isinstance(record, dict) else {}
    stage = names.base_stage(record.get('stage')) or None
    role = STAGES.get(stage)
    task = state.get('current_task') or {}
    # 'job' is the name message() prints, with state as view() and summary_lines() name it:
    # astra_checkpoint is the Tester under reviewer routing.
    result = {'action': 'pause', 'reason': None, 'role': role, 'stage': stage, 'status': raised,
              'job': names.screen_name(stage, state) or None,
              'from': None, 'to': None, 'skipped': [], 'milestone_key': None, 'milestone_ids': _members(task),
              'task_id': task.get('id'), 'contract_hash': (state.get('goal_contract') or {}).get('hash'), 'plan': None}

    def pause(reason):
        result['reason'] = reason
        return result

    config = _config(state)
    if config is None:
        return pause('not_configured')
    if raised not in TRIGGERS:
        return pause('not_a_trigger')
    if classified != raised:
        return pause('status_mismatch')
    code = record.get('exit_code')
    if (type(code) is not int or code <= 0 or record.get('timed_out') or record.get('interrupted')
            or record.get('cleanup_error')):
        return pause('not_stopped')
    if any(isinstance(event, dict) and event.get('type') == 'turn.completed' for event in events or ()):
        return pause('turn_completed')
    text = failure_text(events)
    if any(marker in text for marker in CAPACITY_MARKERS):
        return pause('capacity')
    if AUTH_MARKERS.search(text):
        return pause('authentication')
    if not (QUOTA_MARKERS if raised == 'PAUSED_BUDGET' else RATE_MARKERS).search(text):
        return pause('no_limit_marker')
    if record.get('report_only'):
        return pause('report_repair')
    if role is None or _record_role(record) != role:
        return pause('stage_not_eligible')
    candidates = config['roles'].get(role) or []
    if not candidates:
        return pause('role_not_listed')
    if state.get('parent_run'):
        return pause('parallel_worker')
    settings = state['settings']
    if settings.get('conversation_profile') == 'continuous-v1':
        return pause('continuous_profile')
    saved = (settings.get('roles') or {}).get(role)
    if isinstance(saved, dict) and saved.get('model_pinned'):
        return pause('pinned')
    key = _milestone_key(state)
    if key is None:
        return pause('no_milestone')
    result['milestone_key'] = key
    entries = _entries(state)
    if any(entry['role'] == role and entry['milestone_key'] == key for entry in entries):
        return pause('already_switched')
    origin = _route(saved)
    if not isinstance(saved, dict) or not isinstance(origin['model'], str) or not origin['model']:
        return pause('route_drift')
    result['from'] = origin
    if not isinstance(record.get('launch_route'), dict) or _route(record['launch_route']) != origin:
        return pause('route_drift')
    if raised == 'PAUSED_RATE_LIMIT' and prior_rate_limit_stops < 1:
        return pause('rate_limit_first_stop')
    if source_changed:
        return pause('source_changed')
    effective = effective_routes(state)
    exhausted = {_norm(entry['from']['model']) for entry in entries if entry['milestone_key'] == key}
    built = _builder_models(state) if role in builder_policy.CHECKERS else []
    for row in candidates:
        model = row['model']
        reason = None
        if _norm(model) == _norm(origin['model']):
            reason = 'current_model'
        elif _norm(model) in exhausted:
            reason = 'exhausted_this_milestone'
        else:
            for producer, checker in _partners(role):
                partner = checker if role == producer else producer
                other = (effective.get(partner) or {}).get('model')
                if not independent(*((model, other) if role == producer else (other, model))):
                    reason = f'not_independent:{partner}'
                    break
            if reason is None and any(not independent(builder, model) for builder in built):
                reason = 'checks_earlier_builder_work'
            if reason is None and available is not None and not available(model):
                reason = 'unavailable'
        if reason:
            result['skipped'].append({'model': model, 'reason': reason})
            continue
        result.update(action='switch', reason='granted', plan={'plan': row['plan'], 'billing': row['billing']},
                      to={**origin, 'model': model, 'reasoning_effort': row['reasoning_effort'] or origin['reasoning_effort']})
        return result
    return pause('no_candidate')


def message(decision):
    """The one console line for a decision the in-run switch acted on."""
    job = decision.get('job') or names.screen_name(decision.get('stage') or '') or 'Stage'
    kind = 'rate limit' if decision.get('status') == 'PAUSED_RATE_LIMIT' else 'quota'
    origin = (decision.get('from') or {}).get('model')
    stop = f"{job}: {kind} stop" + (f' on {origin}' if origin else '')
    skipped = '; '.join(f"{row['model']}: {row['reason']}" for row in decision.get('skipped') or [])
    if decision.get('action') == 'switch':
        ids = ', '.join(decision.get('milestone_ids') or [])
        return (f"{stop} ({decision['status']}); attempt archived; continuing on your fallback "
                f"{decision['to']['model']} for the rest of milestone {ids}" + (f' (skipped {skipped})' if skipped else ''))
    return f"{stop}; model fallback not used ({decision.get('reason')}" + (f'; skipped {skipped})' if skipped else ')')


# --- Recording ------------------------------------------------------------------------

def record_switch(state, decision, *, attempt_id, events_path):
    """Save a granted switch once per attempt; pop the role's session; returns the entry."""
    if decision.get('action') != 'switch':
        raise ValueError('record_switch needs a granted switch decision')
    _config(state)
    entries = _entries(state)
    role = decision['role']
    ident = 'rf-' + util.digest([SCOPE, attempt_id, role, decision['milestone_key'], decision['to']['model']])[:12]
    existing = next((entry for entry in entries if entry['id'] == ident), None)
    if existing is not None:
        return copy.deepcopy(existing)
    at = util.now()
    entry = {'id': ident, 'role': role, 'stage': decision['stage'], 'status': decision['status'],
             'from': copy.deepcopy(decision['from']), 'to': copy.deepcopy(decision['to']),
             'events': str(events_path), 'attempt_id': attempt_id, 'at': at, 'task_id': decision['task_id'],
             'milestone_ids': list(decision['milestone_ids']), 'milestone_key': decision['milestone_key'],
             'contract_hash': decision['contract_hash'], 'skipped': copy.deepcopy(decision['skipped']),
             'plan': copy.deepcopy(decision['plan']), 'ended_at': None, 'end_reason': None}
    state.setdefault('route_fallbacks', []).append(entry)
    old = state.setdefault('sessions', {}).pop(role, None)
    if old:
        state.setdefault('session_rotations', []).append({
            'role': role, 'old_session': old, 'at': at,
            'reason': f"Model fallback started: {entry['from']['model']} -> {entry['to']['model']} after {entry['status']}"})
    state.setdefault('user_events', []).append({
        'kind': 'route_fallback', 'actor': 'runner', 'at': at, 'id': ident, 'role': role, 'stage': entry['stage'],
        'status': entry['status'], 'from_model': entry['from']['model'], 'to_model': entry['to']['model'],
        'events': entry['events']})
    return copy.deepcopy(entry)


def record_refusal(state, decision, *, attempt_id, events_path):
    """Note once per attempt why a configured fallback was not used. False on a replay."""
    if decision.get('action') != 'pause':
        raise ValueError('record_refusal needs a refused decision')
    if any(isinstance(event, dict) and event.get('kind') == 'route_fallback_refused'
           and event.get('attempt_id') == attempt_id for event in state.get('user_events') or []):
        return False
    state.setdefault('user_events', []).append({
        'kind': 'route_fallback_refused', 'actor': 'runner', 'at': util.now(), 'role': decision.get('role'),
        'stage': decision.get('stage'), 'status': decision.get('status'), 'reason': decision.get('reason'),
        'skipped': copy.deepcopy(decision.get('skipped') or []), 'attempt_id': attempt_id,
        'events': str(events_path)})
    return True


# --- Dispatch -------------------------------------------------------------------------

def _end_reason(state, entry, config):
    """Why an overlay no longer applies, or None while it does. Read-only."""
    if entry.get('ended_at'):
        return entry.get('end_reason') or 'ended'
    if entry['milestone_key'] != _milestone_key(state):
        return 'milestone_complete'
    saved = (state['settings'].get('roles') or {}).get(entry['role'])
    if not isinstance(saved, dict) or _route(saved) != _route(entry['from']):
        return 'route_changed'
    if saved.get('model_pinned'):
        return 'pinned'
    if entry['to']['model'] not in [row['model'] for row in config['roles'].get(entry['role']) or []]:
        return 'list_changed'
    return None


def active(state, role):
    """The role's live overlay entry (a copy), or None. Never mutates; parallel workers have none."""
    config = _config(state)
    if config is None or state.get('parent_run'):
        return None
    entry = next((entry for entry in reversed(_entries(state))
                  if entry['role'] == role and _end_reason(state, entry, config) is None), None)
    return copy.deepcopy(entry)


def effective_routes(state):
    """A copy of settings.roles with live overlays applied; equal to settings.roles when none is live."""
    routes = copy.deepcopy((state.get('settings') or {}).get('roles') or {})
    for role in ROLES:
        entry = active(state, role)
        if entry and isinstance(routes.get(role), dict):
            routes[role].update(entry['to'])
    return routes


def _end(state, entry, reason, out):
    at, role, text = util.now(), entry['role'], reason.replace('_', ' ')
    entry.update(ended_at=at, end_reason=reason)
    old = state.setdefault('sessions', {}).pop(role, None)
    if old:
        state.setdefault('session_rotations', []).append(
            {'role': role, 'old_session': old, 'at': at, 'reason': f'Model fallback ended: {text}'})
    state.setdefault('user_events', []).append(
        {'kind': 'route_fallback_ended', 'actor': 'runner', 'at': at, 'id': entry['id'], 'role': role, 'reason': reason})
    model = ((state['settings'].get('roles') or {}).get(role) or {}).get('model')
    out(f'{_job(entry, state)}: back on {model} (model fallback ended: {text})')


def dispatch_route(state, stage, route_role, *, out=print):
    """The route for this dispatch: {**to, 'fallback_id'} while the role's overlay is live, else None.

    Ends every overlay that no longer applies first, popping the session it used.
    """
    config = _config(state)
    if config is None or state.get('parent_run'):
        return None
    for entry in _entries(state):
        reason = None if entry.get('ended_at') else _end_reason(state, entry, config)
        if reason:
            _end(state, entry, reason, out)
    role = STAGES.get(names.base_stage(stage))
    entry = active(state, role) if role and role == route_role else None
    return {**entry['to'], 'fallback_id': entry['id']} if entry else None


# --- Read side ------------------------------------------------------------------------

def _job(entry, state):
    return names.screen_name(entry['stage'], state) if entry.get('stage') else _label(entry['role'])


def _last_refusal(state):
    return next((event for event in reversed(state.get('user_events') or [])
                 if isinstance(event, dict) and event.get('kind') == 'route_fallback_refused'), None)


def view(state):
    """The status view's 'route_fallbacks' field; None when the run has no fallback list."""
    config = _config(state)
    if config is None:
        return None
    switches, live = [], {}
    for entry in _entries(state):
        current = not state.get('parent_run') and _end_reason(state, entry, config) is None
        switches.append({**copy.deepcopy(entry), 'job': _job(entry, state), 'active': current})
        if current:
            live[entry['role']] = {'id': entry['id'], 'model': entry['to']['model'], 'since': entry.get('at'),
                                   'stage': entry.get('stage')}
    refusal = _last_refusal(state)
    last = None if refusal is None else {
        'at': refusal.get('at'), 'role': refusal.get('role'), 'job': names.screen_name(refusal.get('stage') or '', state),
        'stage': refusal.get('stage'), 'status': refusal.get('status'), 'reason': refusal.get('reason'),
        'skipped': copy.deepcopy(refusal.get('skipped') or []), 'attempt_id': refusal.get('attempt_id')}
    return {'configured': copy.deepcopy(config['roles']), 'switches': switches, 'active': live, 'last_refusal': last}


def summary_lines(state):
    """Lines for the final summary: one per switch, and the last refusal."""
    if _config(state) is None:
        return []
    lines = []
    for entry in _entries(state):
        job = _job(entry, state)
        ids = entry.get('milestone_ids') or []
        where = f"milestone{'s' if len(ids) > 1 else ''} {', '.join(ids)}"
        if entry.get('end_reason') == 'milestone_complete':
            tail = f"back on {entry['from']['model']} from the next milestone."
        elif entry.get('ended_at'):
            tail = f"ended early ({str(entry.get('end_reason')).replace('_', ' ')})."
        else:
            tail = 'still active at completion.'
        lines.append(f"Model fallback: {job} {entry['from']['model']} -> {entry['to']['model']} after "
                     f"{entry['status']} at the {job} stage ({where}); {tail}")
    refusal = _last_refusal(state)
    if refusal is not None:
        skipped = '; '.join(f"{row.get('model')}: {row.get('reason')}" for row in refusal.get('skipped') or [])
        lines.append(f"Model fallback not used for {names.screen_name(refusal.get('stage') or '', state) or 'a stage'}: "
                     f"{refusal.get('reason')}" + (f' (skipped {skipped})' if skipped else ''))
    return lines
