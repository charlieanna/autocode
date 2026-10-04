"""Stage-boundary adapter for the existing bounded resolver policy.

Only runner-observed conditions get automatic proposals. Agent prose cannot
authorize a retry, mutate the contract, grant permission, or declare success.
"""
from dataclasses import fields, is_dataclass
from collections.abc import Mapping
from pathlib import Path
import time
import uuid
import os
import copy

try:
    from . import autocode_resolver as policy, autocode_support as support
    from . import autocode_goals as goals, autocode_failures as failures
    from . import autocode_resolver_human as human
    from . import autocode_progressive_state as progressive
    from . import autocode_resolver_recovery as recovery
    from . import autocode_recovery_grants as recovery_grants, autocode_recovery_limits as recovery_limits
except ImportError:
    import autocode_resolver as policy
    import autocode_support as support
    import autocode_goals as goals
    import autocode_failures as failures
    import autocode_resolver_human as human
    import autocode_progressive_state as progressive
    import autocode_resolver_recovery as recovery
    import autocode_recovery_grants as recovery_grants
    import autocode_recovery_limits as recovery_limits


REVIEW_STAGES = ('astra_challenge', 'astra_finalize')
MAX_PLANNING_RECOVERY_GRANTS = 2
OPERATIONAL_INSTRUCTION = (
    'AutoResolver authorized only this read-only planning report recovery. Reuse retained '
    'evidence and the saved planning exchange; do not repeat exploratory tools or restart '
    'repository discovery. Finish the required structured report promptly. If evidence is '
    'insufficient, state the concern or unresolved question in the report. Do not change '
    'requirements, permissions, models, deadlines or budgets, fabricate approval, or add '
    'another debate round. The exact final plan still requires human approval.')


def _operational_stop(message):
    raise support.Paused('PAUSED_RESOLVER_OPERATIONAL', message)


def _planning_binding(state, stage, workspace):
    planning = state['planning']
    discovery = planning['reports']['astra_discovery']['output']
    contract = state['goal_contract']
    accepted = next(row for row in reversed(state['stages'])
                    if (row.get('stage') == 'astra_discovery' or
                        (row.get('report_only') and row.get('original_stage') == 'astra_discovery'
                         and row.get('stage') == 'astra_discovery_report_repair'))
                    and not row.get('rejected') and not row.get('abandoned'))
    if (accepted.get('output') != discovery or any(
            old.get('reports', {}).get('astra_discovery', {}).get('output') == discovery
            for old in state.get('planning_history', []))):
        raise ValueError('Discovery does not belong to the current planning cycle')
    inputs = {}
    for entry in [*planning['reports'].values(), state.get('requirements_handoff') or {}]:
        if entry.get('output'):
            path = entry['output']
            if not Path(path).is_file():
                raise ValueError('Missing planning input')
            inputs[path] = support.file_hash(path)
    cycle = support.digest({'discovery': discovery, 'history': len(state.get('planning_history', [])),
                            'task_id': contract['task_id']})
    return {'cycle': cycle, 'discovery_record_hash': support.digest(accepted),
            'stage': stage, 'contract_token': goals.token(contract),
            'contract_hash': contract['hash'], 'contract_digest': support.digest(contract),
            'source_revision': support.snapshot(workspace)['revision'],
            'settings_hash': support.digest(state['settings']), 'inputs': inputs,
            'ordinary_limit': planning.get('review_call_limit', 2),
            'astra_calls': planning['astra_calls'],
            'recovery_calls_used': planning.get('recovery_review_calls_used', 0),
            'planning_inputs_hash': support.digest({'reports': planning['reports'],
                'requirements': state.get('requirements_handoff'), 'task': state.get('task'),
                'answers': state.get('answers'), 'user_events': state.get('user_events')})}


def _operational_blocked(state, run_dir):
    request = state.get('user_request') or (state.get('agent_request') or {}).get('request')
    return (any(state.get(key) for key in ('active_stage', 'pending_report_repair',
                'uncertain_artifacts', 'pending_questions'))
            or (request and request.get('kind') != 'none')
            or (state.get('goal_contract') or {}).get('body', {}).get('open_blocking_questions')
            or (Path(run_dir) / 'pause-requested').exists())


def _timeout_evidence(state, record, revision):
    """Read archive evidence only; never repair or rewrite a failed attempt."""
    if (record.get('stage') not in REVIEW_STAGES or not record.get('timed_out')
            or not all(record.get(key) for key in ('accounted', 'automatic_recovery', 'abandoned', 'rejected'))
            or record.get('changed_files') != [] or record.get('source_revision') != revision
            or record.get('planning_recovery_grant') or record.get('report_only')
            or record.get('exit_code') is None):
        return None
    paths = [Path(record.get(key) or '') for key in ('events', 'before_ref', 'after_ref')]
    if (not all(path.is_file() for path in paths)
            or len({str(path.parent) for path in paths}) != 1
            or not any(note.get('archived') == str(paths[0].parent)
                       and note.get('stage') == record['stage']
                       and note.get('iteration') == record.get('iteration')
                       for note in state.get('reconciliation_notes', []))):
        return None
    if any(event.get('type') == 'turn.completed' for event in support.events(paths[0])):
        return None
    before, after = (support.read(path) for path in paths[1:])
    if (before.get('revision') != revision or after.get('revision') != revision
            or support.changed_paths(before, after)):
        return None
    try:
        from . import autocode_process as processes
    except ImportError:
        import autocode_process as processes
    if record.get('processes') and processes.live_processes(record['processes']):
        return None
    if record.get('pid') and not record.get('processes'):
        try:
            os.kill(record['pid'], 0)
        except ProcessLookupError:
            pass
        else:
            return None
    return {str(path): support.file_hash(path) for path in paths}


