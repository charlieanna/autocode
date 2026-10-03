"""Evidence-bound first serial repair assignment; ambiguity still goes to the Resolver."""
from __future__ import annotations

import copy
from pathlib import Path

try:
    from . import autocode_util as util, autocode_check_refs as check_refs
except ImportError:
    import autocode_util as util, autocode_check_refs as check_refs


def _require(condition, reason):
    if not condition:
        raise util.Paused('PAUSED_STALE_HANDOFF', reason)


def _run_root(output):
    if not isinstance(output, str) or not output:
        return None
    path = Path(output)
    return next((parent for parent in path.parents if parent.parent.name == 'runs'
                 and parent.parent.parent.name == '.autocode'), None)


def _owned(path, workspace, run_dir, *, artifact=False):
    _require(isinstance(path, str) and bool(path), 'Repair evidence has no file path')
    target = Path(path)
    target = target if target.is_absolute() else workspace / target
    _require('..' not in target.parts and target.is_relative_to(workspace),
             'Repair evidence is outside this workspace')
    _require(not artifact or target.is_relative_to(run_dir), 'Stage evidence belongs to another run')
    _require(not target.is_relative_to(workspace / '.autocode') or target.is_relative_to(run_dir),
             'Repair evidence belongs to another run')
    _require(not any(parent.is_symlink() for parent in (target, *target.parents)
                     if parent.is_relative_to(workspace)), 'Repair evidence traverses a symlink')
    _require(target.is_file(), 'Repair evidence is missing')
    return target


def _report(path):
    try:
        return util.read_object(path)
    except (OSError, RuntimeError) as error:
        raise util.Paused('PAUSED_STALE_HANDOFF', 'Saved repair report is unreadable') from error


def _hash(path):
    try:
        return util.file_hash(path)
    except OSError as error:
        raise util.Paused('PAUSED_STALE_HANDOFF', 'Repair evidence cannot be read') from error


def _seal(record, value, workspace, run_dir):
    fields = ('stage', 'task_id', 'source_revision', 'contract_hash', 'contract_revision')
    binding = {field: record.get(field) for field in fields}
    _require(all(isinstance(binding[field], str) and binding[field] for field in fields[:-1])
             and type(binding['contract_revision']) is int and binding['contract_revision'] > 0,
             'Repair report has incomplete runner provenance')
    _require(all(value.get(field) == binding[field] for field in ('task_id', 'contract_hash', 'contract_revision')),
             'Repair report does not match its runner provenance')
    paths = {key: str(_owned(record[key], workspace, run_dir, artifact=True))
             for key in ('output', 'events', 'reported_output') if record.get(key)}
    _require('output' in paths and 'events' in paths, 'Repair report lacks saved events')
    _require(util.digest(_report(paths['output'])) == util.digest(value),
             'Saved normalized repair report differs from the accepted value')
    return {'version': 1, 'run_dir': str(run_dir), 'binding': binding, 'paths': paths,
            'hashes': {path: _hash(path) for path in paths.values()},
            'report_digest': util.digest(value)}


def _repaired(record):
    return any(record.get(key) for key in ('report_only', 'report_repaired', 'repaired_by', 'applied_original_events'))


def capture(record, value):
    """Seal normalized, unrepaired modern reports at the report-loader boundary."""
    if record.get('stage') not in ('sol', 'astra_review') or _repaired(record):
        return value
    if not isinstance(value, dict) or not all(value.get(key) for key in ('contract_hash', 'contract_revision', 'task_id')):
        _require(not record.get('rework_evidence'), 'A sealed report lost its accepted provenance')
        return value
    root = _run_root(record.get('output'))
    if root is None or not all(record.get(key) for key in ('task_id', 'source_revision', 'contract_hash', 'contract_revision')):
        # Old reports without runner provenance remain usable, but cannot earn this optimization.
        _require(not record.get('rework_evidence'), 'A sealed report lost its runner provenance')
        return value
    workspace = root.parent.parent.parent
    sealed = _seal(record, value, workspace, root)
    if 'rework_evidence' in record:
        _require(record['rework_evidence'] == sealed, 'Sealed repair evidence changed; it cannot be repinned')
    else:
        # Sole writer: capture. Readers: route and report reconciliation through capture.
        record['rework_evidence'] = sealed
    return value


