"""Explicit, scoped deletion of stopped dashboard tasks; never edits run state."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import uuid

try:
    from .dashboard_projects import ProjectStore
except ImportError:
    from dashboard_projects import ProjectStore


def canonical(raw):
    if not isinstance(raw, str) or not raw or '\0' in raw:
        raise ValueError('Choose an absolute canonical path')
    path = Path(raw)
    if not path.is_absolute() or '..' in path.parts or str(path.resolve()) != raw:
        raise ValueError('Symbolic links and path traversal are not allowed for deletion')
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError('Symbolic links are not allowed for deletion')
    return path


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError(result.stderr.strip() or 'Git cleanup failed')
    return result.stdout.strip()


def remaining_scope(preview):
    """Inspect every selected resource; an unreadable Git repository is not absence."""
    result = []
    for item in preview['scope']:
        if item['kind'] == 'branch':
            managed = preview['managed']
            reference = 'refs/heads/' + managed['branch']
            present = reference in git(managed['project'], 'for-each-ref', '--format=%(refname)', reference).splitlines()
        else:
            present = os.path.lexists(item['path'])
        if present:
            result.append(item)
    return result


def completed_scope(preview):
    # Older completed receipts are authoritative about what was removed. Older
    # partial receipts did not journal branch removal and cannot prove its result.
    if preview['status'] == 'deleted':
        return preview['scope']
    return preview.get('completed_scope', [item for item in preview.get('deleted', []) if item['kind'] != 'branch'])


def inventory(path):
    """Bind confirmation to actual directory identity and contents, without reading run state."""
    entries = []
    if not path.exists():
        raise ValueError('Selected deletion scope no longer exists; refresh the preview')
    for current, directories, files in os.walk(path, followlinks=False):
        for item in [Path(current), *(Path(current) / name for name in directories + files)]:
            if item.is_symlink():
                raise ValueError('Deletion scope contains a symbolic link: ' + str(item))
            info = item.stat()
            if item.is_file():
                entries.append((str(item.relative_to(path)), info.st_ino, info.st_size, info.st_mtime_ns))
            elif item.is_dir():
                entries.append((str(item.relative_to(path)), info.st_ino, 'directory', 0))
            else:
                raise ValueError('Deletion scope contains an unsupported special file: ' + str(item))
    root = path.stat()
    return {'root': [root.st_dev, root.st_ino], 'files': {row[0]: list(row[1:]) for row in entries}}


@contextmanager
def stopped_locks(workspace, run):
    """Take the same nonblocking writer locks as the native runner."""
    handles = []
    try:
        for path in (workspace / '.autocode/writer.lock', run / 'writer.lock'):
            canonical(str(path))
            descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            handles.append(descriptor)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('A runner still owns this task or workspace') from None
        yield
    finally:
        for descriptor in reversed(handles):
            os.close(descriptor)


class DeleteStore(ProjectStore):
    filename = 'permanent-deletions'

    def read(self):
        if not self._check_file(self.path):
            return {}
        data = json.loads(self.path.read_text())
        if not isinstance(data, dict):
            raise ValueError('Invalid deletion receipts; repair the dashboard data store')
        return data

    def write(self, records):
        temporary = self.root / ('.deletions-' + uuid.uuid4().hex + '.tmp')
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w') as handle:
                json.dump(records, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)


class PermanentDeleteMixin:
    @property
    def deletions(self):
        return DeleteStore(root=self._project_store_root)

    def dashboard_snapshot(self):
        data = super().dashboard_snapshot()
        store = self.deletions
        with store._guard():
            records = store.read()
        fields = ('preview_id', 'title', 'workspace', 'run', 'scope', 'confirmation',
                  'status', 'remaining', 'deleted', 'errors', 'retry_blocked',
                  'include_worktree', 'include_branch', 'can_include_worktree',
                  'can_include_branch', 'worktree_unavailable_reason')
        # A removed run cannot supply its own retry control after a restart.
        # Keep incomplete durable requests discoverable without reading it.
        data['pending_deletions'] = [{key: row[key] for key in fields if key in row}
                                    for row in records.values()
                                    if row.get('status') in ('partial', 'deleting')]
        return data

    def _deletion_stopped(self, workspace, run):
        if str(run) in self.pending or str(workspace) in self.workspace_busy:
            raise ValueError('A dashboard action is still active for this task')
        data, error = self._json_command(['--workspace', str(workspace), '--run-dir', str(run), '--status'], timeout=30)
        if error or not data or data.get('workspace') != str(workspace) or data.get('run_dir') != str(run):
            raise ValueError('Task status is unavailable or uncertain; deletion is blocked')
        status = data.get('status', '')
        if not (status.startswith(('PAUSED_', 'BLOCKED_')) or status in ('TASK_COMPLETE', 'COMPLETE', 'WAITING_FOR_USER', 'AWAITING_GOAL_APPROVAL')):
            raise ValueError('Stop the task and reconcile its workers before deletion')
        if data.get('stale') or data.get('active_stage') or (data.get('view') or {}).get('runner_check'):
            raise ValueError('Active or uncertain task work must be reconciled before deletion')
        batch = data.get('orchestration_batch') or {}
        if batch and batch.get('status') not in ('complete', 'completed'):
            raise ValueError('Parallel task work must be reconciled before deletion')
        for key in ('active_stage_workers', 'runner_check_workers'):
            workers = data.get(key)
            if workers and (workers.get('alive') or not workers.get('checked')):
                raise ValueError('Active or uncertain workers prevent deletion')
        result = subprocess.run(['ps', '-axo', 'pid=,command='], capture_output=True, text=True, timeout=5)
        if result.returncode:
            raise ValueError('Worker inspection unavailable; deletion is blocked')
        for line in result.stdout.splitlines():
            try:
                pid, command = line.strip().split(None, 1)
                if int(pid) == os.getpid():
                    continue
                args = shlex.split(command)
            except ValueError:
                continue
            if any(arg == str(run) or arg.startswith(str(run) + '/') or arg == str(workspace) for arg in args):
                raise ValueError('A live process still references this task or workspace')
        return data

    def _managed_scope(self, workspace, run):
        metadata = workspace / '.autocode/task-workspace.json'
        canonical(str(metadata))
        if not metadata.is_file():
            raise ValueError('This is a shared or unmanaged project; only its selected run may be deleted')
        data = json.loads(metadata.read_text())
        project = canonical(data.get('project_workspace'))
        if (data.get('kind') != 'task' or data.get('workspace') != str(workspace)
                or workspace.parent != project / '.autocode/worktrees'):
            raise ValueError('Exclusive managed worktree ownership could not be verified')
        if list((workspace / '.autocode/runs').iterdir()) != [run]:
            raise ValueError('The worktree contains other run artifacts; delete only the selected run')
        if (workspace / '.autocode/worktrees').exists():
            raise ValueError('The worktree contains nested workspaces')
        branch = data.get('branch')
        if not isinstance(branch, str) or not branch.startswith('autocode/') or git(workspace, 'symbolic-ref', '--short', 'HEAD') != branch:
            raise ValueError('The dedicated task branch could not be verified')
        if git(project, 'rev-parse', '--path-format=absolute', '--git-common-dir') != git(workspace, 'rev-parse', '--path-format=absolute', '--git-common-dir'):
            raise ValueError('Worktree belongs to a different repository')
        records = git(project, 'worktree', 'list', '--porcelain').split('\n\n')
        owners = [entry for entry in records if 'branch refs/heads/' + branch in entry.splitlines()]
        if len(owners) != 1 or 'worktree ' + str(workspace) not in owners[0].splitlines():
            raise ValueError('The task branch is shared or worktree registration changed')
        return {'project': str(project), 'branch': branch, 'branch_head': git(project, 'rev-parse', 'refs/heads/' + branch)}

    def deletion_preview(self, data):
        workspace, run = canonical(data.get('workspace')), canonical(data.get('run'))
        if self.workspace_for(str(workspace)) != workspace or self.run_for(workspace, str(run)) != run:
            raise ValueError('Choose a task connected to this dashboard')
        if run.parent != workspace / '.autocode/runs':
            raise ValueError('Only the selected direct run folder may be deleted')
        include_worktree = data.get('include_worktree', False)
        include_branch = data.get('include_branch', False)
        if type(include_worktree) is not bool or type(include_branch) is not bool or include_branch and not include_worktree:
            raise ValueError('Branch deletion requires its exclusively owned worktree scope')
        with stopped_locks(workspace, run):
            status = self._deletion_stopped(workspace, run)
            managed, reason = None, None
            try:
                managed = self._managed_scope(workspace, run)
            except (ValueError, OSError) as error:
                reason = str(error)
            if include_worktree and not managed:
                raise ValueError(reason)
            scope = [{'kind': 'run', 'path': str(run)}]
            if include_worktree:
                scope.append({'kind': 'worktree', 'path': str(workspace)})
            if include_branch:
                scope.append({'kind': 'branch', 'path': managed['branch']})
            title = ((status.get('view') or {}).get('evidence') or {}).get('outcome') or run.name
            preview = {'preview_id': uuid.uuid4().hex, 'title': str(title)[:240], 'workspace': str(workspace),
                       'run': str(run), 'scope': scope, 'include_worktree': include_worktree,
                       'include_branch': include_branch, 'managed': managed if include_worktree else None,
                       'can_include_worktree': bool(managed), 'can_include_branch': bool(managed), 'worktree_unavailable_reason': reason,
                       'fingerprint': inventory(workspace if include_worktree else run), 'status': 'preview',
                       'deleted': [], 'remaining': scope, 'errors': [],
                       'receipt_version': 2, 'completed_scope': []}
            # The short challenge identifies this exact, immutable path preview.
            # Requiring the user to retype every absolute path hides the action
            # on small screens and adds no authority beyond the saved preview.
            preview['confirmation'] = 'DELETE ' + preview['preview_id'][:12]
        store = self.deletions
        with store._guard(write=True):
            records = store.read()
            records[preview['preview_id']] = preview
            store.write(records)
        return preview

    def _forget_deleted(self, preview):
        result, error = self._json_command(['registry', 'forget-deleted', '--workspace', preview['workspace'], '--run-dir', preview['run'], '--json'])
        if error:
            raise ValueError('Files removed but discovery cleanup failed: ' + error['message'])
        self.task_archive.restore(preview['run'])
        if preview['include_worktree']:
            self.project_preferences.restore(preview['workspace'])
        self.registry_cache['at'] = 0
        self.discovery_cache.clear()

    def delete_permanently(self, data):
        store = self.deletions
        with store._guard(write=True):
            records = store.read()
            preview = records.get(data.get('preview_id'))
            if not preview or data.get('confirmation') != preview['confirmation']:
                raise ValueError('Type the confirmation code for this exact task and deletion scope')
            def record_step(kinds):
                finished = completed_scope(preview)
                preview['completed_scope'] = [item for item in preview['scope'] if item in finished or item['kind'] in kinds]
                preview['remaining'] = remaining_scope(preview)
                preview['deleted'] = [item for item in preview['scope'] if item not in preview['remaining']]
                store.write(records)

            try:
                present = remaining_scope(preview)
                finished = completed_scope(preview)
                recreated = [item for item in present if item in finished]
                if recreated:
                    preview.update(status='partial', remaining=present, completed_scope=finished,
                                   deleted=[item for item in preview['scope'] if item not in present], retry_blocked=True,
                                   errors=['A previously removed path or branch has been recreated. The old confirmation cannot delete new work.'])
                    store.write(records)
                    return preview
                if preview['status'] == 'deleted':
                    return preview
                if preview.get('retry_blocked'):
                    return preview
            except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                # In particular, an unreadable repository cannot produce a
                # successful replay of a completed branch-deletion receipt.
                return {**preview, 'status': 'partial', 'remaining': preview['scope'],
                        'errors': ['Deletion scope could not be inspected: ' + str(error)]}
            workspace, run = canonical(preview['workspace']), canonical(preview['run'])
            target = workspace if preview['include_worktree'] else run
            try:
                if target.exists():
                    with stopped_locks(workspace, run):
                        self._deletion_stopped(workspace, run)
                        actual = inventory(target)
                        original = preview['fingerprint']
                        unchanged = actual == original
                        if preview.get('execution_started'):
                            unchanged = actual['root'] == original['root'] and all(original['files'].get(key) == value for key, value in actual['files'].items())
                        if not unchanged:
                            raise ValueError('Deletion scope changed since preview; request a new preview')
                        if preview['include_worktree'] and self._managed_scope(workspace, run) != preview['managed']:
                            raise ValueError('Managed ownership changed since preview')
                        preview['status'] = 'deleting'
                        preview['execution_started'] = True
                        store.write(records)
                        if preview['include_worktree']:
                            git(preview['managed']['project'], 'worktree', 'remove', '--force', str(workspace))
                        else:
                            shutil.rmtree(run)
                elif not preview.get('execution_started'):
                    raise ValueError('Deletion scope disappeared before confirmation; refresh status')
                if target.exists():
                    raise ValueError('Deletion incomplete; selected files remain')
                record_step({'run', 'worktree'} if preview['include_worktree'] else {'run'})
                if preview['include_worktree']:
                    managed = preview['managed']
                    entries = git(managed['project'], 'worktree', 'list', '--porcelain').split('\n\n')
                    if any('worktree ' + str(workspace) in entry.splitlines() for entry in entries):
                        git(managed['project'], 'worktree', 'remove', '--force', str(workspace))
                if preview['include_branch']:
                    managed = preview['managed']
                    references = git(managed['project'], 'for-each-ref', '--format=%(refname) %(objectname)', 'refs/heads/' + managed['branch'])
                    if references:
                        if preview.get('branch_removal_started') or preview.get('receipt_version') != 2:
                            preview['retry_blocked'] = True
                            raise ValueError('The earlier branch removal has no durable result. Branch retained; the old confirmation cannot safely retry it.')
                        if references != 'refs/heads/' + managed['branch'] + ' ' + managed['branch_head']:
                            preview['retry_blocked'] = True
                            raise ValueError('Branch changed since confirmation; branch retained')
                        preview['branch_removal_started'] = True
                        store.write(records)
                        try:
                            git(managed['project'], 'branch', '-D', '--', managed['branch'])
                        except ValueError:
                            # A returned Git refusal can be retried. A timeout,
                            # crash or failed receipt write keeps the ambiguity
                            # marker, so it cannot authorize a second removal.
                            preview['branch_removal_started'] = False
                            store.write(records)
                            raise
                    record_step({'branch'})
                if target.exists():
                    raise ValueError('Deletion incomplete; selected files remain')
                if remaining_scope(preview):
                    raise ValueError('Deletion incomplete; a selected path or branch still exists')
                self._forget_deleted(preview)
                preview.update(status='deleted', deleted=preview['scope'], remaining=[], errors=[],
                               deleted_at=datetime.now(timezone.utc).isoformat())
            except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                if not preview.get('execution_started'):
                    raise ValueError(str(error)) from error
                # Keep the exact receipt retryable. Never convert a missing response into success.
                preview.update(status='partial', errors=[str(error)])
                try:
                    preview['remaining'] = remaining_scope(preview)
                except (ValueError, OSError, subprocess.TimeoutExpired) as inspection_error:
                    preview['remaining'] = preview['scope']
                    preview['errors'].append('Scope inspection unavailable: ' + str(inspection_error))
                preview['deleted'] = [item for item in preview['scope'] if item not in preview['remaining']]
            store.write(records)
            return preview
