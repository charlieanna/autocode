"""Reversible dashboard project removal; never changes repositories or runner state."""
from pathlib import Path
import threading

try:
    from .dashboard_projects import ProjectStore
except ImportError:  # Support direct execution from this source directory.
    from dashboard_projects import ProjectStore


def compact_run(row):
    """Project only the fields used by task lists; detail has its own endpoint."""
    if not isinstance(row, dict) or not row.get('run'):
        return row
    goal = row.get('goal') if isinstance(row.get('goal'), dict) else {}
    body = goal.get('body') if isinstance(goal.get('body'), dict) else {}
    active = row.get('active_stage') if isinstance(row.get('active_stage'), dict) else {}
    stage_keys = ('stage', 'role', 'route_role', 'started_at', 'finished_at', 'duration_seconds',
                  'exit_code', 'rejected', 'interrupted', 'timed_out')
    keep = ('workspace', 'project_workspace', 'task_branch', 'run', 'created_at', 'task', 'phase',
            'status', 'stage', 'iteration', 'state_error', 'error', 'stop_reason', 'goal_token',
            'questions', 'user_request', 'review_token', 'review_criteria', 'human_reviews',
            'model_settings', 'monitor')
    result = {key: row.get(key) for key in keep if key in row}
    result['goal'] = {key: goal.get(key) for key in ('origin', 'revision', 'approval_status') if key in goal}
    if 'intended_outcome' in body:
        result['goal']['body'] = {'intended_outcome': body['intended_outcome']}
    result['active_stage'] = {key: active.get(key) for key in stage_keys if key in active}
    result['stages'] = [{key: stage.get(key) for key in stage_keys if key in stage}
                        for stage in row.get('stages', []) if isinstance(stage, dict)]
    return result


class ProjectRemovalMixin:
    def __init__(self, *args, project_store_root=None, **kwargs):
        self._project_store = None
        self._project_store_lock = threading.RLock()
        self._project_store_root = project_store_root
        if project_store_root is None and kwargs.get('conversation_root') is not None:
            self._project_store_root = Path(kwargs['conversation_root']).parent
        super().__init__(*args, **kwargs)

    @property
    def project_preferences(self):
        with self._project_store_lock:
            if self._project_store is None:
                self._project_store = ProjectStore(root=self._project_store_root)
            return self._project_store

    def removed_projects(self):
        return self.project_preferences.list()

    def removed_project(self, raw, removed=None):
        if not isinstance(raw, (str, Path)) or not str(raw).strip():
            return None
        raw = str(raw)
        entries = self.removed_projects() if removed is None else removed
        # Keep exact saved identities removable/restorable even if a folder no
        # longer exists or a symlink has since been repointed.
        exact = next((row for row in entries if row['workspace'] == raw), None)
        if exact:
            return exact
        try:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                return None
            canonical = str(path.resolve(strict=False))
        except (ValueError, OSError, RuntimeError):
            return None
        return next((row for row in entries if row['workspace'] == canonical), None)

    def _require_visible_project(self, raw):
        if self.removed_project(raw):
            raise ValueError('This project was removed from the dashboard. Restore it in Settings before starting or changing its tasks.')

    def project_action(self, data):
        action, raw = data.get('action'), data.get('workspace')
        if action not in ('remove', 'restore'):
            raise ValueError('Choose remove or restore for the project')
        if not isinstance(raw, str) or not raw.strip() or '\0' in raw:
            raise ValueError('An absolute project path is required')
        try:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                raise ValueError('An absolute project path is required')
            prior = self.removed_project(raw)
            workspace = prior['workspace'] if prior else str(path.resolve(strict=False))
        except (OSError, RuntimeError) as error:
            raise ValueError('The project path could not be resolved. Check the folder path and symbolic links.') from error
        if action == 'remove':
            if prior is None:
                known = {str(item) for item in self.workspaces}
                known.update(row['workspace'] for row in super().discover() if isinstance(row.get('workspace'), str))
                if workspace not in known:
                    raise ValueError('That project is not connected to this dashboard')
            self.project_preferences.remove(workspace)
        else:
            self.project_preferences.restore(workspace)
        return {'action': action, 'workspace': workspace, 'removed': action == 'remove'}

    def dashboard_snapshot(self):
        with self.pin_registry():
            removed = self.removed_projects()
            visible = lambda raw: self.removed_project(raw, removed) is None
            workspaces = [path for path in self.workspaces if visible(path)]
            return {
                'workspaces': [str(path) for path in workspaces],
                'runs': [compact_run(row) for row in super().discover() if visible(row.get('workspace'))],
                'workspace_actions': {str(path): self.action_log(path) for path in workspaces},
                'watch_roots': self.watch_root_rows(),
                'registry': self.registry_status(),
                'zai': bool(self.zai_probe()),
                'conversations': [doc for doc in super().conversation_list()
                                  if visible((doc.get('attachment') or {}).get('workspace'))],
                'removed_projects': removed,
            }

    def discover(self):
        removed = self.removed_projects()
        return [row for row in super().discover() if not self.removed_project(row.get('workspace'), removed)]

    def conversation_list(self):
        removed = self.removed_projects()
        return [doc for doc in super().conversation_list()
                if not self.removed_project((doc.get('attachment') or {}).get('workspace'), removed)]

    def conversation_get(self, ident):
        doc = self.conversations.get(ident)
        if self.removed_project((doc.get('attachment') or {}).get('workspace')):
            return {**doc, 'project_removed': True}
        return super().conversation_get(ident)

    def task_view(self, workspace, run):
        if self.removed_project(workspace):
            return {'project_removed': True, 'workspace': str(workspace), 'run': str(run), 'task': 'Removed project'}
        return super().task_view(workspace, run)

    def mutate(self, data):
        self._require_visible_project(data.get('workspace'))
        return super().mutate(data)

    def chat(self, data):
        self._require_visible_project(data.get('workspace'))
        return super().chat(data)

    def create(self, data):
        project = data.get('project')
        raw = project if isinstance(project, str) and project.strip() else data.get('workspace')
        self._require_visible_project(raw)
        return super().create(data)

    def conversation_attach(self, data):
        self._require_visible_project(data.get('project') or data.get('workspace'))
        doc = self.conversations.get(data.get('id'))
        self._require_visible_project((doc.get('attachment') or {}).get('workspace'))
        return super().conversation_attach(data)
