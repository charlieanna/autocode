"""Conservative, one-revision reuse of accepted serial milestones.

Reuse unlocks scheduling only. Original validation keeps its original lineage
and cannot satisfy the new contract's final integration/completion gate.
"""
import copy
from pathlib import Path

try:
    from . import autocode_support as s
except ImportError:
    import autocode_support as s


def valid_path(value):
    return (isinstance(value, str) and bool(value) and not value.startswith('/')
            and not any(char in value for char in '*?[]\\\x00\n')
            and all(part not in ('', '.', '..', '.git', '.autocode', '.autocode-ui')
                    for part in value.rstrip('/').split('/')))


def footprint(current, roots, changed):
    selected = {path: digest for path, digest in current['files'].items()
                if path in changed or any(path == root.rstrip('/') or path.startswith(root.rstrip('/') + '/') for root in roots)}
    for path in changed:
        selected.setdefault(path, 'deleted')
    if not selected or any(value.startswith(('symlink:', 'submodule:', 'uninitialized-submodule'))
                           for value in selected.values()):
        raise ValueError('Missing source footprint or unsupported link/submodule')
    return selected


def criteria(body, milestone):
    definitions = {row['id']: row for row in body['acceptance_criteria']}
    return {cid: definitions[cid] for cid in milestone['acceptance_criteria']}


def global_scope(body):
    return {key: value for key, value in body.items()
            if key not in ('milestones', 'acceptance_criteria', 'initial_task')}


def intact(pins):
    return bool(pins) and all(Path(path).is_file() and s.file_hash(path) == digest for path, digest in pins.items())


def capture(state, row, current):
    """Capture at actual acceptance; never reconstruct trust from legacy reports."""
    try:
        contract = state['goal_contract']
        milestone = next(m for m in contract['body']['milestones'] if m['id'] == row['id'])
        if row.get('milestone_ids') or row.get('accepted_batch') or row.get('carried_from'):
            raise ValueError('Batch or previously carried milestone requires fresh validation')
        if 'depends_on' not in milestone:
            raise ValueError('Explicit dependency list required')
        definitions = criteria(contract['body'], milestone)
        if any(c['human_review'] for c in definitions.values()):
            raise ValueError('Human artifact approval cannot move to another contract')
        tasks = [task for task in [*state.get('task_archive', []), state.get('current_task', {})]
                 if task.get('contract_hash') == contract['hash'] and task.get('milestone_id') == row['id']]
        if not tasks or any(task.get('milestone_ids') for task in tasks):
            raise ValueError('Complete serial task history required')
        roots = sorted(set(path for task in tasks for path in task.get('affected_paths', [])))
        if not roots or not all(valid_path(path) for path in roots):
            raise ValueError('Explicit literal source ownership required')
        task_ids = {task['id'] for task in tasks}
        records = [record for record in state.get('stages', [])
                   if not record.get('runner_owned') and record.get('task_id') in task_ids]
        validation = state['validation']
        if task_ids != {record.get('task_id') for record in records}:
            raise ValueError('Stage history missing for a milestone task')
        if not records or validation['output'] not in {record.get('output') for record in records}:
            raise ValueError('Validation stage provenance missing')
        changed, stage_pins = set(), {}
        for record in records:
            if record.get('contract_hash') != contract['hash']:
                raise ValueError('Stage contract lineage missing')
            before, after = s.read(record['before_ref']), s.read(record['after_ref'])
            if any(snapshot['revision'] != s.digest({key: snapshot[key] for key in ('head', 'files')})
                   for snapshot in (before, after)):
                raise ValueError('Stage source snapshot integrity differs')
            if after['revision'] != record['source_revision']:
                raise ValueError('Stage source snapshot differs')
            paths = s.changed_paths(before, after)
            if paths != sorted(record['changed_files']):
                raise ValueError('Stage changed-file manifest differs')
            changed.update(paths)
            for field in ('before_ref', 'after_ref', 'output'):
                stage_pins[record[field]] = s.file_hash(record[field])
        if not all(valid_path(path) for path in changed):
            raise ValueError('Unsupported changed-file path')
        pins = {**validation['evidence_hashes'], **stage_pins}
        if not intact(pins):
            raise ValueError('Evidence missing or changed')
        manifest = {'version': 1, 'contract': copy.deepcopy(contract), 'milestone': copy.deepcopy(milestone),
                    'criteria': copy.deepcopy(definitions), 'roots': roots, 'changed_files': sorted(changed),
                    'files': footprint(current, roots, changed), 'evidence_hashes': pins,
                    'accepted_source_revision': current['revision'], 'validation_digest': s.digest(validation)}
        manifest['hash'] = s.digest(manifest)
        return manifest, None
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as error:
        return None, str(error)


def manifest_valid(manifest):
    return (isinstance(manifest, dict) and manifest.get('version') == 1
            and manifest.get('hash') == s.digest({key: value for key, value in manifest.items() if key != 'hash'}))


def matches(manifest, current):
    try:
        return (manifest_valid(manifest) and intact(manifest['evidence_hashes'])
                and footprint(current, manifest['roots'], manifest['changed_files']) == manifest['files'])
    except (OSError, ValueError, KeyError, TypeError):
        return False