def _verify(record, workspace, run_dir, value=None):
    seal = record.get('rework_evidence')
    if not seal or _repaired(record):
        return None
    _require(run_dir.is_relative_to(workspace / '.autocode' / 'runs'), 'Repair run does not belong to this workspace')
    paths = {key: _owned(record[key], workspace, run_dir, artifact=True)
             for key in ('output', 'events', 'reported_output') if record.get(key)}
    report = _report(paths['output']) if value is None else value
    _require(_seal(record, report, workspace, run_dir) == seal, 'Saved repair seal or evidence changed')
    return report


def _eligible(state, decision, record, previous_criteria, prior_request, pending_human, retry_policy):
    settings = state.get('settings', {})
    body = state['goal_contract']['body']
    task = state.get('current_task') or {}
    spec = decision.get('next_task') or {}
    milestones = body.get('milestones') or []
    stages = state.get('stages', [])
    builders = [row for row in stages if row.get('stage') == 'terra']
    retry = settings.get('builder_retry') or {}
    ordinary = retry.get('ordinary_retries')
    if (state.get('version', 2) < 3 or settings.get('workflow') or state.get('progressive')
            or state.get('parent_run') or state.get('parent_batch') or state.get('orchestration_batch')
            or state.get('orchestration_history') or task.get('milestone_ids') or pending_human or prior_request
            or state.get('direct_rework_assignments') or state.get('resolution_history')
            or state.get('recovery_context') or state.get('automatic_timeout_recoveries')
            or state.get('pending_report_repair') or state.get('report_repair_history') or state.get('builder_retry_decisions')
            or any(row.get('stage') == 'astra_resolve' or _repaired(row)
                   or row.get('timed_out') or row.get('interrupted') for row in stages)
            or any(row.get('needs_replan') for row in state.get('milestone_progress', {}).values())
            or len(builders) != 1 or builders[0].get('rejected') or builders[0].get('batch_id')
            or builders[0].get('task_id') != task.get('id')
            or builders[0].get('exit_code') != 0 or builders[0].get('role') != 'terra'
            or _repaired(record) or record.get('timed_out') or record.get('interrupted')
            or type(record.get('exit_code')) is not int or record['exit_code'] != 0
            or not retry_policy.enabled(state) or type(ordinary) is not int or not 1 <= ordinary <= 3
            or any(lane.get('failures') or lane.get('action') for lane in state.get('builder_retries', {}).values())):
        return False
    if (len(milestones) != 1 or task.get('milestone_id') != milestones[0].get('id')
            or spec.get('milestone_id') != task.get('milestone_id') or spec.get('kind') != 'implement'
            or task.get('kind') != 'implement' or decision.get('user_request', {}).get('kind') != 'none'
            or body.get('open_blocking_questions') or not isinstance(decision.get('next_objective'), str)
            or not decision['next_objective'].strip()):
        return False
    for key in ('requirements', 'acceptance_criteria', 'validation_plan'):
        entries = spec.get(key)
        if not isinstance(entries, list) or not entries or any(not isinstance(item, str) or not item.strip() for item in entries):
            return False
    identity = lambda rows: [{key: value for key, value in row.items() if key not in ('evidence', 'status')} for row in rows]
    reviewed = decision.get('acceptance_criteria', [])
    # A reviewer can report blocked/unverified without changing the criterion.
    # New verified/other statuses still require the ordinary diagnosis path.
    status_safe = all(new.get('status') == old.get('status') or new.get('status') in
                      ('unverified', 'blocked')
                      for old, new in zip(previous_criteria, reviewed))
    approved = body.get('acceptance_criteria') or []
    if (identity(previous_criteria) != identity(reviewed) or not status_safe
            or {row['id']: row['criterion'] for row in previous_criteria} != {row['id']: row['criterion'] for row in approved}
            or spec['acceptance_criteria'] != task.get('acceptance_criteria')
            or spec['acceptance_criteria'] != milestones[0].get('acceptance_criteria')
            or len(set(spec['acceptance_criteria'])) != len(spec['acceptance_criteria'])):
        return False
    paths = decision.get('affected_paths') or []
    if (not paths or len(set(paths)) != len(paths) or set(paths) != set(task.get('affected_paths') or [])
            or set(paths) != set(milestones[0].get('affected_paths') or [])
            or any(not isinstance(path, str) or Path(path).is_absolute() or '..' in Path(path).parts for path in paths)):
        return False
    evidence, findings = decision.get('evidence'), decision.get('findings')
    return (isinstance(evidence, list) and bool(evidence)
            and all(isinstance(item, str) and item.strip() for item in evidence)
            and isinstance(findings, list) and bool(findings)
            and any(row.get('blocking') is True for row in findings)
            and all(isinstance(row.get('evidence'), str) and row['evidence'].strip()
                    and isinstance(row.get('finding'), str) and row['finding'].strip() for row in findings))