def validate_operational_grant(state, stage, workspace):
    """Admission-time validation. No grant is consumed by preparing a prompt."""
    try:
        planning = state['planning']
        origin = planning.get('review_call_limit_origin', 'runner_default' if 'review_call_limit' not in planning else None)
        if origin != 'runner_default':
            _operational_stop('An explicit or unmarked planning cap cannot be exceeded by automatic recovery')
        grants = state['planning'].get('recovery_review_grants', [])
        pending = [grant for grant in grants if not grant.get('consumed')]
        if (len(pending) != 1 or len(grants) > MAX_PLANNING_RECOVERY_GRANTS
                or sum(bool(grant.get('consumed')) for grant in grants)
                != state['planning'].get('recovery_review_calls_used', 0)):
            _operational_stop('No single reserved planning recovery remains; no provider will launch')
        grant = pending[0]
        if (_operational_blocked(state, grant['run_dir'])
                or grant['binding'] != _planning_binding(state, stage, workspace)):
            _operational_stop('Planning recovery scope or inputs changed; no provider will launch')
        origin = next(row for row in state['stages'] if row.get('output') == grant['origin_output'])
        if (_timeout_evidence(state, origin, grant['binding']['source_revision']) != grant['pins']
                or support.digest(origin) != grant['origin_hash']):
            _operational_stop('Archived planning recovery evidence changed; no provider will launch')
        receipt = Path(grant['receipt_output'])
        if not receipt.is_file() or support.file_hash(receipt) != grant['receipt_hash']:
            _operational_stop('Planning recovery receipt changed; no provider will launch')
        saved = support.read(receipt)['receipt']
        if (saved['id'] != grant['id'] or saved['binding'] != grant['binding']
                or saved['origin_output'] != grant['origin_output'] or saved['pins'] != grant['pins']):
            _operational_stop('Planning recovery grant differs from its receipt; no provider will launch')
        return grant
    except (KeyError, TypeError, ValueError, OSError, StopIteration, AttributeError) as error:
        _operational_stop(f'Planning recovery cannot be validated: {error}')


def operational_boundary(runner, state, run_dir, workspace, *, persist=True):
    """Reserve at most two one-use reports for proven ordinary planning timeouts."""
    stage = state.get('next_stage')
    planning = state.get('planning') or {}
    if (not state.get('settings', {}).get('joint_planning') or stage not in REVIEW_STAGES
            or state.get('status') not in ('RUNNING', 'PAUSED_PLANNING_BUDGET')
            or _operational_blocked(state, run_dir)):
        return False
    limit = runner.planning.review_call_limit(state)
    if limit == 0 or planning.get('astra_calls', 0) < limit:
        return False
    origin = planning.get('review_call_limit_origin', 'runner_default' if 'review_call_limit' not in planning else None)
    if origin != 'runner_default':
        return False
    grants = planning.get('recovery_review_grants', [])
    if not isinstance(grants, list) or any(not isinstance(grant, dict) for grant in grants):
        _operational_stop('Saved planning recovery grants are malformed; no provider will launch')
    if (any(not grant.get('id') or not grant.get('origin_output') for grant in grants)
            or len({grant['origin_output'] for grant in grants}) != len(grants)
            or sum(bool(grant.get('consumed')) for grant in grants)
            != planning.get('recovery_review_calls_used', 0)):
        _operational_stop('Saved planning recovery accounting is inconsistent; no provider will launch')
    if any(not grant.get('consumed') for grant in grants):
        validate_operational_grant(state, stage, workspace)
        runner.timeout_recovery_guard(state)
        return True
    try:
        binding = _planning_binding(state, stage, workspace)
        discovery = planning['reports']['astra_discovery']['output']
        start = next(i for i, row in enumerate(state['stages'])
                     if (row.get('stage') == 'astra_discovery' or
                         (row.get('report_only') and row.get('original_stage') == 'astra_discovery'
                          and row.get('stage') == 'astra_discovery_report_repair'))
                     and row.get('output') == discovery
                     and not row.get('rejected') and not row.get('abandoned'))
        reviews = [row for row in state['stages'][start + 1:]
                   if row.get('stage') in REVIEW_STAGES and not row.get('runner_owned')
                   and not row.get('report_only')]
        # Only ordinary attempts can fund recovery. Failed grants never mint grants.
        ordinary = [row for row in reviews if not row.get('planning_recovery_grant')][:limit]
        eligible = [(row, pins) for row in ordinary
                    if (pins := _timeout_evidence(state, row, binding['source_revision']))]
    except (KeyError, TypeError, ValueError, OSError, StopIteration, AttributeError):
        return False
    if not eligible:
        return False
    if (len(grants) >= MAX_PLANNING_RECOVERY_GRANTS
            or planning['astra_calls'] >= limit + MAX_PLANNING_RECOVERY_GRANTS):
        _operational_stop('Reserved planning recovery exhausted; no provider will launch')
    used = {grant['origin_output'] for grant in grants}
    remaining = [(row, pins) for row, pins in eligible if row['output'] not in used]
    if not remaining:
        _operational_stop('Every proven ordinary timeout has used its recovery; no provider will launch')
    # A completed challenge cannot be challenged again using the reserve.
    if stage in planning['reports']:
        _operational_stop('Planning recovery cannot authorize another debate round')
    runner.timeout_recovery_guard(state)
    origin, pins = remaining[0]
    identity = support.digest({'binding': binding, 'origin': origin['output'], 'pins': pins})
    path = Path(run_dir) / 'resolver' / (identity + '.json')
    receipt = {'stage': 'resolver', 'role': 'resolver', 'engine': 'runner', 'runner_owned': True,
               'iteration': state['iteration'], 'finished_at': support.now(), 'exit_code': 0,
               'runner_calls': 0, 'output': str(path),
               'metrics': {'provider_tokens': {'input_tokens': 0, 'output_tokens': 0}},
               'decision': {'action': 'retry', 'rationale': OPERATIONAL_INSTRUCTION},
               'receipt': {'id': identity, 'callbacks_used': [], 'scope': 'planning_operational_recovery',
                           'binding': binding, 'origin_output': origin['output'], 'pins': pins}}
    support.atomic_json(path, receipt)
    planning.setdefault('recovery_review_grants', []).append({
        'id': identity, 'binding': binding, 'pins': pins, 'origin_output': origin['output'],
        'origin_hash': support.digest(origin), 'run_dir': str(run_dir), 'consumed': False,
        'receipt_output': str(path), 'receipt_hash': support.file_hash(path)})
    state.setdefault('stages', []).append(receipt)
    state.setdefault('history', []).append(receipt)
    if persist:
        runner.write_json(Path(run_dir) / 'state.json', state)
    return True


