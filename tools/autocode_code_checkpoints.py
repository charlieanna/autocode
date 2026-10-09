"""Immutable, source-bound code checkpoints owned by the runner.

Writes only ``code_checkpoints`` (an append-only source receipt list, read by the
status view and checkpoint CLI). A checkpoint is a Git object, not a claim of
acceptance. The real index, HEAD and working files are never changed. Metadata
from older runs cannot be upgraded into missing source snapshots.
"""
from __future__ import annotations

try:
    from . import autocode_source_scope as source_scope
    from . import autocode_source_snapshot as source_snapshot
except ImportError:
    import autocode_source_scope as source_scope
    import autocode_source_snapshot as source_snapshot


import contextlib
import hashlib
import os
import subprocess
import tempfile
from copy import deepcopy
from pathlib import Path

try:
    from . import autocode_contract_identity as contract
    from . import autocode_repair_provenance as repair_provenance
    from . import autocode_util as util
except ImportError:
    import autocode_contract_identity as contract
    import autocode_repair_provenance as repair_provenance
    import autocode_util as util


def approved(state):
    try:
        return contract.approved(state)
    except (KeyError, TypeError, ValueError):
        return False


def _git_argv(workspace, *args):
    return ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
            '-C', str(workspace), *args]


def git(workspace, *args, env=None, data=None):
    proc = subprocess.run(_git_argv(workspace, *args), input=data, capture_output=True,
                          env={**os.environ, **(env or {})})
    if proc.returncode:
        raise ValueError(proc.stderr.decode(errors='replace').strip() or 'Git checkpoint operation failed')
    return proc.stdout


def content_files(snapshot):
    return {name: value for name, value in snapshot['files'].items() if value != 'deleted'}


def _read_blob_identity(output, oid, mode):
    """Read one length-framed batch reply without retaining a whole regular blob."""
    header = output.readline(256)
    fields = header[:-1].split(b' ')
    if (not header.endswith(b'\n') or len(fields) != 3
            or fields[0] != oid.encode() or fields[1] != b'blob'
            or not fields[2].isdigit()):
        raise ValueError('Git checkpoint batch returned an invalid blob header')
    remaining = int(fields[2])
    digest = hashlib.sha256()
    target = bytearray() if mode == '120000' else None
    while remaining:
        data = output.read(min(remaining, 64 * 1024))
        if not data:
            raise ValueError('Git checkpoint batch returned a truncated blob')
        remaining -= len(data)
        if target is None:
            digest.update(data)
        else:
            target.extend(data)
    if output.read(1) != b'\n':
        raise ValueError('Git checkpoint batch returned an invalid blob terminator')
    return ('symlink:' + target.decode() if target is not None else
            ('executable:' if mode == '100755' else '') + digest.hexdigest())


def tree_files(workspace, revision):
    """Compare actual blobs, modes and links through one streamed Git batch."""
    entries = []
    for entry in git(workspace, 'ls-tree', '-rz', revision).split(b'\0'):
        if not entry:
            continue
        meta, name = entry.split(b'\t', 1)
        mode, kind, oid = meta.decode().split()
        if kind != 'blob':
            raise ValueError('Checkpoint restoration requires ordinary files; nested Git repositories are unsupported')
        entries.append((name.decode(), mode, oid))
    result = {}
    if not entries:
        return result
    # One request and response at a time avoids full-tree buffering and pipe
    # backpressure. Stderr uses a file so it cannot block a large stdout reply.
    with tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(_git_argv(workspace, 'cat-file', '--batch'),
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors)
        try:
            for name, mode, oid in entries:
                proc.stdin.write(oid.encode() + b'\n')
                proc.stdin.flush()
                result[name] = _read_blob_identity(proc.stdout, oid, mode)
            proc.stdin.close()
            if proc.stdout.read(1):
                raise ValueError('Git checkpoint batch returned unexpected trailing output')
            if proc.wait():
                errors.seek(0)
                raise ValueError(errors.read().decode(errors='replace').strip()
                                 or 'Git checkpoint operation failed')
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            for pipe in (proc.stdin, proc.stdout):
                with contextlib.suppress(OSError):
                    pipe.close()
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


