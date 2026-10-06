"""Original test gates retained before a new code run dispatches a model.

reconcile alone writes settings.protected_tests. Replay and completion read it;
user_events and content-addressed bundles preserve explicit revisions. The
caller supplies test classification, framework discovery and scratch execution.
"""
from __future__ import annotations

try:
    from . import autocode_source_scope as source_scope
except ImportError:
    import autocode_source_scope as source_scope

import copy
from pathlib import Path
import uuid
try:
    from . import autocode_util as util
    from . import autocode_protected_paths as paths, autocode_protected_store as store
    from .autocode_protected_paths import identity, path_in
except ImportError:
    import autocode_util as util
    import autocode_protected_paths as paths
    import autocode_protected_store as store
    from autocode_protected_paths import identity, path_in


def body(record):
    return {key: record[key] for key in ('version', 'files', 'command')}


def verify_binding(record):
    if record.get('version') not in (1, 2) or record.get('binding_hash') != util.digest(body(record)):
        raise ValueError('Protected test binding changed; a model cannot revise the original gate')
    if record.get('inventory_path') and util.read_object(record['inventory_path']) != body(record):
        raise ValueError('Retained protected-test inventory changed')
    archive = store.archive_path(record)
    if archive is not None:
        store.read(record, archive)
    else:
        for name, expected in record['files'].items():
            if identity(path_in(record['root'], name, allow_link=record['version'] == 2)) != expected:
                raise ValueError(f'Original protected test bundle changed: {name}')
        if record['version'] == 2:
            paths.verify_links(record['root'], record['files'])
    return record


def retain(workspace, run_dir, files, command):
    workspace = Path(workspace).resolve()
    version = 2 if any('symlink' in value for value in files.values()) else 1
    record = {'version': version, 'files': copy.deepcopy(files), 'command': command}
    record['binding_hash'] = util.digest(body(record))
    root = Path(run_dir) / 'protected-tests' / (record['binding_hash'] + '.zip')
    record['root'] = str(root)
    store.capture(record, workspace, root)
    inventory_path = root.parent / (record['binding_hash'] + '.json')
    if not inventory_path.exists():
        util.atomic_json(inventory_path, body(record))
    record['inventory_path'] = str(inventory_path)
    return verify_binding(record)


def inventory(workspace, is_test_path):
    snapshot = util.snapshot(workspace)['files']
    names = [name for name, value in snapshot.items() if is_test_path(name) and value != 'deleted']
    return paths.collect(workspace, names, snapshot)


def reconcile(state, settings, args, workspace, run_dir, *, is_test_path, discover_command):
    """New runs bind all eligible existing tests; saved runs never silently rebind.

    A CLI revision declares the exact old binding, new inventory, command and
    rationale. It is admitted only at a reconciled paused validation boundary.
    Neither a Builder report nor a repaired verification command is authority.
    """
    revision = getattr(args, 'revise_protected_tests', None)
    previous = settings.get('protected_tests')
    if revision:
        if not getattr(args, 'run_dir', None) or not getattr(args, 'resume_paused', False):
            raise ValueError('--revise-protected-tests requires a saved pause and --resume-paused')
        if (not str(state.get('status', '')).startswith('PAUSED_')
                or state.get('next_stage') not in ('sol', 'astra_checkpoint')
                or any(state.get(key) for key in ('active_stage', 'pending_report_repair', 'uncertain_artifacts',
                                                 'active_runner_check', 'runner_check'))):
            raise ValueError('Reconcile the attempt and stop before independent validation to revise protected tests')
        if not previous:
            raise ValueError('This saved run has no original protected-test binding; start a new run')
        verify_binding(previous)
        proposal = util.read_object(revision)
        if (set(proposal) != {'previous_hash', 'files', 'command', 'reason'}
                or proposal['previous_hash'] != previous['binding_hash']
                or not isinstance(proposal['reason'], str) or not proposal['reason'].strip()
                or not isinstance(proposal['command'], str) or not proposal['command'].strip()):
            raise ValueError('A protected-test revision needs the exact previous hash, command and user rationale')
        files = inventory(workspace, is_test_path)
        if proposal['files'] != files or not files:
            raise ValueError('Protected-test revision must identify every current eligible test by hash, size and mode')
        current = retain(workspace, run_dir, files, proposal['command'])
        state.setdefault('user_events', []).append({'kind': 'protected_tests_revised', 'actor': 'user_cli',
            'at': util.now(), 'previous': previous, 'current': current, 'reason': proposal['reason'],
            'proposal_sha256': util.file_hash(revision)})
        settings['protected_tests'] = current
    elif not getattr(args, 'run_dir', None):
        files = inventory(workspace, is_test_path)
        command = (settings.get('regression') or {}).get('test_command') or (discover_command() if files else None)
        settings['protected_tests'] = retain(workspace, run_dir, files, command)
    if getattr(args, 'run_dir', None) and not any(state.get(key) for key in
            ('active_stage', 'active_runner_check')):
        retained = [settings.get('protected_tests')]
        for event in state.get('user_events', []):
            if event.get('kind') == 'protected_tests_revised':
                retained.extend((event.get('previous'), event.get('current')))
        for record in retained:
            if record:
                verify_binding(record)
                store.compact(record, run_dir)
    return settings


