"""Immutable, source-bound code checkpoints owned by the runner.

Writes only ``code_checkpoints`` (an append-only source receipt list, read by the
status view and checkpoint CLI). A checkpoint is a Git object, not a claim of
acceptance. The real index, HEAD and working files are never changed. Metadata
from older runs cannot be upgraded into missing source snapshots.
"""
from __future__ import annotations

try:
    from . import autocode_source_scope as source_scope, autocode_source_snapshot as source_snapshot
except ImportError:
    import autocode_source_scope as source_scope, autocode_source_snapshot as source_snapshot


from copy import deepcopy
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile

try:
    from . import autocode_util as util, autocode_contract_identity as contract
except ImportError:
    import autocode_util as util, autocode_contract_identity as contract


def approved(state):
    try:
        return contract.approved(state)
    except (KeyError, TypeError, ValueError):
        return False


def git(workspace, *args, env=None, data=None):
    proc = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
                           '-C', str(workspace), *args], input=data, capture_output=True,
                          env={**os.environ, **(env or {})})
    if proc.returncode:
        raise ValueError(proc.stderr.decode(errors='replace').strip() or 'Git checkpoint operation failed')
    return proc.stdout


def content_files(snapshot):
    return {name: value for name, value in snapshot['files'].items() if value != 'deleted'}


def tree_files(workspace, revision):
    """Compare actual blobs, modes and links; reject unsupported submodule snapshots."""
    result = {}
    for entry in git(workspace, 'ls-tree', '-rz', revision).split(b'\0'):
        if not entry:
            continue
        meta, name = entry.split(b'\t', 1)
        mode, kind, oid = meta.decode().split()
        if kind != 'blob':
            raise ValueError('Checkpoint restoration requires ordinary files; nested Git repositories are unsupported')
        data = git(workspace, 'cat-file', 'blob', oid)
        result[name.decode()] = ('symlink:' + data.decode() if mode == '120000' else
                                ('executable:' if mode == '100755' else '') + hashlib.sha256(data).hexdigest())
    return result


def capture_tree(workspace, source, directory):
    """Build from a separate index and verify its blobs against the captured source."""
    files = content_files(source)
    if any(value.startswith(('submodule:', 'uninitialized-submodule')) for value in files.values()):
        raise ValueError('Nested Git repositories cannot yet be restored as a code checkpoint')
    with tempfile.TemporaryDirectory(prefix='code-checkpoint-', dir=directory) as tmp:
        env = {'GIT_INDEX_FILE': str(Path(tmp) / 'index')}
        git(workspace, 'read-tree', '--empty', env=env)
        if files:
            # NUL-delimited literal paths support spaces, newlines and leading dashes.
            git(workspace, '--literal-pathspecs', 'add', '-f', '--pathspec-from-file=-', '--pathspec-file-nul',
                env=env, data=b'\0'.join(name.encode() for name in files) + b'\0')
        tree = git(workspace, 'write-tree', env=env).decode().strip()
        if tree_files(workspace, tree) != files or source_snapshot.snapshot(workspace, paths=source.get('source_paths', ())) != source:
            raise ValueError('Source changed during checkpoint capture, or Git filters changed the captured content')
        commit = git(workspace, '-c', 'user.name=AutoCode', '-c', 'user.email=autocode@localhost',
                     'commit-tree', tree, '-p', source['head'], data=b'AutoCode saved code checkpoint\n').decode().strip()
    return commit