def _saved_file(path):
    return type(path) is str and bool(path) and Path(path).is_file()


def _saved_original_report(original):
    output = original.get('output')
    if _saved_file(output) or _saved_file(original.get('response_text')):
        return True
    # Older OpenCode attempts saved the terminal response under this exact stem
    # before the repair handler recorded response_text on the pending copy.
    return (original.get('engine') == 'opencode' and type(output) is str and bool(output)
            and _saved_file(str(Path(output).with_suffix('.response.txt'))))


def builder_execution(state, accepted, implementation):
    """Resolve a repaired handoff to its runner-observed implementation step.

    A report-only turn has no source changes of its own. Its accepted, hash-pinned
    repair receipt identifies the original command; model-declared paths are never
    used. Missing or ambiguous provenance cannot create a checkpoint.
    """
    if not accepted.get('report_only') and accepted.get('stage') in ('terra', 'orchestrator'):
        return accepted
    stage = accepted.get('original_stage')
    receipts = [row for row in state.get('report_repair_history', [])
                if row.get('result') == 'accepted' and row.get('repair') == accepted]
    if (not accepted.get('report_only') or stage not in ('terra', 'orchestrator')
            or accepted.get('stage') != stage + '_report_repair'
            or type(accepted.get('exit_code')) is not int or accepted['exit_code'] != 0
            or accepted.get('rejected') or accepted.get('abandoned') or accepted.get('timed_out')
            or accepted.get('changed_files') != [] or len(receipts) != 1
            or type(accepted.get('applied_original_events')) is not str
            or not accepted['applied_original_events']):
        raise ValueError('Accepted Builder repair lacks unique read-only provenance')
    receipt = repair_provenance.verify_accepted_repair(state, accepted)
    originals = [row for row in state.get('stages', [])
                 if row.get('output') == receipt.get('original_output')
                 and row.get('events') == accepted['applied_original_events']]
    if len(originals) != 1:
        raise ValueError('Accepted Builder repair lacks a unique original execution')
    original = originals[0]
    if (original.get('stage') != stage or original.get('report_only')
            or type(original.get('exit_code')) is not int or original['exit_code'] != 0
            or original.get('abandoned') or original.get('timed_out')
            or any(type(accepted.get(key)) is not str or not accepted[key]
                   or accepted[key] != implementation.get(key)
                   for key in ('task_id', 'contract_hash', 'source_revision'))
            or any(original.get(key) != accepted.get(key) for key in
                   ('task_id', 'contract_hash', 'source_revision', 'role', 'iteration',
                    'schema', 'criteria_revision'))
            or not original.get('role') or not original.get('schema')
            or type(original.get('iteration')) is not int
            or not _saved_file(original.get('events'))
            or not _saved_original_report(original)
            or type(original.get('changed_files')) is not list
            or any(type(path) is not str or not path for path in original['changed_files'])):
        raise ValueError('Accepted Builder repair has mismatched original execution provenance')
    return original


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
        if not stage:
            return
        base = {'id': ident, 'task_id': task['id'], 'contract_hash': implementation['contract_hash'],
                'source_revision': implementation['source_revision'], 'iteration': state.get('iteration'),
                'at': stage.get('finished_at') or util.now(), 'label': task.get('objective') or 'Saved code change',
                'milestone_id': task.get('milestone_id'), 'changed_files': [],
                'finding_ids': [row['id'] for row in state.get('findings_ledger', []) if row.get('id')],
                'recorded_check': None}
        try:
            execution = builder_execution(state, stage, implementation)
            if not execution.get('changed_files'):
                return
            base['changed_files'] = deepcopy(execution['changed_files'])
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