def carry(state, current):
    """Run only after explicit approval; never transplant a current validation."""
    try:
        from . import autocode_goals as goals
    except ImportError:
        import autocode_goals as goals
    if not goals.approved(state):
        return []
    target = state['goal_contract']
    if any(row['to_contract_hash'] == target['hash'] for row in state.get('milestone_carry_forward', [])):
        return []
    candidates, outcomes, carried = {}, [], []
    milestones = {row['id']: row for row in target['body']['milestones']}
    previous = next((old for old in reversed(state.get('contract_history', []))
                     if old['revision'] == target['revision'] - 1), {})
    for row in list(state.get('milestone_progress', {}).values()):
        manifest = row.get('reuse_manifest')
        if not row.get('accepted') or row.get('contract_hash') != previous.get('hash'):
            continue
        reason = None
        old = manifest.get('contract', {}) if isinstance(manifest, dict) else {}
        if not manifest_valid(manifest):
            reason = 'No complete acceptance provenance; revalidate'
        elif row.get('carried_from') or row.get('accepted_batch'):
            reason = 'Chained or batch reuse requires fresh validation'
        elif (old.get('revision') != target['revision'] - 1 or old.get('task_id') != target['task_id']
              or old.get('hash') != row.get('contract_hash')
              or not goals.approved({'goal_contract': old, 'user_events': state.get('user_events', [])})):
            reason = 'Not the immediately preceding approved contract'
        elif global_scope(old['body']) != global_scope(target['body']):
            reason = 'Global goal, scope or constraints changed'
        elif row['id'] not in milestones or manifest['milestone'] != milestones[row['id']]:
            reason = 'Milestone definition, ownership or dependencies changed'
        elif manifest['criteria'] != criteria(target['body'], milestones[row['id']]):
            reason = 'Criterion definition or verification method changed'
        elif s.digest(row.get('accepted_validation')) != manifest['validation_digest']:
            reason = 'Accepted validation changed'
        elif not matches(manifest, current):
            reason = 'Source footprint or retained evidence changed'
        if reason:
            outcomes.append({'milestone_id': row['id'], 'from_contract_hash': row.get('contract_hash'), 'result': 'revalidate', 'reason': reason})
        else:
            candidates[row['id']] = row
    # A dependent milestone may move only with every prerequisite. Missing or
    # changed prerequisites invalidate the downstream candidate transitively.
    while candidates:
        ready = [mid for mid in candidates if set(milestones[mid]['depends_on']) <= set(carried)]
        if not ready:
            break
        for mid in ready:
            old_row = candidates.pop(mid)
            manifest = old_row['reuse_manifest']
            provenance = {'from_contract_hash': old_row['contract_hash'], 'from_revision': manifest['contract']['revision'],
                          'to_contract_hash': target['hash'], 'to_revision': target['revision'],
                          'manifest_hash': manifest['hash'], 'at': s.now(),
                          'source_revision_at_reuse': current['revision'], 'final_integration_required': True}
            copied = copy.deepcopy(old_row)
            copied.update(contract_hash=target['hash'], carried_from=provenance)
            state['milestone_progress'][f"{target['hash']}:{mid}"] = copied
            carried.append(mid)
            outcomes.append({'milestone_id': mid, 'result': 'carried', **provenance})
    for mid in candidates:
        outcomes.append({'milestone_id': mid, 'result': 'revalidate', 'reason': 'Prerequisite could not be carried'})
    state.setdefault('milestone_carry_forward', []).append({
        'to_contract_hash': target['hash'], 'to_revision': target['revision'], 'outcomes': outcomes})
    return carried


def current_ids(state, accepted):
    """Recheck carried prerequisites after subsequent source/evidence changes."""
    rows = [row for row in state.get('milestone_progress', {}).values()
            if row.get('contract_hash') == state.get('goal_contract', {}).get('hash') and row.get('carried_from')]
    if not rows:
        return accepted
    current = s.snapshot(Path(state['workspace']))
    accepted = set(accepted)
    for row in rows:
        if not matches(row.get('reuse_manifest'), current):
            accepted.discard(row['id'])
    while True:
        invalid = {row['id'] for row in rows if not set(row.get('depends_on', [])) <= accepted}
        updated = accepted - invalid
        if updated == accepted:
            return accepted
        accepted = updated


def before_assignment(state, spec, decision):
    """Avoid redundant rebuilding, while allowing explicit evidence-backed repair."""
    key = f"{state['goal_contract']['hash']}:{spec['milestone_id']}"
    row = state.get('milestone_progress', {}).get(key)
    if not row or not row.get('carried_from') or not row.get('accepted') or spec['kind'] != 'implement':
        return
    accepted = {item['id'] for item in state['milestone_progress'].values()
                if item.get('accepted') and item.get('contract_hash') == state['goal_contract']['hash']}
    if (spec['milestone_id'] in current_ids(state, accepted)
            and decision['status'] != 'REWORK'):
        raise ValueError('Milestone already carried forward; select unfinished work or validation')
    row.update(accepted=False, carry_revoked={'at': s.now(), 'reason': 'Rework or source/evidence drift'})
