"""Public, locked checkpoint operations; never launches a provider.

This is the runtime owner of checkpoint restoration, exposed through TaskRun.
Dashboard/coordinators only invoke this CLI; they never clone private run state.
Each operation creates an explicit continuation in a new managed worktree. The
old run, index and branch are preserved. An intent precedes Git mutations and
idempotent replay reconciles only its exact owned path; it never resets files.
"""
from __future__ import annotations

try:
    from . import autocode_source_scope as source_scope, autocode_source_snapshot as source_snapshot
except ImportError:
    import autocode_source_scope as source_scope, autocode_source_snapshot as source_snapshot


import argparse
from contextlib import contextmanager
import difflib
import fcntl
import json
from pathlib import Path
import re
import sys

try:
    from . import autocode_util as util, autocode_code_checkpoints as checkpoints
    from . import autocode_checkpoint_continuation as continuation, autocode_contract_identity as contract
    from . import autocode_workspaces as workspaces, autocode_registry as registry
    from . import autocode_legacy_process as legacy, autocode_checkout_lock as checkout_lock
except ImportError:
    import autocode_util as util, autocode_code_checkpoints as checkpoints
    import autocode_checkpoint_continuation as continuation, autocode_contract_identity as contract
    import autocode_workspaces as workspaces, autocode_registry as registry
    import autocode_legacy_process as legacy, autocode_checkout_lock as checkout_lock


def regular(path, root):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError('Checkpoint artifact must be a regular file inside its run directory')
    return path


def target(workspace, run_dir):
    workspace = Path(workspace).resolve(strict=True)
    run_dir = Path(run_dir).resolve(strict=True)
    if not run_dir.is_relative_to(workspace / '.autocode/runs'):
        raise ValueError('Select the checkpoint run in its actual task workspace')
    state = util.read(regular(run_dir / 'state.json', run_dir))
    if state.get('workspace') != str(workspace) or state.get('run_dir', str(run_dir)) != str(run_dir):
        raise ValueError('Run identity differs from the selected workspace')
    state['run_dir'] = str(run_dir)
    return workspace, run_dir, state


def receipt(state, run_dir, ident):
    row = next((row for row in state.get('code_checkpoints', []) if row.get('id') == ident), None)
    if not row or not row.get('available'):
        raise ValueError('This step has no restorable source checkpoint; historical metadata cannot reconstruct files')
    path = regular(row['receipt'], run_dir)
    if util.file_hash(path) != row['receipt_sha256']:
        raise ValueError('The saved code checkpoint receipt changed')
    saved = util.read(path)
    if (saved['id'] != ident or saved['commit'] != row['commit'] or saved['run_dir'] != str(run_dir)
            or saved['contract_hash'] != (state.get('goal_contract') or {}).get('hash')):
        raise ValueError('Checkpoint identity or approved plan changed')
    if checkpoints.tree_files(state['workspace'], saved['commit']) != checkpoints.content_files(saved['source']):
        raise ValueError('Saved Git checkpoint contents cannot be verified')
    return saved


def source_token(state, saved, source):
    # Restores are receipts, not changes to the paused execution frontier.
    bound = {key: value for key, value in state.items() if key != 'checkpoint_restores'}
    return 'checkpoint-v1:' + util.digest({'state': bound, 'checkpoint': saved, 'source': source})


def compare(state, run_dir, ident):
    saved = receipt(state, run_dir, ident)
    source = source_scope.snapshot(state['workspace'], state)
    changed = util.changed_paths(saved['source'], source)
    patch = ''
    before = checkpoints.content_files(saved['source'])
    after = checkpoints.content_files(source)
    for name in changed:
        old, new = before.get(name), after.get(name)
        patch += 'diff --checkpoint ' + name + '\n'
        path = Path(state['workspace']) / name
        if any(str(value).startswith('symlink:') for value in (old, new)):
            patch += 'Link before: ' + str(old) + '\nLink now: ' + str(new) + '\n'
            continue
        if (old or '').startswith('executable:') != (new or '').startswith('executable:'):
            patch += 'Executable mode changed.\n'
        try:
            old_bytes = checkpoints.git(state['workspace'], 'show', saved['commit'] + ':' + name) if old else b''
            new_bytes = path.read_bytes() if new else b''
            if max(len(old_bytes), len(new_bytes)) > 100000 or b'\0' in old_bytes + new_bytes:
                patch += 'Binary or large file changed; inspect the saved checkpoint and current file.\n'
            else:
                patch += ''.join(difflib.unified_diff(old_bytes.decode().splitlines(True), new_bytes.decode().splitlines(True),
                                                     'checkpoint/' + name, 'current/' + name))
        except UnicodeError:
            patch += 'Binary file changed.\n'
    if source != source_scope.snapshot(state['workspace'], state):
        raise ValueError('Source changed while comparing; refresh the comparison')
    return {'version': 1, 'checkpoint_id': ident, 'checkpoint_commit': saved['commit'],
            'expected_token': source_token(state, saved, source), 'changed_files': changed,
            'patch': patch[:200000], 'truncated': len(patch) > 200000,
            'blocked_reason': checkpoints.blocked_reason(state),
            'source_revision': source['revision'], 'compared_at': util.now(),
            'source_workspace': state['workspace'], 'source_branch': state.get('task_branch') or
                checkpoints.git(state['workspace'], 'branch', '--show-current').decode().strip()}