def reconsider_operational_request(runner, state, run_dir, workspace):
    """Withdraw an unanswered planning-cap ask only with proven existing credit.

    The caller owns the writer lock. Admission covers receipt verification, proof
    and the single state commit; operational_boundary must not save halfway through.
    """
    try:
        with runner.interventions.admission(run_dir):
            public = human.current(state)
            if not public or public['scope'] != 'operational_exhaustion':
                return False
            saved = state.get('resolver') or {}
            entry = saved['human_escalations'][public['request_id']]
            proposal = entry['identity']['proposal']
            if (proposal['origin'].get('pause_status') != 'PAUSED_PLANNING_BUDGET'
                    or proposal['origin'].get('stage') != state.get('next_stage')
                    or entry.get('response') or saved.get('pending_human_response')
                    or saved.get('human_response_frontier')
                    or any(response.get('request_id') == public['request_id']
                           for response in saved.get('human_responses', []))
                    or state.get(human.PRIVATE)
                    or any(state.get(key) for key in ('active_stage', 'uncertain_artifacts', 'pending_report_repair'))
                    or (state.get('goal_contract') or {}).get('body', {}).get('open_blocking_questions')
                    or (state.get('pause_intent') and not state['pause_intent'].get('acknowledged_at'))
                    or (Path(run_dir) / 'pause-requested').exists()):
                return False
            candidate = copy.deepcopy(state)
            if not human.supersede_operational(candidate,
                    'AutoResolver reconsidered the unchanged planning boundary and proved existing recovery credit'):
                return False
            if not operational_boundary(runner, candidate, run_dir, workspace, persist=False):
                return False
            candidate.update(status='RUNNING', phase='PLANNING')
            candidate.pop('stop_reason', None)
            runner.write_json(Path(run_dir) / 'state.json', candidate)
            state.clear()
            state.update(candidate)
            return True
    except (support.Paused, KeyError, TypeError, ValueError, OSError, RuntimeError):
        # A failed speculative proof never withdraws or republishes the old ask.
        return False


def _operational_receipt(state, run_dir, action, detail, evidence):
    state['run_dir'] = str(Path(run_dir).resolve())
    binding = human._binding(state)
    identity = support.digest({'scope': 'operational_diagnostic', 'action': action,
                               'detail': detail, 'evidence': evidence, 'binding': binding})
    saved = state.setdefault('resolver', {})
    receipts = saved.setdefault('operational_receipts', {})
    if identity in receipts:
        return identity
    path = Path(run_dir) / 'resolver' / (identity + '.json')
    record = {'stage': 'resolver', 'role': 'resolver', 'engine': 'runner', 'runner_owned': True,
              'iteration': state['iteration'], 'finished_at': support.now(), 'exit_code': 0,
              'runner_calls': 0, 'output': str(path), 'summary': detail,
              'metrics': {'provider_tokens': {'input_tokens': 0, 'output_tokens': 0}},
              'decision': {'action': action, 'rationale': detail},
              'receipt': {'id': identity, 'callbacks_used': [],
                           'scope': 'operational_diagnostic', 'evidence': evidence,
                           'evaluation_binding': binding}}
    support.atomic_json(path, record)
    state.setdefault('stages', []).append(record)
    state.setdefault('history', []).append(record)
    receipts[identity] = copy.deepcopy(record)
    saved.setdefault('operational_diagnostics', []).append(identity)
    return identity


def observe_operational_recovery(runner, state, run_dir, workspace, recovery):
    """Own a runner-established recovery diagnosis, without expanding authority."""
    if _operational_blocked(state, run_dir):
        return False
    histories = ('automatic_timeout_recoveries', 'automatic_capacity_recoveries',
                 'automatic_permission_recoveries')
    if not any(recovery in state.get(name, []) for name in histories):
        return False
    try:
        origin = next(row for row in reversed(state['stages'])
                      if row.get('events') == recovery.get('events') and not row.get('runner_owned'))
        if not all(origin.get(key) for key in ('accounted', 'automatic_recovery', 'abandoned', 'rejected')):
            return False
        runner.assert_stage_stopped(origin)
        paths = [Path(origin[key]) for key in ('events', 'before_ref', 'after_ref')]
        if not all(path.is_file() for path in paths):
            return False
        pins = {str(path): support.file_hash(path) for path in paths}
    except (KeyError, TypeError, ValueError, OSError, StopIteration, support.Paused):
        return False
    instruction = (OPERATIONAL_INSTRUCTION if recovery.get('stage') in REVIEW_STAGES else
                   'AutoResolver retained the stopped attempt and its diagnosis. Continue only '
                   'through the existing bounded recovery route; preserve requirements, permissions, '
                   'failure counts, independent validation and retained partial work.')
    return _operational_receipt(state, run_dir, 'retry', instruction,
                                 {'origin_output': origin['output'], 'pins': pins,
                                  'source_revision': recovery.get('source_revision'),
                                  'next_stage': recovery.get('next_stage'),
                                  'timeout_kind': recovery.get('timeout_kind'),
                                  'timeout_reason': recovery.get('timeout_reason'),
                                  'capacity_error': recovery.get('capacity_error')})