def route(runtime, state, decision, record, queue, retry_policy, *, run_dir):
    """Preserve human holds; queue other repairs and replace only a proven first repair."""
    _require(record.get('stage') in (None, 'astra_review') and decision.get('status') == 'REWORK',
             'Repair routing requires a Completion REWORK report')
    task = state.get('current_task') or {}
    previous_criteria = copy.deepcopy(state.get('acceptance_criteria', []))
    prior_request = bool(state.get('resolution_request'))
    # A historical display receipt may remain after domain-level goal approval.
    # Hold actual waiting states, unresolved questions/requests and queued proposals.
    pending_human = (state.get('status') in ('WAITING_FOR_USER', 'AWAITING_GOAL_APPROVAL')
                     or any(state.get(key) for key in ('user_request', 'pending_questions',
                                                       'resolver_human_proposal')))
    if pending_human:
        # queue_resolution normally clears these fields for a new diagnosis.
        # A completed review must not consume a still-unanswered human decision.
        raise util.Paused('PAUSED_RESOLVER',
                          'Resolve the existing human decision before assigning or diagnosing this repair')
    root = Path(run_dir)
    workspace = Path(state['workspace'])
    completion = _verify(record, workspace, root, decision)
    validation = state.get('validation') or {}
    validators = [row for row in state.get('stages', []) if row.get('stage') == 'sol'
                  and not row.get('rejected') and row.get('output') == validation.get('output')]
    accepted = validators[0] if len(validators) == 1 else None
    report = _verify(accepted, workspace, root) if accepted is not None else None
    if report is not None:
        # apply_review_result resolves runner-owned check:<n> aliases after loading.
        # Compare that accepted representation while retaining the original sealed file.
        report = check_refs.resolve(copy.deepcopy(report))
        _require(all(validation.get(key) == value for key, value in report.items()),
                 'Accepted Validator body no longer matches its sealed report')
        _require(all(validation.get(key) == accepted.get(key) for key in
                     ('task_id', 'contract_hash', 'contract_revision', 'source_revision')),
                 'Validator provenance differs from its accepted stage')
    current_validation = all(validation.get(key) == expected for key, expected in (
        ('task_id', task.get('id')), ('contract_hash', state['goal_contract']['hash']),
        ('contract_revision', state['goal_contract']['revision']), ('source_revision', record.get('source_revision'))))
    pins = validation.get('evidence_hashes') or {}
    if report is not None and current_validation:
        for path, digest in pins.items():
            _require(_hash(_owned(path, workspace, root)) == digest, 'Validator evidence changed before repair')
    queue(state, decision, record)
    _require(not any(row.get('source_output') == record.get('output') for row in state.get('direct_rework_assignments', [])),
             'This Completion repair was already assigned; reconcile its existing handoff')
    if (completion is None or report is None or not current_validation or validation.get('verdict') != 'FAIL'
            or not _eligible(state, decision, record, previous_criteria, prior_request, pending_human, retry_policy)):
        return False
    builders = [row for row in state['stages'] if row.get('stage') == 'terra']
    builder = builders[0]
    roles = state['settings'].get('roles', {})
    builder_model = (builder.get('launch_route') or roles.get('terra') or {}).get('model')
    validator_model = (accepted.get('launch_route') or roles.get('sol') or {}).get('model')
    if (accepted.get('role') != 'sol' or validation.get('reviewer_role') != 'sol'
            or accepted.get('changed_files') or accepted.get('report_only')
            or type(accepted.get('exit_code')) is not int or accepted['exit_code'] != 0
            or not all(isinstance(model, str) and model for model in (builder_model, validator_model))
            or builder_model.rsplit('/', 1)[-1] == validator_model.rsplit('/', 1)[-1]
            or (builder.get('thread_id') and builder.get('thread_id') == accepted.get('thread_id'))):
        return False
    failed = [check for check in validation.get('checks', []) if type(check.get('exit_code')) is int and check['exit_code'] != 0]
    if not failed or not pins:
        return False
    for check in failed:
        ref = check.get('evidence_ref') or ''
        if ref.startswith('event:'):
            if accepted['events'] not in pins:
                return False
        else:
            receipt_path = _owned(ref, workspace, root, artifact=True)
            receipt = _report(receipt_path)
            raw = _owned(receipt.get('full_output'), workspace, root, artifact=True)
            if str(receipt_path) not in pins or str(raw) not in pins:
                return False
    try:
        runtime.support.verify_checks(copy.deepcopy(failed), workspace, accepted['events'],
                                      **runtime.check_evidence_options(accepted))
    except ValueError as error:
        raise util.Paused('PAUSED_STALE_HANDOFF', 'Failed check lacks an executed Validator receipt') from error
    current = runtime.support.snapshot(workspace)
    probe = copy.deepcopy(state)
    probe['iteration'] += 1
    try:
        runtime.lifecycle.assign_task(probe, decision, current)
    except ValueError:
        return False  # A valid rejection may still need the Resolver to formulate its task.
    except util.Paused as error:
        if not error.status.startswith('PAUSED_MILESTONE_'):
            raise
    candidate = copy.deepcopy(state)
    candidate['iteration'] += 1
    reason = 'Completion Owner supplied a bounded REWORK assignment: ' + decision['next_objective']
    action = retry_policy.failure(candidate, record['output'], reason)
    _require(action == 'retry', 'Direct repair must not manufacture an escalation allowance')
    try:
        runtime.lifecycle.assign_task(candidate, decision, current)
    except ValueError:
        return False
    except util.Paused as error:
        if not error.status.startswith('PAUSED_MILESTONE_'):
            raise
        runtime.milestones.handle_gate(candidate, error, current,
            origin={'stage': 'astra_review', 'output': record['output']}, ask_user=runtime.lifecycle.wait_for_user)
        runtime.goals.record_decision(candidate, decision)
        state.clear()
        state.update(candidate)
        return True
    runtime.goals.record_decision(candidate, decision)
    request = candidate.pop('resolution_request')
    # Sole writer: route. Readers: route's replay guard and the public run view.
    candidate.setdefault('direct_rework_assignments', []).append({
        'source_task_id': task['id'], 'assigned_task_id': candidate['current_task']['id'],
        'contract_hash': state['goal_contract']['hash'], 'contract_revision': state['goal_contract']['revision'],
        'source_revision': record['source_revision'], 'source_output': record['output'],
        'source_stage': 'astra_review', 'provenance': 'completion_direct_assignment',
        'report_digest': record['rework_evidence']['report_digest'],
        'evidence_hashes': {**request['evidence_hashes'], **accepted['rework_evidence']['hashes']},
        'retry_charge': {'action': action, 'evidence': record['output'], 'iteration': candidate['iteration']},
        'reason': reason, 'assigned_at': util.now(),
    })
    candidate.pop('agent_request', None)
    candidate.update(status='RUNNING', phase='EXECUTING', next_action=decision['next_objective'],
                     next_stage=runtime.dispatch.build_stage(candidate))
    state.clear()
    state.update(candidate)
    return True
