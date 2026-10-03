"""Original test gates retained before a new code run dispatches a model.

reconcile alone writes settings.protected_tests. Replay and completion read it;
user_events and content-addressed bundles preserve explicit revisions. The
caller supplies test classification, framework discovery and scratch execution.
"""
from __future__ import annotations
import copy
from pathlib import Path, PurePosixPath
import shutil
import uuid
try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util


def path_in(root, name):
    relative = PurePosixPath(name)
    if not name or relative.is_absolute() or '..' in relative.parts or str(relative) != name:
        raise ValueError('Protected test paths must be portable repository-relative paths')
    path = Path(root) / name
    if any(parent.is_symlink() for parent in [path, *path.parents] if parent != Path(root).parent):
        raise ValueError(f'Protected test must not follow a symlink: {name}')
    if not path.resolve().is_relative_to(Path(root).resolve()) or not path.is_file():
        raise ValueError(f'Protected test is missing: {name}')
    return path


def identity(path):
    return {'sha256': util.file_hash(path), 'size': path.stat().st_size,
            'mode': path.stat().st_mode & 0o777}


def body(record):
    return {key: record[key] for key in ('version', 'files', 'command')}


def verify_binding(record):
    if record.get('version') != 1 or record.get('binding_hash') != util.digest(body(record)):
        raise ValueError('Protected test binding changed; a model cannot revise the original gate')
    if record.get('inventory_path') and util.read_object(record['inventory_path']) != body(record):
        raise ValueError('Retained protected-test inventory changed')
    for name, expected in record['files'].items():
        if identity(path_in(record['root'], name)) != expected:
            raise ValueError(f'Original protected test bundle changed: {name}')
    return record


def retain(workspace, run_dir, files, command):
    workspace = Path(workspace).resolve()
    record = {'version': 1, 'files': copy.deepcopy(files), 'command': command}
    record['binding_hash'] = util.digest(body(record))
    root = Path(run_dir) / 'protected-tests' / record['binding_hash']
    record['root'] = str(root)
    if not root.exists():
        temporary = root.parent / ('.capture-' + uuid.uuid4().hex)
        temporary.mkdir(parents=True)
        try:
            for name, expected in files.items():
                source = path_in(workspace, name)
                if identity(source) != expected:
                    raise ValueError(f'Protected input changed during capture: {name}')
                target = temporary / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            verify_binding({**record, 'root': str(temporary)})
            temporary.rename(root)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    inventory_path = root.parent / (record['binding_hash'] + '.json')
    if not inventory_path.exists():
        util.atomic_json(inventory_path, body(record))
    record['inventory_path'] = str(inventory_path)
    return verify_binding(record)


def inventory(workspace, is_test_path):
    return {name: identity(path_in(workspace, name)) for name, value in util.snapshot(workspace)['files'].items()
            if is_test_path(name) and value != 'deleted'}


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
    revision = util.snapshot(workspace)['revision']
    changed = []
    for name, expected in record['files'].items():
        try:
            actual = identity(path_in(workspace, name))
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
        result['original'] = scratch_run(workspace, directory / 'original', command=record['command'], timeout=timeout,
                                        files={name: str(path_in(record['root'], name)) for name in record['files']})
        receipts = (result['candidate'], result['original'])
        if any(row.get('exit_code') != 0 or row.get('error') or row.get('timed_out') for row in receipts):
            result['verdict'] = 'FAIL'
    verify_binding(record)
    if util.snapshot(workspace)['revision'] != revision:
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