def record_operational_exhaustion(runner, state, run_dir, error, *, request=None):
    """Retain exhaustion and stage a resolver-owned, request-only escalation.

    ``request`` replaces the generic question only for a stop the runner diagnosed itself
    (autocode_validation_rounds). The runner composes it from its own records, which may quote
    saved rejection reasons; no model proposes or edits it.
    """
    if error.status == 'PAUSED_BUILDER_RETRY_LIMIT' and recovery.known_builder_pause(state):
        return False
    if progressive.retained_review_budget_pause(state, error.status):
        return False
    if (error.status not in ('PAUSED_RESOLVER_OPERATIONAL', 'PAUSED_TIMEOUT_RECOVERY',
                             'PAUSED_PROVIDER_CAPACITY', 'PAUSED_PLANNING_BUDGET', 'PAUSED_RATE_LIMIT',
                             'PAUSED_TIME_LIMIT', 'PAUSED_ITERATION_LIMIT', 'PAUSED_BUDGET',
                             'PAUSED_REPORT_REPAIR_LIMIT',
                             'PAUSED_REPEATED_FAILURE', 'PAUSED_RESOLVER',
                             'PAUSED_ORCHESTRATOR_WORKER', 'PAUSED_BUILDER_RETRY_LIMIT',
                             'PAUSED_MILESTONE_STALLED', 'PAUSED_MILESTONE_BUDGET',
                             'PAUSED_MILESTONE_TIME_LIMIT',
                             'PAUSED_PROVIDER_UNCERTAIN', 'PAUSED_UNCERTAIN_STAGE', 'PAUSED_WORKSPACE_BUSY',
                             'PAUSED_NO_PROGRESS')
            or state.get('pending_questions')
            or (Path(run_dir) / 'pause-requested').exists()):
        return False
    pending = state.get('user_request') or (state.get('agent_request') or {}).get('request')
    if pending and pending.get('kind') != 'none':
        return False
    kind = {'PAUSED_ITERATION_LIMIT': 'iteration_ceiling', 'PAUSED_TIME_LIMIT': 'max_seconds',
            'PAUSED_PLANNING_BUDGET': 'planning_review_call_limit',
            'PAUSED_MILESTONE_TIME_LIMIT': 'milestone_max_seconds',
            'PAUSED_NO_PROGRESS': 'no_progress_batches'}.get(error.status)
    settings = state.get('settings', {})
    origin = settings.get('budget_origins', {}).get(kind, 'unknown')
    limit = settings.get('limits', {}).get(kind)
    if kind == 'planning_review_call_limit':
        planning = state.get('planning', {})
        origin = planning.get('review_call_limit_origin', 'runner_default' if 'review_call_limit' not in planning else 'unknown')
        limit = planning.get('review_call_limit', 2)
    elif kind == 'milestone_max_seconds':
        limit = settings.get('milestone_checkpoints', {}).get('max_seconds')
    category = ('no_progress' if error.status == 'PAUSED_NO_PROGRESS' else
                'internal_default' if origin in ('runner_default', 'resolver_delegated') else
                'explicit_user_cap' if origin == 'user_explicit' else 'protected_saved_limit') if kind else (
                'provider_or_spending_guard' if error.status in ('PAUSED_BUDGET', 'PAUSED_RATE_LIMIT') else 'operational_recovery')
    budget = {'category': category, 'kind': kind, 'origin': origin, 'limit': limit}
    receipt = _operational_receipt(state, run_dir, 'hold',
        'AutoResolver cannot safely resolve this blocker under the current authority. ' + str(error), {
        'status': error.status, 'stage': state.get('next_stage'), 'budget': budget,
        'astra_calls': state.get('planning', {}).get('astra_calls'),
        'recovery_calls_used': state.get('planning', {}).get('recovery_review_calls_used', 0)})
    attempts = sum(len(state.get(name, [])) for name in ('automatic_timeout_recoveries', 'automatic_capacity_recoveries',
                                                       'automatic_permission_recoveries')) + sum(bool(r.get('startup_recovery')) for r in state.get('stages', []))
    decision = (f'AutoResolver could not resolve {category} after {attempts} recorded operational recoveries. '
                'Provide corrective information or leave the run paused.')
    options = ['Provide corrective information', 'Leave paused']
    count = (runner.recovery_count(state) if callable(getattr(runner, 'recovery_count', None))
             else attempts)
    maximum = int(getattr(runner, 'MAX_AUTOMATIC_RECOVERIES', 3) or 3)
    # Advice and grant eligibility share one check (#288): never name a command
    # the CLI will refuse at this stop.
    allow_grant = recovery_grants.eligible(
        state, current_request=human.current, count=count, maximum=maximum,
        issued={'scope': 'operational_exhaustion', 'request_id': None}, cause=error.status)
    if allow_grant:
        decision += ' ' + recovery_limits.GRANT_ADVICE
        options.append('Authorize more recoveries with --grant-recovery N')
    else:
        active = state.get('active_stage') or {}
        attempt = (f"{active['iteration']:03d}/{Path(active['output']).stem}"
                   if active.get('output') and isinstance(active.get('iteration'), int) else None)
        decision += ' ' + recovery_limits.advice(allow_grant=False, pause_status=error.status,
                                                 attempt=attempt)
        if attempt:
            options.append(f'Abandon the uncertain attempt with --abandon-stage {attempt}')
    request = request or {'kind': 'blocker', 'discovered': str(error),
                          'impact': 'AutoResolver retained the attempts, work and evidence but cannot continue safely.',
                          'decision_needed': decision,
                          'options': options,
                          'proposed_delta': 'Answering does not authorize a retry, approval, permission or budget change.'}
    human.queue(state, 'operational_exhaustion',
                {'stage': state.get('next_stage') or 'operational_recovery', 'pause_status': error.status, 'budget': budget},
                request=request, evidence={'resolver_receipt_id': receipt}, next_stage=state.get('next_stage'))
    # Keep the printed stop reason on the same contract as the published request, after the cause it
    # stops for (an external_directory denial, a spent budget), which the advice alone does not name.
    cause = str(error).strip()
    advice = request.get('decision_needed') or decision
    state['stop_reason'] = advice if not cause or cause in advice else (
        cause + ('' if cause.endswith('.') else '.') + ' ' + advice)
    return True


def plain(value):
    if is_dataclass(value):
        return {field.name: plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(item) for item in value]
    return value


_DECISION_FIELDS = frozenset(f.name for f in fields(policy.Decision))
_RECEIPT_FIELDS = frozenset(f.name for f in fields(policy.Receipt)) - {'version'}
_OPTIONAL_STR = lambda value: value is None or isinstance(value, str)
_OPTIONAL_MAPPING = lambda value: value is None or isinstance(value, Mapping)


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _validate_decision_dict(raw):
    """Reject a malformed or reinterpreted Decision before construction.

    A dataclass constructor does not check runtime types: ``Decision(**raw)``
    would happily accept a non-string action or a list-valued payload. Every
    field here is authority-bearing (it drives dispatch), so each one is
    checked explicitly rather than trusted to the constructor.
    """
    _require(isinstance(raw, Mapping) and set(raw) == _DECISION_FIELDS, 'malformed decision fields')
    _require(isinstance(raw['action'], str) and raw['action'] in policy.ACTIONS, 'invalid decision action')
    _require(isinstance(raw['payload'], Mapping), 'invalid decision payload')
    _require(isinstance(raw['rationale'], str) and isinstance(raw['in_scope_reason'], str), 'invalid decision rationale')
    return policy.Decision(**raw)


def _validate_receipt_dict(raw):
    """Reject a malformed, retyped or unsupported-version Receipt before construction."""
    _require(isinstance(raw, Mapping), 'malformed receipt')
    version = raw.get('version', 1)
    _require(type(version) is int and version in policy.SUPPORTED_RECEIPT_VERSIONS, 'unsupported receipt version')
    present = set(raw) - {'version'}
    _require(present == _RECEIPT_FIELDS, 'malformed receipt fields')
    _require(isinstance(raw['blocker_id'], str) and isinstance(raw['blocker_digest'], str), 'invalid receipt identity')
    _require(isinstance(raw['classifier_outcome'], str) and isinstance(raw['rationale'], str)
              and isinstance(raw['in_scope_reason'], str), 'invalid receipt narrative fields')
    _require(_OPTIONAL_STR(raw['budget_key']) and _OPTIONAL_STR(raw['idempotency_key']), 'invalid receipt budget/idempotency key')
    _require(raw['attempt'] is None or (type(raw['attempt']) is int and raw['attempt'] >= 0), 'invalid receipt attempt')
    _require(isinstance(raw['action'], str) and raw['action'] in policy.ACTIONS, 'invalid receipt action')
    _require(_OPTIONAL_STR(raw['proposal_rationale']) and _OPTIONAL_STR(raw['reviewer_rationale']), 'invalid receipt review narrative')
    _require(raw['review_verdict'] in (None, 'approved', 'vetoed'), 'invalid receipt review verdict')
    # The type must be exactly bool: a list-valued callbacks_used would pass a
    # bare isinstance/truthiness check but is not the boolean the policy emits.
    _require(type(raw['callbacks_used']) is bool, 'invalid receipt callbacks_used type')
    _require(_OPTIONAL_MAPPING(raw['prior_lineage']) and _OPTIONAL_MAPPING(raw['new_lineage']), 'invalid receipt lineage')
    return policy.Receipt(**{**raw, 'version': version})