def update(state, run_dir):
    """Called only from the runner's durable writer after an applied Builder step."""
    implementation = state.get('implementation') or {}
    task = state.get('current_task') or {}
    if (not implementation.get('source_revision') or not task.get('id')
            or implementation.get('task_id') != task['id']
            or implementation.get('contract_hash') != (state.get('goal_contract') or {}).get('hash')
            or state.get('active_stage') or not approved(state)):
        return
    rows = state.setdefault('code_checkpoints', [])
    ident = 'code-' + util.digest({key: implementation.get(key) for key in
                                 ('task_id', 'contract_hash', 'source_revision')})[:24]
    existing = next((row for row in rows if row['id'] == ident), None)
    if existing is None:
        stage = next((row for row in reversed(state.get('stages') or [])
                      if row.get('task_id') == task['id'] and row.get('source_revision') == implementation['source_revision']
                      and row.get('stage', '').removesuffix('_report_repair') in ('terra', 'orchestrator')
                      and not row.get('rejected') and row.get('exit_code') == 0), {})
        if not stage.get('changed_files'):
            return
        base = {'id': ident, 'task_id': task['id'], 'contract_hash': implementation['contract_hash'],
                'source_revision': implementation['source_revision'], 'iteration': state.get('iteration'),
                'at': stage.get('finished_at') or util.now(), 'label': task.get('objective') or 'Saved code change',
                'milestone_id': task.get('milestone_id'), 'changed_files': deepcopy(stage['changed_files']),
                'finding_ids': [row['id'] for row in state.get('findings_ledger', []) if row.get('id')],
                'recorded_check': None}
        try:
            source = source_scope.snapshot(state['workspace'], state)
            if source['revision'] != implementation['source_revision']:
                return
            directory = Path(run_dir) / 'code-checkpoints'
            if directory.is_symlink():
                raise ValueError('Checkpoint storage must not be a symlink')
            directory.mkdir(parents=True, exist_ok=True)
            commit = capture_tree(state['workspace'], source, directory)
            manifest = {**base, 'commit': commit, 'source': source,
                        'findings_ledger': deepcopy(state.get('findings_ledger', [])),
                        'run_dir': str(Path(run_dir).resolve()), 'workspace': state['workspace']}
            path = directory / (ident + '.json')
            # The immutable manifest is published before its reference in state.
            if path.exists():
                saved = util.read(path)
                if saved['source'] != source or saved['task_id'] != task['id']:
                    raise ValueError('An existing checkpoint receipt conflicts with this step')
                manifest, commit = saved, saved['commit']
            else:
                util.atomic_json(path, manifest)
            ref = 'refs/autocode/code-checkpoints/' + util.digest(str(Path(run_dir).resolve()))[:20] + '/' + ident
            git(state['workspace'], 'update-ref', ref, commit)
            base.update(commit=commit, receipt=str(path), receipt_sha256=util.file_hash(path), available=True)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            # Saving work must not fail just because optional rollback storage is unavailable.
            # A failed receipt is visible, retained, and never presented as restorable.
            base.update(available=False, reason=str(error))
        rows.append(base)
        existing = base
    validation = state.get('validation') or {}
    if (validation.get('task_id') == task['id'] and validation.get('contract_hash') == existing['contract_hash']
            and validation.get('source_revision') == existing['source_revision']):
        existing['recorded_check'] = {'verdict': validation.get('verdict'),
                                      'at': next((r.get('finished_at') for r in reversed(state.get('stages', [])) if r.get('stage') in ('sol', 'astra_checkpoint') and r.get('source_revision') == existing['source_revision']), None),
                                      'scope': 'Historical independent verdict; fresh checks are required after restoration.'}


def blocked_reason(state):
    if state.get('status') not in ('PAUSED_REQUESTED', 'PAUSED_INTERVENTION'):
        return 'Pause after the current step before restoring a checkpoint.'
    if not approved(state):
        return 'Checkpoint restoration requires the same approved plan.'
    if any(state.get(key) for key in ('active_stage', 'active_runner_check', 'uncertain_artifacts',
                                     'pending_report_repair', 'orchestration_batch', 'pending_questions',
                                     'user_request', 'resolver_human_request', 'resolver_pending_human')):
        return 'Reconcile the active step, batch or human request before restoring.'
    if state.get('progressive') or state.get('parent_run') or (state.get('workflow') or {}).get('kind') not in (None, 'build'):
        return 'This checkpoint action currently supports ordinary build runs; this run uses a different execution contract.'
    if any(row.get('kind') == 'stop' for row in state.get('applied_interventions', [])):
        return 'A stopped run is terminal. Its saved work remains available.'
    return None


def project(state):
    reason = blocked_reason(state)
    return {'version': 1, 'blocked_reason': reason,
            'rows': [{key: deepcopy(row.get(key)) for key in
                      ('id', 'task_id', 'iteration', 'at', 'label', 'milestone_id', 'changed_files',
                       'source_revision', 'commit', 'available', 'reason', 'recorded_check')}
                     for row in state.get('code_checkpoints', [])],
            'continuation': deepcopy(state.get('checkpoint_continuation')),
            'restores': deepcopy(state.get('checkpoint_restores', []))}