def context(settings, affected_paths=()):
    record = settings.get('protected_tests')
    if not record or not record['files']:
        return None
    verify_binding(record)
    assigned = {name: expected for name, expected in record['files'].items()
                if any(name == path.rstrip('/') or name.startswith(path.rstrip('/') + '/')
                       for path in affected_paths)}
    return {key: copy.deepcopy(record.get(key)) for key in ('binding_hash', 'root', 'inventory_path', 'command')} | {
        'total_files': len(record['files']), 'assigned_tests': copy.deepcopy(assigned),
        'instruction': 'These original test files are the pre-build gate. The runner independently executes them '
        'against your implementation if you edit, rename, skip or delete a protected test. Added coverage is allowed; '
        'weakening a failing assertion cannot earn PASS. Fix the implementation within the approved scope. '
        'Only an explicit saved user CLI revision may replace the gate; never edit its retained bundle.'}


def replay(state, workspace, out, scratch_run, *, timeout):
    record = state.get('settings', {}).get('protected_tests')
    if not record:
        return None  # compatibility: do not invent an original binding for old runs
    verify_binding(record)
    revision = source_scope.snapshot(workspace, state)['revision']
    changed = []
    for name, expected in record['files'].items():
        try:
            actual = identity(path_in(workspace, name, allow_link=record['version'] == 2))
        except (OSError, ValueError):
            actual = None
        if actual != expected:
            changed.append(name)
    result = {'binding_hash': record['binding_hash'], 'source_revision': revision,
              'changed_tests': changed, 'verdict': 'PASS', 'candidate': None, 'original': None}
    if not changed:
        result['meaning'] = 'Original test files unchanged; ordinary independent checks remain required.'
        return result
    directory = Path(out) / 'protected-tests' / uuid.uuid4().hex
    if not record['command']:
        result.update(verdict='NOT_VERIFIED', error='No original suite command was available; explicit user revision required')
    else:
        result['candidate'] = scratch_run(workspace, directory / 'candidate', command=record['command'], timeout=timeout)
        with store.opened(record, workspace) as original_root:
            overlays = {'files': {name: str(path_in(original_root, name))
                                  for name, value in record['files'].items() if 'symlink' not in value}}
            links = {name: value['symlink'] for name, value in record['files'].items() if 'symlink' in value}
            if links:
                overlays['links'] = links
            result['original'] = scratch_run(workspace, directory / 'original', command=record['command'], timeout=timeout,
                                            **overlays)
        receipts = (result['candidate'], result['original'])
        if any(row.get('exit_code') != 0 or row.get('error') or row.get('timed_out') for row in receipts):
            result['verdict'] = 'FAIL'
    verify_binding(record)
    if source_scope.snapshot(workspace, state)['revision'] != revision:
        result.update(verdict='NOT_VERIFIED', error='Candidate changed during protected-test replay')
    directory.mkdir(parents=True, exist_ok=True)
    receipt = directory / 'receipt.json'
    util.atomic_json(receipt, result)
    result['receipt'] = str(receipt)
    result['receipt_sha256'] = util.file_hash(receipt)
    if result['verdict'] != 'PASS':
        tail = (result.get('original') or {}).get('tail', '')[-500:]
        raise ValueError('Protected tests changed and the original gate did not pass on this implementation. '
                         'Repair the implementation; a modified assertion is not proof. '
                         + result.get('error', '') + f' Receipt: {receipt}. {tail}')
    return result


def ready(state, current_revision):
    record = state.get('settings', {}).get('protected_tests')
    if not record:
        return True
    try:
        verify_binding(record)
        proof = (state.get('validation') or {}).get('check_replay') or {}
        result = proof.get('protected_tests') or {}
        if result.get('receipt'):
            receipt = Path(result['receipt'])
            if (not receipt.is_file() or util.file_hash(receipt) != result.get('receipt_sha256')
                    or util.read_object(receipt) != {key: value for key, value in result.items()
                                                    if key not in ('receipt', 'receipt_sha256')}):
                return False
        return (result.get('verdict') == 'PASS' and result.get('binding_hash') == record['binding_hash']
                and result.get('source_revision') == current_revision)
    except (OSError, ValueError, TypeError, KeyError):
        return False