def load_ledger(saved):
    return policy.Ledger(
        attempts=dict(saved.get('attempts', {})), outcomes=dict(saved.get('outcomes', {})),
        cache={key: (_validate_decision_dict(pair[0]), _validate_receipt_dict(pair[1]))
               for key, pair in saved.get('cache', {}).items()})


def _evaluate(state, run_dir, blocker, context, evidence, boundaries, proposal):
    """Resolve one request against the durable ledger and record its receipt.

    Shared by every admission and completion path so budget, idempotency and
    receipt recording behave identically whether the proposal is entirely
    runner-owned (report repair, blocked validation, user boundary, diagnosis
    admission) or was derived from a model's diagnosis (diagnosis completion).
    """
    contract = state['goal_contract']
    snapshot = policy.ContractSnapshot(**{key: contract[key] for key in
        ('task_id', 'revision', 'hash', 'body', 'approval_status', 'approval_event')}, origin=contract.get('origin') or '')
    saved = state.setdefault('resolver', {})
    try:
        ledger = load_ledger(saved)
    except (KeyError, IndexError, TypeError, ValueError, AttributeError) as error:
        raise support.Paused('PAUSED_RESOLVER_STATE', 'Saved resolver ledger is malformed; reconcile before retry') from error
    started = time.monotonic()
    decision, receipt = policy.resolve(policy.ResolverRequest(
        blocker, snapshot, context, evidence, boundaries, ledger, proposed_resolution=proposal, clock=support.now))
    saved.update(attempts=ledger.attempts, outcomes=ledger.outcomes,
                 cache={key: plain(pair) for key, pair in ledger.cache.items()})
    # Fallbacks have no policy cache key; make their runner records idempotent too.
    outcome_key = receipt.idempotency_key or support.digest({'blocker': plain(blocker), 'context': context,
                                                           'contract_hash': contract['hash']})
    recorded = saved.setdefault('recorded', [])
    if outcome_key not in recorded:
        path = Path(run_dir) / 'resolver' / (outcome_key + '.json')
        record = {'stage': 'resolver', 'role': 'resolver', 'engine': 'runner', 'runner_owned': True,
                  'iteration': state['iteration'], 'finished_at': support.now(), 'exit_code': 0,
                  'duration_seconds': time.monotonic() - started, 'runner_calls': 0,
                  'metrics': {'provider_tokens': {'input_tokens': 0, 'output_tokens': 0}},
                  'output': str(path), 'summary': decision.rationale,
                  'decision': plain(decision), 'receipt': plain(receipt)}
        support.atomic_json(path, record)
        state.setdefault('stages', []).append(record)
        state.setdefault('history', []).append(record)
        recorded.append(outcome_key)
    # A malformed proposal can yield the policy's diagnostic "retry" outcome;
    # only a validated, runner-proposed repair may authorize dispatch.
    accepted = (receipt.in_scope_reason == 'proposal within boundaries'
                and decision.action == proposal.action)
    return decision, receipt, accepted


def report_repair_identity(original):
    """Identify the runner's original execution, never a repair draft or error."""
    attempt = original.get('failure_attempt')
    if not attempt:
        # Older saved records may lack failure_attempt. Archival retains the
        # original path mapping; interpreting it needs no filesystem access.
        output = original.get('output')
        output = next((old for old, archived in original.get('archived_paths', {}).items()
                       if archived == output), output)
        attempt = f"{original.get('iteration')}:{original['stage']}:{output}"
    return {'stage': original['stage'], 'artifact_hash': original.get('source_revision'),
            'task_id': original.get('task_id'), 'execution': attempt,
            'started_at': original.get('started_at')}


def _report_repair_blocker(state, original):
    selected = report_repair_identity(original)
    stages = state.get('stages', [])
    # Recover the original error key from its execution, not a mutated pending
    # error. The execution-scoped identity itself never depends on that key.
    saved_original = next((row for row in stages if not row.get('runner_owned') and not row.get('report_only')
                           and report_repair_identity(row) == selected), None)
    original = saved_original or original
    legacy = {'stage': original['stage'], 'artifact_hash': original.get('source_revision'),
              'failure_key': original.get('failure_key')}
    legacy_id = support.digest(legacy)
    contract = state['goal_contract']
    lineage = {key: contract[key] for key in ('task_id', 'revision', 'hash')}
    budget = support.digest({'blocker_id': legacy_id, **lineage})
    try:
        ledger = load_ledger(state.get('resolver', {}))
        used = ledger.attempts.get(budget, 0)
        _require(type(used) is int and used >= 0, 'invalid legacy attempt count')
        if saved_original is None:
            # Without the execution row its current error key may be a reclassification.
            # Only exclude charges proved to belong elsewhere; an opaque retained
            # budget cannot be treated as unspent merely because its old key is lost.
            accounted = {support.digest({'blocker_id': support.digest(selected), **lineage})}
            for key, failure in (state.get('failure_history') or {}).items():
                recorded = failure.get('identity') or {}
                if key != support.digest(recorded):
                    continue
                identity = {'stage': recorded.get('stage'), 'artifact_hash': recorded.get('artifact_hash'),
                            'failure_key': key}
                if (identity['stage'], identity['artifact_hash']) != (selected['stage'], selected['artifact_hash']):
                    accounted.add(support.digest({'blocker_id': support.digest(identity), **lineage}))
            for _, receipt in ledger.cache.values():
                if (receipt.prior_lineage and receipt.prior_lineage != lineage
                        and receipt.budget_key == support.digest({'blocker_id': receipt.blocker_id,
                                                                **receipt.prior_lineage})):
                    accounted.add(receipt.budget_key)
            _require(all(type(count) is int and count >= 0 for count in ledger.attempts.values()),
                     'invalid attempt count')
            if any(count and key not in accounted for key, count in ledger.attempts.items()):
                raise support.Paused('PAUSED_RESOLVER_STATE',
                    'Cannot attribute saved legacy report-repair charges; reconcile original execution history before retry')
    except (KeyError, IndexError, TypeError, ValueError, AttributeError) as error:
        raise support.Paused('PAUSED_RESOLVER_STATE', 'Saved resolver ledger is malformed; reconcile before retry') from error
    cached = {key: receipt for key, (_, receipt) in ledger.cache.items()
              if receipt.budget_key == budget and receipt.blocker_id == legacy_id
              and receipt.prior_lineage == lineage and receipt.attempt is not None}
    owner, found, attributed = None, False, set()
    for row in stages:
        if not row.get('runner_owned') and not row.get('report_only'):
            owner = report_repair_identity(row)
            found = found or owner == selected
        receipt = row.get('receipt') or {}
        # Keep an already-spent legacy incident on its original ledger/cache.
        # An exhausted fallback has attempt=None: it cannot bind a later
        # original to the earlier original's spent allowance.
        if (owner and row.get('runner_owned') and receipt.get('blocker_id') == legacy_id
                and receipt.get('attempt') is not None and receipt.get('prior_lineage') == lineage):
            if owner == selected:
                return legacy
            key = receipt.get('idempotency_key')
            if (owner['stage'] == selected['stage'] and owner['artifact_hash'] == selected['artifact_hash']
                    and key in cached and {'version': 1, **receipt} == plain(cached[key])):
                attributed.add(key)
    if used and (not found or not cached or set(cached) - attributed
                 or len({receipt.attempt for receipt in cached.values() if 1 <= receipt.attempt <= used}) != used):
        # The legacy ledger did not encode its execution owner. Missing display
        # rows cannot renew it; reconstruct the binding before automatic retry.
        raise support.Paused('PAUSED_RESOLVER_STATE',
                             'Cannot attribute saved legacy report-repair charges; reconcile original execution history before retry')
    return selected


