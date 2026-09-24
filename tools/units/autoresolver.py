"""Read-only diagnosis and bounded repair planning; never writes application code."""
import copy
import json
from pathlib import Path
try:
    from .. import autocode_support as support, autocode_goals as goals
except ImportError:
    import autocode_support as support
    import autocode_goals as goals
from .common import ModelRequest, execution_request





def guard(state, workspace):
    goals.execution_guard(state)
    request = state.get('resolution_request') or {}
    if (request.get('contract_hash') != state['goal_contract']['hash']
            or request.get('task_id') != state.get('current_task', {}).get('id')
            or request.get('source_revision') != support.snapshot(workspace)['revision']):
        raise support.Paused('PAUSED_STALE_HANDOFF', 'Repair diagnosis needs the current reviewed source and task')
    for path, digest in request.get('evidence_hashes', {}).items():
        if not Path(path).is_file() or support.file_hash(path) != digest:
            raise support.Paused('PAUSED_STALE_HANDOFF', 'Repair evidence changed; review again before resolving')


def prepare(state, stage, state_path, schema_dir):
    if stage != 'astra_resolve':
        raise ValueError(f'Autoresolver cannot run {stage}')
    guard(state, Path(state['workspace']))
    # Inherit the planner model, not its conversation. A dedicated route keeps
    # resolver sessions separate from planning, completion and implementation.
    state['settings']['roles'].setdefault('resolver', copy.deepcopy(state['settings']['roles']['astra']))
    request = execution_request(state, 'astra_review', state_path, schema_dir)
    instruction, payload = request.prompt.split('CURRENT HANDOFF DATA\n', 1)
    data = json.loads(payload)
    data.update(stage=stage, resolution_request=state['resolution_request'],
                execution_engine=state['settings']['roles']['resolver'].get('engine', state['settings'].get('engine', 'codex')))
    schema = copy.deepcopy(request.schema)
    schema['properties']['status']['enum'] = ['REWORK', 'BLOCKED']
    schema['properties']['diagnosis'] = goals.STRING
    schema['required'].append('diagnosis')
    prompt = ('You are AUTORESOLVER, a read-only failure diagnostician, not a Builder or completion owner. '
              'Inspect the rejected build, review findings and exact evidence. Return a nonempty diagnosis '
              'and one bounded REWORK next_task with defect evidence and concrete validation_plan retests. '
              'The runner exports this task as a one-node repair DAG. Preserve the whole integrated batch. '
              'Return the complete unchanged acceptance_criteria list from the handoff; select the repair subset only in next_task.acceptance_criteria. '
              'Criterion statuses and evidence remain owned by the reviewer, not the resolver. '
              'Do not approve work, change requirements, weaken tests, or modify source. '
              'If scope or permission must change, return BLOCKED with a structured user_request; never grant it yourself.\n'
              + instruction + '\nResolver constraint overrides completion choices: only REWORK or BLOCKED.\n'
              + 'CURRENT HANDOFF DATA\n' + json.dumps(data, indent=2))
    metrics = {**request.metrics, 'estimated_prompt_tokens': (len(prompt) + 3) // 4}
    return ModelRequest('astra', 'resolver', prompt, metrics, schema, False)


def validate(state, value, record, workspace):
    guard(state, workspace)
    if record.get('changed_files') or record['source_revision'] != state['resolution_request']['source_revision']:
        raise support.Paused('PAUSED_STALE_HANDOFF', 'Resolver must leave the reviewed source unchanged')
    if value.get('status') not in ('REWORK', 'BLOCKED') or not value.get('diagnosis', '').strip():
        raise ValueError('Resolver requires a diagnosis and a REWORK or BLOCKED decision')
    if value['status'] == 'REWORK' and (not value.get('evidence') or value.get('next_task', {}).get('kind') != 'implement'):
        raise ValueError('Resolver must supply an evidence-backed implementation repair')


def preserve_review_criteria(state, value):
    """A focused diagnosis may omit criteria, but cannot redefine or verify them."""
    authoritative = state['acceptance_criteria']
    by_id = {row['id']: row for row in authoritative}
    seen = set()
    for row in value['acceptance_criteria']:
        cid = row['id']
        if cid in seen:
            raise support.Paused('PAUSED_INVALID_OUTPUT', 'Duplicate acceptance IDs')
        seen.add(cid)
        if cid not in by_id or row['criterion'] != by_id[cid]['criterion']:
            raise support.Paused('PAUSED_CRITERIA_CHANGE', 'Repair cannot change approved acceptance criteria')
    # Preserve the last review's order, statuses and evidence, including omitted
    # criteria. Diagnosis supplies repair instructions, not a new review verdict.
    return {**value, 'acceptance_criteria': copy.deepcopy(authoritative)}