@contextmanager
def inbox_boundary(run_dir, state):
    path = run_dir / 'interventions.lock'
    if path.is_symlink():
        raise ValueError('Intervention lock must not be a symlink')
    with path.open('a+') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('An intervention is being submitted; retry after it is reconciled') from None
        inbox = run_dir / 'interventions.json'
        if inbox.exists():
            requests = util.read(regular(inbox, run_dir))['requests']
            applied = {row.get('id') for row in state.get('applied_interventions', [])}
            if any(row.get('id') not in applied for row in requests):
                raise ValueError('Reconcile the pending intervention before restoring a checkpoint')
        yield


def _same_files(workspace, saved):
    return checkpoints.content_files(source_snapshot.snapshot(workspace, paths=saved['source'].get('source_paths', ()))) == checkpoints.content_files(saved['source'])


def record_restore(state, run_dir, result):
    """Repair a receipt-before-status crash without repeating the Git operation."""
    if not any(row.get('operation') == result['operation'] for row in state.get('checkpoint_restores', [])):
        state.setdefault('checkpoint_restores', []).append(result)
        util.atomic_json(run_dir / 'state.json', state)


def candidate_storage(workspace, child_dir):
    """Refuse redirected partial storage before opening or creating lock files."""
    paths = [workspace / '.autocode', workspace / '.autocode/runs', child_dir,
             workspace / '.autocode/writer.lock', child_dir / 'writer.lock']
    if any(path.is_symlink() or not path.resolve().is_relative_to(workspace) for path in paths):
        raise ValueError('Partial candidate operational storage changed; preserved it')