def boundary(runner, state, run_dir, workspace):
    """Record one deterministic decision at a stopped, approved stage boundary."""
    if human.current(state):
        return True
    if (state.get('active_stage') or state.get('version', 2) < 3
            or state.get('status') not in ('RUNNING', 'WAITING_FOR_USER')
            or not goals.approved(state)):
        return False
    pending = state.get('pending_report_repair')
    request = state.get('user_request') or (state.get('agent_request') or {}).get('request')
    validation = state.get('validation') or {}
    failed = next((row for row in reversed(state.get('stages', [])) if not row.get('runner_owned')), None)
    if request and request.get('kind') != 'none':
        # The legacy 'blocker' category has no typed risk/permission boundary.
        # Never treat an agent's "no scope change" prose as authorization.
        kind = request.get('kind', 'unknown')
        description = request.get('decision_needed') or 'Unclassified user decision'
        evidence = []
        selected = {'stage': state.get('next_stage'), 'request': request,
                    'contract_hash': state['goal_contract']['hash']}
        proposal = policy.Proposal('escalate', {'reason': description}, 'User decision boundary')
    elif pending:
        failed = pending['original']
        if (support.snapshot(workspace)['revision'] != failed.get('source_revision')
                or pending.get('contract_hash') != state['goal_contract']['hash']
                or any(not Path(path).is_file() or support.file_hash(path) != digest
                       for path, digest in pending.get('pins', {}).items())):
            raise support.Paused('PAUSED_STALE_VALIDATION', 'Saved report-repair inputs changed; do not resolve or retry')
        kind, description = 'model_output', pending.get('error') or failed.get('rejection_reason', 'Invalid stage report')
        evidence = [failed[key] for key in ('output', 'events') if failed.get(key)]
        selected = _report_repair_blocker(state, failed)
        proposal = policy.Proposal('retry', {'guidance': 'Use only the existing bounded report-repair path; preserve original execution evidence.'},
                                   'Terminal report failure eligible for report-only repair')
    elif (validation.get('verdict') == 'BLOCKED' and failed
          and validation.get('output') == failed.get('output')):
        if (not Path(validation['output']).is_file()
                or support.snapshot(workspace)['revision'] != failed.get('source_revision')):
            raise support.Paused('PAUSED_STALE_VALIDATION', 'Failed validation artifact changed before resolution')
        kind, description = 'validation', 'Independent validation could not complete'
        evidence = [validation['output']]
        failures.record(state, failed, support.Paused('VALIDATION_BLOCKED', description), support.now())
        selected = {'stage': failed['stage'], 'artifact_hash': failed.get('source_revision'),
                    'failure_key': failed.get('failure_key')}
        proposal = policy.Proposal('continue', {'guidance': 'Preserve failed evidence and continue the existing approved repair or review route.'},
                                   'Existing workflow routing handles failed validation')
    else:
        return False

    repeated = failures.repeated(state, failed) if failed and not request else None
    exhausted = bool(pending and pending['attempts'] >= runner.repair_limit(state))
    if repeated or exhausted:
        proposal = policy.Proposal('escalate', {'reason': 'Persisted failure identity or report-repair budget exhausted'},
                                   'Existing failure and repair limits take precedence')
    context = {'next_stage': state.get('next_stage'),
               'report_attempts': pending['attempts'] if pending else None,
               'evidence_attempt': failed.get('output') if failed else None,
               'failure_count': repeated['count'] if repeated else None}
    decision, receipt, accepted = _evaluate(
        state, run_dir, policy.Blocker(support.digest(selected), kind, description, tuple(evidence)),
        context, evidence, policy.Boundaries(frozenset({'continue', 'retry', 'escalate'}), frozenset()), proposal)
    if decision.action == 'escalate' or not accepted:
        if request:
            # Preserve the actual pending question and exact approval boundary.
            pass
        else:
            status = 'PAUSED_REPEATED_FAILURE' if repeated else 'PAUSED_RESOLVER'
            state.update(status=status, phase='PAUSED_OR_BLOCKED', stop_reason=decision.rationale)
            runner.write_json(Path(run_dir) / 'state.json', state)
            raise support.Paused(status, decision.rationale)
    runner.write_json(Path(run_dir) / 'state.json', state)
    return True


def reset_for_resume(state):
    """Clear the resolver's per-incident attempt budget for an explicit resume.

    The per-blocker attempt count is a current-cycle allowance, not a lifetime
    cap: --resume-paused clears it so an operator can retry after fixing the
    underlying cause. That clearing must not be silent. Each reset records how
    many policy evaluations it erased and folds them into a lifetime total.
    Provider diagnostic reservations are separate and are never reset here.
    """
    saved = state.get('resolver')
    if not isinstance(saved, dict):
        return
    prior = {key: value for key, value in saved.get('attempts', {}).items() if isinstance(value, int)}
    saved['attempts'] = {}
    if not prior:
        return
    total = sum(prior.values())
    saved['lifetime_attempts'] = saved.get('lifetime_attempts', 0) + total
    state.setdefault('user_events', []).append({
        'kind': 'resolver_resume_epoch', 'actor': 'user_cli', 'at': support.now(),
        'cleared_attempts': prior, 'cleared_total': total, 'lifetime_attempts': saved['lifetime_attempts']})


