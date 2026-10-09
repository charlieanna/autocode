"""Durable named execution checkpoints; tool activity is never acceptance."""
import copy


def update(state):
    task = state.get('current_task') or {}
    if not task.get('id'):
        return
    active = state.get('active_stage') or {}
    stage = active.get('stage') or state.get('next_stage')
    implementation = state.get('implementation') or {}
    contract = state.get('goal_contract', {}).get('hash')
    implementation = implementation if (implementation.get('task_id') == task['id']
        and implementation.get('contract_hash') == contract) else {}
    validation = state.get('validation') or {}
    validation = validation if (implementation and implementation.get('source_revision')
        and validation.get('task_id') == task['id']
        and validation.get('source_revision') == implementation.get('source_revision')
        and validation.get('contract_hash') == contract
        and not (active and stage == 'terra')) else {}
    paused = state.get('status', '').startswith('PAUSED')
    no_progress = any(r.get('task_id') == task['id'] and r.get('contract_hash') == contract
                      for r in state.get('no_progress_reports', [])) and not implementation
    last_builder = next((r for r in reversed(state.get('stages', []))
                         if r.get('stage') == 'terra' and r.get('task_id') == task['id']), {})
    activity = (active if active and stage == 'terra' else last_builder).get('activity') or {}
    rows = [
        {'id': 'implementation', 'label': 'Implementation',
         'status': 'recorded' if implementation else 'no_progress' if no_progress else 'running' if active and stage == 'terra' else 'pending'},
        {'id': 'builder_activity', 'label': 'Builder tool activity (not independent verification)',
         'status': 'recorded' if implementation else 'running' if active and stage == 'terra' else 'pending',
         'completed_tools': activity.get('completed_tool_count', 0)},
        {'id': 'validation', 'label': 'Independent validation',
         'status': 'verified' if validation.get('verdict') == 'PASS' else 'failed' if validation else 'running' if active and stage == 'sol' else 'pending'},
        {'id': 'review', 'label': 'Completion review',
         'status': 'verified' if state.get('status') == 'TASK_COMPLETE' and validation.get('verdict') == 'PASS' else 'running' if active and stage == 'astra_review' else 'pending'},
    ]
    results = {r['id']: r for r in validation.get('criterion_results', [])}
    definitions = {r['id']: r['criterion'] for r in state.get('acceptance_criteria', [])}
    for cid in task.get('acceptance_criteria', []):
        result = results.get(cid, {})
        rows.append({'id': 'criterion:' + cid, 'label': cid + ': ' + definitions.get(cid, cid),
                     'status': 'verified' if result.get('status') == 'PASS' else 'not_verified',
                     'evidence_refs': result.get('evidence_refs', [])})
    value = {'task_id': task['id'], 'milestone_id': task.get('milestone_id'),
             'contract_hash': state.get('goal_contract', {}).get('hash'),
             'source_revision': implementation.get('source_revision'),
             'paused': paused, 'rows': rows}
    old = state.get('execution_checkpoints')
    if old and old['task_id'] != task['id']:
        state.setdefault('checkpoint_history', []).append(copy.deepcopy(old))
        state['checkpoint_history'] = state['checkpoint_history'][-100:]
    state['execution_checkpoints'] = value