def restore(state, run_dir, ident, expected, operation):
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', operation or ''):
        raise ValueError('Restore requires a stable request id (8–100 letters, digits, underscores or hyphens)')
    root = run_dir / 'checkpoint-restores'
    if root.is_symlink():
        raise ValueError('Restoration receipts must not be symlinks')
    root.mkdir(exist_ok=True)
    path = root / (operation + '.json')
    binding = {'checkpoint_id': ident, 'expected_token': expected, 'operation': operation}
    prior = util.read(regular(path, run_dir)) if path.exists() else None
    if prior and prior['binding'] != binding:
        raise ValueError('This request id already belongs to a different checkpoint confirmation')
    if prior and prior.get('result'):
        record_restore(state, run_dir, prior['result'])
        return {**prior['result'], 'replayed': True, 'message': 'Restore already completed. Open the candidate for its current status.'}
    reason = checkpoints.blocked_reason(state)
    if reason:
        raise ValueError(reason)
    saved = receipt(state, run_dir, ident)
    if not expected or expected != source_token(state, saved, source_scope.snapshot(state['workspace'], state)):
        raise ValueError('The paused task or source changed. Compare again before restoring.')
    legacy.assert_no_legacy_process(run_dir, state['workspace'])
    project = Path(state.get('project_workspace') or state['workspace']).resolve()
    meta = workspaces.metadata(Path(state['workspace']))
    if meta:
        project = Path(meta['project_workspace'])
    elif project != Path(state['workspace']):
        raise ValueError('Original project ownership cannot be verified without managed worktree metadata')
    parent = workspaces.keep_out_of_git(project) / 'worktrees'
    parent.mkdir(exist_ok=True)
    if parent.is_symlink() or not parent.resolve().is_relative_to(project):
        raise ValueError('Restoration worktree storage must remain inside the project')
    name = 'checkpoint-' + util.digest([str(run_dir), operation])[:20]
    workspace, branch = parent / name, 'autocode/' + name
    child_dir = workspace / '.autocode/runs' / name
    if prior is None:
        if workspace.exists():
            raise ValueError('Restoration target already exists; its files were preserved')
        intent = {'binding': binding, 'workspace': str(workspace), 'branch': branch, 'created_at': util.now()}
        util.atomic_json(path, intent)
        workspace.mkdir(mode=0o700)
        stat = workspace.stat()
        intent['directory_identity'] = [stat.st_dev, stat.st_ino]
        util.atomic_json(path, intent)
    else:
        intent = prior
    stat = workspace.stat()
    if (workspace.is_symlink() or intent.get('directory_identity') != [stat.st_dev, stat.st_ino]
            or intent['workspace'] != str(workspace) or intent['branch'] != branch):
        raise ValueError('Partial restore ownership cannot be verified; all partial files were retained')
    if not (workspace / '.git').exists():
        if any(workspace.iterdir()):
            raise ValueError('Partial restore contains files; inspect them before any recovery')
        # Git serializes ref creation. An existing branch is a conflict, never force-reset.
        checkpoints.git(project, 'worktree', 'add', '-b', branch, str(workspace), saved['commit'])
    candidate_storage(workspace, child_dir)
    with util.run_lock(child_dir), checkout_lock.exclusive(workspace, child_dir):
        if (checkpoints.git(workspace, 'rev-parse', 'HEAD').decode().strip() != saved['commit']
                or checkpoints.git(workspace, 'branch', '--show-current').decode().strip() != branch
                or checkpoints.git(workspace, 'write-tree').strip() != checkpoints.git(workspace, 'rev-parse', 'HEAD^{tree}').strip()
                or not _same_files(workspace, saved)):
            raise ValueError('Partial restore source changed; preserved it without overwriting')
        workspaces.keep_out_of_git(workspace)
        metadata = {'version': 1, 'kind': 'task', 'project_workspace': str(project), 'workspace': str(workspace),
                    'branch': branch, 'base_commit': state.get('base_commit') or saved['source']['head']}
        metadata_path = workspace / '.autocode/task-workspace.json'
        if metadata_path.exists() and util.read(regular(metadata_path, workspace)) != metadata:
            raise ValueError('Partial restore workspace metadata changed')
        util.atomic_json(metadata_path, metadata)
        child_dir.mkdir(parents=True, exist_ok=True)
        child = continuation.create(state, saved, workspace, child_dir, branch, project, operation, at=intent['created_at'])
        if expected != source_token(state, saved, source_scope.snapshot(state['workspace'], state)):
            raise ValueError('Original source changed during restoration; partial candidate retained')
        expected_child = util.digest(child)
        if intent.get('child_digest') and intent['child_digest'] != expected_child:
            raise ValueError('Partial continuation inputs changed; inspect retained history')
        intent['child_digest'] = expected_child
        util.atomic_json(path, intent)
        child_path = child_dir / 'state.json'
        if child_path.exists():
            child = util.read(regular(child_path, child_dir))
            if util.digest(child) != expected_child:
                raise ValueError('Partial continuation has changed; preserved it without replaying the restore')
        else:
            util.atomic_json(child_dir / 'restoration-history.json', state)
            # History lives with both branches. No mutation of the original journal.
            journal = run_dir / 'conversation-journal.json'
            if journal.exists():
                journal_copy = util.read(regular(journal, run_dir))
                journal_copy['restored_from_conversation'] = journal_copy['conversation_id']
                journal_copy['conversation_id'] = util.digest([str(run_dir), operation, 'conversation'])[:32]
                journal_copy['conversation']['id'] = journal_copy['conversation_id']
                util.atomic_json(child_dir / journal.name, journal_copy)
            util.atomic_json(child_path, child)
        registry.configure_workspace_storage(state['workspace'])
        registry.register_run(workspace, child_dir, child)
    result = {'version': 1, 'operation': operation, 'checkpoint_id': ident,
              'workspace': str(workspace), 'run_dir': str(child_dir), 'branch': branch,
              'preserved_workspace': state['workspace'], 'preserved_run': str(run_dir),
              'preserved_branch': state.get('task_branch') or checkpoints.git(state['workspace'], 'branch', '--show-current').decode().strip(),
              'status': child['status'], 'next_stage': child['next_stage'],
              'message': 'Original branch and later work preserved. New candidate is paused; fresh checks are required.'}
    intent['result'] = result
    util.atomic_json(path, intent)
    record_restore(state, run_dir, result)
    return result


def cli(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--run-dir', required=True, type=Path)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--compare', metavar='CHECKPOINT')
    action.add_argument('--restore', metavar='CHECKPOINT')
    parser.add_argument('--expected-token')
    parser.add_argument('--request-id')
    args = parser.parse_args(argv)
    try:
        workspace, run_dir, state = target(args.workspace, args.run_dir)
        if args.compare:
            result = compare(state, run_dir, args.compare)
        else:
            with util.run_lock(run_dir), checkout_lock.exclusive(workspace, run_dir):
                workspace, run_dir, state = target(workspace, run_dir)
                with inbox_boundary(run_dir, state):
                    result = restore(state, run_dir, args.restore, args.expected_token, args.request_id)
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError, KeyError) as error:
        print(json.dumps({'error': {'code': 'checkpoint_refused', 'message': str(error)}}))
        print('Input rejected: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(cli(sys.argv[1:]))