# Operational diagnosis: a bounded, read-only model diagnosis for a repeated
# in-scope Builder (implementation) failure that the deterministic report-repair
# route cannot resolve. This is deliberately narrower than the trigger matrix's
# full "repeated failure" row: there is no automatic runner heuristic yet (the
# exact automatic trigger is an open design question, left for a later
# decision), so the route lands operator-triggered only, and only for the one
# well-understood case -- a repeated (3x) Builder report rejection with a
# report-repair-eligible identity. This is deliberately NOT "report-repair is
# exhausted": whether repair is exhausted is only checked by reject_completed_
# stage at the moment PAUSED_REPEATED_FAILURE first fires, and that check
# reads pending_report_repair['attempts'], which --resume-paused's own
# reset_report_repair_for_resume zeroes on every explicit resume (after this
# route's admission check, but admission does not read it). There is currently
# no marker of exhaustion that survives that reset, so admission enforces
# repetition count only; do not describe or rely on it as an exhaustion check
# until one exists. A
# reviewer's own REWORK verdict keeps using the existing, unrelated
# astra_resolve route.
DIAGNOSIS_BOUNDARIES = policy.Boundaries(frozenset({'retry', 'escalate'}), frozenset())
DIAGNOSTIC_CALL_DEFAULTS = {'max_calls_per_run': 2}


def diagnostic_call_limit(state):
    limit = state.get('settings', {}).get('operational_diagnosis', {}).get(
        'max_calls_per_run', DIAGNOSTIC_CALL_DEFAULTS['max_calls_per_run'])
    if type(limit) is not int or not 0 <= limit <= 8:
        raise ValueError('operational_diagnosis.max_calls_per_run must be an integer from 0 to 8')
    return limit


def diagnostic_calls_used(state):
    """Preserve old reservations, including concrete attempts the old counter missed."""
    saved = state.get('resolver', {})
    _require(isinstance(saved, Mapping), 'invalid diagnostic ledger')
    calls = saved.get('diagnostic_calls', 0)
    _require(type(calls) is int and calls >= 0, 'invalid diagnostic call count')
    if 'diagnostic_reservations' in saved:
        reservations = saved['diagnostic_reservations']
        legacy = saved.get('diagnostic_legacy_calls')
        _require(type(legacy) is int and legacy >= 0, 'invalid legacy diagnostic call count')
        _require(isinstance(reservations, list) and all(isinstance(value, str) and value for value in reservations)
                 and len(reservations) == len(set(reservations)), 'invalid diagnostic reservations')
        return max(calls, legacy + len(reservations))
    watermark = saved.get('diagnostic_dispatch_charged_through', 0)
    _require(type(watermark) is int and watermark >= 0, 'invalid legacy diagnostic watermark')
    # The shipped watermark was iteration-local and can undercount. Existing
    # rows establish a lower bound, not an identity for any future dispatch.
    records = [row for row in state.get('stages', [])
               if row.get('stage') == 'astra_diagnose' and not row.get('dry_run')]
    active = state.get('active_stage')
    if (active and active.get('stage') == 'astra_diagnose'
            and not any(row.get('output') == active.get('output') for row in records)):
        records.append(active)
    return max(calls, watermark, len(records))


def check_diagnostic_capacity(runner, state, run_dir):
    """Refuse a known budget hold before creating provider-request artifacts."""
    limit = diagnostic_call_limit(state)
    used = diagnostic_calls_used(state)
    if used >= limit:
        reason = f'Operational diagnostic budget exhausted for this run ({used}/{limit})'
        state.setdefault('resolver', {})['diagnostic_calls'] = used
        state.update(status='PAUSED_REPEATED_FAILURE', phase='PAUSED_OR_BLOCKED', stop_reason=reason)
        runner.write_json(Path(run_dir) / 'state.json', state)
        raise support.Paused('PAUSED_REPEATED_FAILURE', reason)
    return used


def charge_diagnostic_dispatch(runner, state, run_dir, workspace, record):
    """Reserve one provider attempt at run_role's final admission boundary.

    The caller persists this mutation AND active_stage in one checkpoint before
    Popen. A crash after that checkpoint is uncertain, never refunded or replayed:
    existing active-stage reconciliation owns it. Rechecking the same saved
    record is idempotent; a replacement gets a new UUID, regardless of iteration,
    blocker, or archive length. This helper must not persist a charge on its own.

    Report-only formatting repair is a separate existing bounded allowance,
    not a fresh diagnostic stage. Its calls still consume elapsed time and record token usage.
    """
    if record.get('stage') != 'astra_diagnose' or record.get('report_only') or record.get('dry_run'):
        return
    request = state.get('diagnosis_request') or {}
    if (request.get('contract_hash') != state['goal_contract']['hash']
            or request.get('source_revision') != support.snapshot(workspace)['revision']):
        raise support.Paused('PAUSED_STALE_HANDOFF', 'Diagnosis needs the current source and approved contract')
    for path, digest in request.get('evidence_hashes', {}).items():
        if not Path(path).is_file() or support.file_hash(path) != digest:
            raise support.Paused('PAUSED_STALE_HANDOFF', 'Diagnosis evidence changed; reconcile before diagnosing')
    saved = state.setdefault('resolver', {})
    diagnostic_call_limit(state)
    diagnostic_calls_used(state)
    reservation = record.get('diagnostic_reservation_id')
    if reservation and reservation in saved.get('diagnostic_reservations', []):
        return
    diagnostic_calls = check_diagnostic_capacity(runner, state, run_dir)
    recovery.admit_dispatch(state, record, workspace, run_dir)
    if 'diagnostic_reservations' not in saved:
        saved['diagnostic_legacy_calls'] = diagnostic_calls
        saved['diagnostic_reservations'] = []
    reservation = uuid.uuid4().hex
    record['diagnostic_reservation_id'] = reservation
    saved['diagnostic_reservations'].append(reservation)
    saved['diagnostic_calls'] = diagnostic_calls + 1


