"""Reversible per-run dashboard archives. No runner state or worker is changed."""
from pathlib import Path
import threading
try:
    from .dashboard_projects import ProjectStore
except ImportError:  # Support direct execution from this source directory.
    from dashboard_projects import ProjectStore


class TaskArchiveStore(ProjectStore):
    filename = 'archived-tasks'
    collection = 'archived'
    identity_key = 'run'
    timestamp_key = 'archived_at'
    invalid_message = 'The saved task archive is invalid. Restore or repair archived-tasks.json, then retry.'

    def entries(self):
        return [{'run': row['workspace'], 'archived_at': row['removed_at']} for row in self.list()]


class TaskArchiveMixin:
    def __init__(self, *args, **kwargs):
        self._task_archive = None
        self._task_archive_lock = threading.RLock()
        super().__init__(*args, **kwargs)

    @property
    def task_archive(self):
        with self._task_archive_lock:
            if self._task_archive is None:
                self._task_archive = TaskArchiveStore(root=self._project_store_root)
            return self._task_archive

    def archived_task(self, raw, entries=None):
        entries = self.task_archive.entries() if entries is None else entries
        if not isinstance(raw, (str, Path)) or not str(raw):
            return None
        exact = next((row for row in entries if row['run'] == str(raw)), None)
        if exact:
            return exact
        try:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                return None
            canonical = str(path.resolve(strict=False))
        except (OSError, ValueError, RuntimeError):
            return None
        return next((row for row in entries if row['run'] == canonical), None)

    def archived_view(self, entry):
        run = Path(entry['run'])
        return {**entry, 'workspace': str(run.parent.parent.parent), 'task': run.name,
                'task_archived': True}

    def _require_unarchived(self, raw):
        if self.archived_task(raw):
            raise ValueError('Restore this archived task before changing or continuing it.')

    def require_unarchived_conversation(self, ident):
        doc = self.conversations.get(ident)
        self._require_unarchived((doc.get('attachment') or {}).get('run'))

    def task_archive_action(self, data):
        action = data.get('action')
        if action not in ('archive', 'restore'):
            raise ValueError('Choose archive or restore for the task')
        prior = self.archived_task(data.get('run'))
        if prior:
            run = Path(prior['run'])
        else:
            workspace = self.workspace_for(data.get('workspace'))
            run = self.run_for(workspace, data.get('run')) if workspace else None
            if not run:
                raise ValueError('Choose a task connected to this dashboard')
            if run.parent.name != 'runs' or run.parent.parent.name != '.autocode':
                raise ValueError('Task archive requires a direct run folder')
        if action == 'archive':
            self.task_archive.remove(str(run))
        else:
            self.task_archive.restore(str(run))
        return {'action': action, 'run': str(run), 'workspace': str(run.parent.parent.parent), 'archived': action == 'archive'}

    def dashboard_snapshot(self):
        data = super().dashboard_snapshot()
        entries = self.task_archive.entries()
        hidden = {row['run'] for row in entries}
        titles = {row.get('run'): row.get('task') for row in data['runs']}
        data['runs'] = [row for row in data['runs'] if row.get('run') not in hidden]
        data['conversations'] = [doc for doc in data['conversations'] if (doc.get('attachment') or {}).get('run') not in hidden]
        data['archived_tasks'] = [{**self.archived_view(row), 'task': titles.get(row['run']) or Path(row['run']).name} for row in entries]
        data['archived_conversations'] = [doc for doc in self.conversations.list(include_archived=True) if doc.get('archived_at')]
        return data

    def discover(self):
        entries = self.task_archive.entries()
        return [row for row in super().discover() if not self.archived_task(row.get('run'), entries)]

    def conversation_list(self):
        entries = self.task_archive.entries()
        return [doc for doc in super().conversation_list() if not self.archived_task((doc.get('attachment') or {}).get('run'), entries)]

    def conversation_get(self, ident):
        doc = self.conversations.get(ident)
        entry = self.archived_task((doc.get('attachment') or {}).get('run'))
        if entry:
            return {**doc, 'task_archived': True}
        return super().conversation_get(ident)

    def task_view(self, workspace, run):
        archived = self.archived_task(run)
        return self.archived_view(archived) if archived else super().task_view(workspace, run)

    def mutate(self, data):
        self._require_unarchived(data.get('run'))
        return super().mutate(data)

    def chat(self, data):
        self._require_unarchived(data.get('run'))
        return super().chat(data)

    def conversation_attach(self, data):
        self._require_visible_project(data.get('project') or data.get('workspace'))
        self.require_unarchived_conversation(data.get('id'))
        return super().conversation_attach(data)
