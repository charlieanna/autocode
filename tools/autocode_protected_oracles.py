"""Original test gates retained before a new code run dispatches a model.

reconcile alone writes settings.protected_tests. Replay and completion read it;
user_events and content-addressed bundles preserve explicit revisions. The
caller supplies test classification, framework discovery and scratch execution.

A regular test is bound by hash, size and mode. A test that is a symbolic link
is bound as that link: its exact relative target and, recursively, the identity
of what it names. The bundle and the original replay recreate each link and its
targets (the closure), never a dereferenced copy. A link must name a file
inside the repository through real directories; absolute, escaping, dangling,
cyclic and directory links are refused.
"""
from __future__ import annotations
import copy
import os
from pathlib import Path, PurePosixPath
import shutil
import uuid
try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util


def path_in(root, name, *, link=False):
    """``name`` under ``root`` through real directories; a regular file, or with ``link`` also a link itself."""
    relative = PurePosixPath(name)
    if not name or relative.is_absolute() or '..' in relative.parts or str(relative) != name:
        raise ValueError('Protected test paths must be portable repository-relative paths')
    path = Path(root) / name
    if any(parent.is_symlink() for parent in [path, *path.parents]
           if parent != Path(root).parent and not (link and parent == path)):
        raise ValueError(f'Protected test must not follow a symlink: {name}')
    if link and path.is_symlink():
        return path
    if not path.resolve().is_relative_to(Path(root).resolve()) or not path.is_file():
        raise ValueError(f'Protected test is missing or not a file: {name}')
    return path


def link_target(name, link):
    """The repository path a protected link names. Leading '..' only, so it resolves the same in any copy."""
    relative = PurePosixPath(link)
    up = next((index for index, part in enumerate(relative.parts) if part != '..'), len(relative.parts))
    parent = PurePosixPath(name).parent.parts
    if (not link or relative.is_absolute() or str(relative) != link or '..' in relative.parts[up:]
            or up == len(relative.parts) or up > len(parent)):
        raise ValueError("Protected test link must name a path inside the repository, with '..' only leading it")
    return str(PurePosixPath(*parent[:len(parent) - up], *relative.parts[up:]))


def identity(root, name, seen=()):
    """A regular file's hash, size and mode, or a link's exact target and the identity of what it names."""
    path = path_in(root, name, link=True)
    if not path.is_symlink():
        return {'sha256': util.file_hash(path), 'size': path.stat().st_size,
                'mode': path.stat().st_mode & 0o777}
    if name in seen:
        raise ValueError(f'Protected test link is cyclic: {name}')
    link = os.readlink(path)
    try:
        return {'symlink': link, 'target': identity(root, link_target(name, link), (*seen, name))}
    except ValueError as error:
        raise ValueError(f'Protected test link {name} -> {link} is refused: {error}') from None


def closure(files):
    """Every path a binding restores: each test and, for a link, every path along its chain of targets."""
    paths = {}
    for name, entry in files.items():
        while True:
            if paths.setdefault(name, entry) != entry:
                raise ValueError(f'Protected test binding gives {name} two identities')
            if 'symlink' not in entry:
                break
            name, entry = link_target(name, entry['symlink']), entry['target']
    return paths


def body(record):
    return {key: record[key] for key in ('version', 'files', 'command')}


def verify_binding(record):
    if record.get('version') != 1 or record.get('binding_hash') != util.digest(body(record)):
        raise ValueError('Protected test binding changed; a model cannot revise the original gate')
    if record.get('inventory_path') and util.read_object(record['inventory_path']) != body(record):
        raise ValueError('Retained protected-test inventory changed')
    for name, expected in record['files'].items():
        if identity(record['root'], name) != expected:
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
                if identity(workspace, name) != expected:
                    raise ValueError(f'Protected input changed during capture: {name}')
            for name, entry in closure(files).items():
                target = temporary / name
                target.parent.mkdir(parents=True, exist_ok=True)
                if 'symlink' in entry:
                    target.symlink_to(entry['symlink'])
                else:
                    shutil.copy2(path_in(workspace, name), target)
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
    return {name: identity(workspace, name) for name, value in util.snapshot(workspace)['files'].items()
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
            actual = identity(workspace, name)
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
        restored = closure(record['files'])
        result['original'] = scratch_run(workspace, directory / 'original', command=record['command'], timeout=timeout,
            files={name: str(path_in(record['root'], name)) for name, entry in restored.items() if 'symlink' not in entry},
            links={name: entry['symlink'] for name, entry in restored.items() if 'symlink' in entry})
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