def admit_operational_diagnosis(runner, state, run_dir, workspace):
    """Explicitly admit one bounded, read-only diagnosis for a repeated Builder failure.

    Requires an unchanged, thrice-repeated Builder (terra) report rejection
    with a report-repair-eligible identity -- the same identity
    ``--retry-failed-stage`` inspects, but instead of blindly retrying it
    routes through a model diagnosis whose recommendation the runner
    independently validates, through the same bounded policy, before any
    retry is authorized. Persists its own outcome durably (like ``boundary``),
    since a later step in the same explicit-resume pass could still raise.

    This does NOT itself verify that report-repair's own attempt budget is
    exhausted: the repeated-failure count is the enforced gate. Repetition
    can stop the repair path while that path still has attempts remaining.
    """
    if state.get('status') != 'PAUSED_REPEATED_FAILURE':
        raise ValueError('Diagnosis requires a run paused for repeated failure')
    pending = state.get('pending_report_repair') or {}
    record = pending.get('original')
    if not record:
        raise ValueError('Operational diagnosis requires a report-repair-eligible identity; use --retry-failed-stage instead')
    if record.get('role') != 'terra' or record.get('stage') != 'terra':
        raise ValueError('Operational diagnosis is scoped to a repeated Builder (terra) failure in this release')
    repeated = failures.repeated(state, record)
    if not repeated:
        raise ValueError('No unchanged repeated failure to diagnose; fix the cause, then resume')
    selected = {'stage': record['stage'], 'artifact_hash': record.get('source_revision'), 'failure_key': record.get('failure_key')}
    blocker_id = support.digest(selected)
    description = repeated.get('last_error') or 'Repeated Builder report rejection'
    evidence = [record[key] for key in ('output', 'events') if record.get(key)]
    diagnostic_calls = diagnostic_calls_used(state)
    limit = diagnostic_call_limit(state)
    if diagnostic_calls >= limit:
        proposal = policy.Proposal('escalate', {'reason': f'Operational diagnostic budget exhausted for this run ({diagnostic_calls}/{limit})'},
                                   'Run-level diagnostic call limit reached')
    else:
        proposal = policy.Proposal('retry', {'guidance': 'Route this repeated failure to a bounded read-only diagnosis before another blind retry.'},
                                   'Repeated in-scope Builder failure eligible for bounded diagnosis')
    context = {'next_stage': state.get('next_stage'), 'failure_count': repeated['count'],
               'diagnostic_calls_used': diagnostic_calls, 'diagnostic_call_limit': limit}
    blocker = policy.Blocker(blocker_id, 'implementation', description, tuple(evidence))
    decision, receipt, accepted = _evaluate(state, run_dir, blocker, context, evidence, DIAGNOSIS_BOUNDARIES, proposal)
    if decision.action == 'escalate' or not accepted:
        state.update(status='PAUSED_REPEATED_FAILURE', phase='PAUSED_OR_BLOCKED', stop_reason=decision.rationale)
        runner.write_json(Path(run_dir) / 'state.json', state)
        raise support.Paused('PAUSED_REPEATED_FAILURE', decision.rationale)
    # The run-level cap is charged at actual dispatch (charge_diagnostic_dispatch),
    # not here: admission alone does not guarantee astra_diagnose ever launches,
    # and charging here would falsely count an invocation that never happened.
    pins = {record[key]: support.file_hash(record[key]) for key in ('output', 'events')
            if record.get(key) and Path(record[key]).is_file()}
    state['diagnosis_request'] = {
        'contract_hash': state['goal_contract']['hash'], 'source_revision': record.get('source_revision'),
        'original_stage': record['stage'], 'failure_key': selected['failure_key'], 'blocker_id': blocker_id,
        'description': description, 'repeated_count': repeated['count'], 'evidence': evidence, 'evidence_hashes': pins}
    recovery.prepare_diagnosis(state, state['diagnosis_request'], record, run_dir)
    # The stopped report-repair pointer is superseded by the diagnosis; leaving
    # it would make the next dispatch's before_code_stage hook try to execute it
    # against next_stage='astra_diagnose' and pause with PAUSED_STALE_REPORT_ROUTE.
    state.setdefault('report_repair_archive', []).append({
        'at': support.now(), 'reason': 'Superseded by an admitted operational diagnosis',
        'repair': state.pop('pending_report_repair')})
    state.update(status='RUNNING', phase='EXECUTING', next_stage='astra_diagnose')
    state.pop('stop_reason', None)
    runner.write_json(Path(run_dir) / 'state.json', state)


def finish_operational_diagnosis(state, run_dir, recommendation, *, recovery_change=None):
    """Validate a model's diagnosis recommendation against the same bounded
    policy and per-incident budget used to admit the diagnosis (the second
    of that budget's two evaluations), before authorizing any retry.

    Called on the candidate state inside the commit-then-persist boundary
    (like ``queue_resolution``/``finish_resolution``): it mutates ``state``
    only and leaves persistence to that outer boundary. On escalate or a
    rejected recommendation it must NOT raise: this function's own call to
    ``_evaluate`` already mutated the candidate (the spent attempt, the
    receipt, the stage record), and the caller's deep-copy-then-commit
    pattern (``autopilot.apply_result``) discards every candidate mutation
    the moment anything raises out of it. Raising here would silently lose
    the very evaluation this function exists to make durable, and a later
    resume would redispatch astra_diagnose against a ledger that still
    showed only the first (admission) attempt spent -- an uncounted repeat
    of the second evaluation on every such resume. Setting the paused
    status on the candidate and returning is what the drive loop already
    treats as a stop (``active(state)`` is false once status leaves
    RUNNING), so no exception is needed to end the run here.
    """
    request = state['diagnosis_request']
    payload = {'reason': recommendation['rationale']}
    if recommendation.get('guidance', '').strip():
        payload['guidance'] = recommendation['guidance']
    if recommendation.get('evidence_refs'):
        payload['evidence_refs'] = recommendation['evidence_refs']
    proposal = policy.Proposal(recommendation['action'], payload, recommendation['rationale'])
    blocker = policy.Blocker(request['blocker_id'], 'implementation', request['description'], tuple(request['evidence']))
    context = {'next_stage': state.get('next_stage'),
               'diagnostic_calls_used': state.get('resolver', {}).get('diagnostic_calls'),
               'failure_count': request.get('repeated_count')}
    decision, receipt, accepted = _evaluate(state, run_dir, blocker, context, request['evidence'], DIAGNOSIS_BOUNDARIES, proposal)
    if decision.action == 'retry' and accepted:
        failure_key = request.get('failure_key')
        original_stage = request['original_stage']
        state.pop('diagnosis_request', None)
        if failure_key:
            entry = state.get('failure_history', {}).get(failure_key)
            if entry is not None:
                entry.setdefault('diagnostic_retries', []).append({
                    'receipt': receipt.idempotency_key, 'recommendation': copy.deepcopy(recommendation),
                    'recovery_packet': copy.deepcopy(request.get('recovery_packet'))})
        plan = {'kind': 'operational-diagnosis', 'tasks': [copy.deepcopy(state.get('current_task') or {})],
                'recommendation': copy.deepcopy(recommendation)}
        recovery.finish_resolution_packet(state, request, plan)
        if recovery_change:
            plan['recovery_change'] = copy.deepcopy(recovery_change)
        state['repair_plan'] = plan
        state.setdefault('resolution_history', []).append(copy.deepcopy(plan))
        state.update(status='RUNNING', phase='EXECUTING', next_stage=original_stage)
        state.pop('stop_reason', None)
        return True
    state.update(status='PAUSED_REPEATED_FAILURE', phase='PAUSED_OR_BLOCKED', stop_reason=decision.rationale)
    return False
