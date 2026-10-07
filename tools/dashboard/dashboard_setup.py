"""First-run guidance and explicit project creation; no installs or credentials."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess

try:
    from .dashboard_projects import ProjectStore
    from .dashboard_project_controls import conversation_workspace
except ImportError:
    from dashboard_projects import ProjectStore
    from dashboard_project_controls import conversation_workspace
try:
    from ..autocode_util import atomic_json
except ImportError:
    from autocode_util import atomic_json

_GUIDANCE = {
    'python': ('Python 3.11 or newer', 'Install Python 3.11+ and reinstall AutoCode with that interpreter.'),
    'psutil': ('Process inspection', 'Reinstall AutoCode with its declared dependencies, or use the repository virtual environment.'),
    'git': ('Git', 'Install Git using the instructions for your operating system.'),
    'engine:opencode': ('OpenCode', 'Install OpenCode 1.x or 2.x. Strict tool containment is qualified only for OpenCode 1.18.33.'),
    'engine:codex': ('Codex sign-in', 'Install Codex and sign in through Codex. Credentials stay with the provider.'),
    'engine': ('Selected model tool', 'Choose an installed model tool and complete its own sign-in.'),
    'workspace': ('Committed Git project', 'Choose an existing committed Git repository, or create a new project below.'),
    'workspace:clean': ('Uncommitted changes', 'Commit or stash your existing changes before attaching this project.'),
}


def canonical_project(raw, *, new=False):
    if not isinstance(raw, str) or not raw.strip() or '\0' in raw:
        raise ValueError('Enter an absolute project folder path.')
    path = Path(raw).expanduser()
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Enter an absolute project path without parent traversal.')
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('Choose a project folder directly, not through a symbolic link.')
    parent = path.parent.resolve(strict=True)
    if not parent.is_dir():
        raise ValueError('The parent folder must already exist.')
    result = parent / path.name
    if not new and not result.is_dir():
        raise ValueError('The selected project folder does not exist.')
    return result


def git(project, *args):
    """Only fixed argv reach Git; never return arbitrary tool output to setup UI."""
    try:
        result = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false', '-C', str(project), *args], capture_output=True,
                                text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError('Git is unavailable or did not finish. The project folder was preserved.') from error
    return result


def ready_project(project):
    top = git(project, 'rev-parse', '--show-toplevel')
    if top.returncode or top.stdout.strip() != str(project):
        raise ValueError('Choose the root folder of a Git repository.')
    if git(project, 'rev-parse', '--verify', 'HEAD').returncode:
        raise ValueError('The project needs its first commit before attaching.')
    clean = git(project, 'status', '--porcelain', '--untracked-files=all')
    if clean.returncode:
        raise ValueError('Git could not verify the project. No conversation was attached.')
    if clean.stdout.strip():
        raise ValueError('This project has uncommitted changes. Commit or stash them before attaching; your files were preserved.')


class SetupStore(ProjectStore):
    filename = 'project-setup-requests'

    def read(self):
        if not self._check_file(self.path):
            return {}
        data = json.loads(self.path.read_text())
        if not isinstance(data, dict):
            raise ValueError('The saved setup requests are invalid. Existing projects were preserved.')
        return data

    def write(self, data):
        self._check_file(self.path)
        atomic_json(self.path, data)


class SetupMixin:
    def _setup_scope_problem(self, ident):
        store = SetupStore(root=self._project_store_root)
        with store._guard():
            records = store.read()
        for record in records.values():
            if not isinstance(record, dict) or record.get('conversation_id') != ident:
                continue
            return self._setup_record_problem(record)
        return None

    @staticmethod
    def _setup_record_problem(record):
        try:
            path = canonical_project(record.get('path'))
            info = path.stat()
            valid = (record.get('identity') is None or record['identity'] == [info.st_dev, info.st_ino]) and (path / '.git').exists()
        except (OSError, ValueError):
            valid = False
        if not valid:
            return 'The saved project folder is missing or was replaced. Restore the original folder before continuing this conversation. All saved work is preserved.'
        return None

    def require_unarchived_conversation(self, ident):
        doc = self.conversations.get(ident)
        self._require_visible_project(conversation_workspace(doc))
        self._require_visible_project((doc.get('attachment') or {}).get('workspace'))
        if doc.get('project_scope_error'):
            raise ValueError(doc['project_scope_error'])
        problem = self._setup_scope_problem(ident)
        if problem:
            raise ValueError(problem)
        return super().require_unarchived_conversation(ident)

    def conversation_get(self, ident):
        doc = super().conversation_get(ident)
        problem = self._setup_scope_problem(ident)
        return {**doc, 'status': 'error', 'error': problem, 'project_scope_error': problem} if problem else doc

    def _workspaces_for_registry(self, registry):
        # A setup-created chat is usable before there is a run to register.
        # Persisted setup receipts provide membership across dashboard restarts;
        # never rediscover a replaced folder from its former path alone.
        known = super()._workspaces_for_registry(registry)
        store = SetupStore(root=self._project_store_root)
        with store._guard():
            records = store.read()
        for record in records.values():
            if not isinstance(record, dict) or record.get('status') != 'complete' or not record.get('conversation_id'):
                continue
            try:
                project = canonical_project(record.get('path'))
                info = project.stat()
                if (record.get('identity') is not None and record['identity'] != [info.st_dev, info.st_ino]) or not (project / '.git').exists():
                    continue
                if project not in known:
                    known.append(project)
            except (OSError, ValueError):
                continue
        return known

    def confirm_conversation_scope(self, data):
        if set(data) != {'id', 'token'}:
            raise ValueError('Confirm the exact project folder shown in chat.')
        doc = self.conversations.get(data['id'])
        self._require_visible_project(conversation_workspace(doc))
        problem = self._setup_scope_problem(data['id'])
        if problem:
            raise ValueError(problem)
        return self.conversations.confirm_project_scope(data['id'], data['token'])

    def conversation_attach(self, data):
        self._require_visible_project(data.get('project') or data.get('workspace'))
        self.require_unarchived_conversation(data.get('id'))
        return super().conversation_attach(data)

    def setup_check(self, data):
        if set(data) - {'workspace', 'engine'}:
            raise ValueError('Setup checks accept only a project path and model tool.')
        engine = data.get('engine', 'opencode')
        if engine not in ('opencode', 'codex'):
            raise ValueError('Choose OpenCode or Codex for the setup check.')
        raw = data.get('workspace')
        project = canonical_project(raw) if raw else None
        # No-project checks use the application source directory only as the
        # doctor's read-only cwd; the UI explicitly reports no selected project.
        workspace = project or Path(self.runner).resolve().parent
        report, error = self._json_command(['doctor', '--workspace', str(workspace), '--engine', engine, '--json'], timeout=120)
        if error and (error.get('uncertain') or error.get('exit_code') != 1):
            raise ValueError('Setup checks could not complete. Recheck when the runner is available.')
        if not isinstance(report, dict) or not isinstance(report.get('checks'), list):
            raise ValueError('This runner does not provide supported setup checks.')
        checks = []
        found = {row.get('name'): row for row in report['checks'] if isinstance(row, dict)}
        for name, (label, fix) in _GUIDANCE.items():
            if name.startswith('engine:') and name != 'engine:' + engine:
                continue
            row = found.get(name)
            if name == 'workspace:clean' and not row:
                continue
            status = row.get('status') if row else None
            if status not in ('ok', 'missing', 'warn'):
                status = 'unknown'
            if name.startswith('workspace') and project is None:
                status = 'missing'
            # Tool output can contain account names or unexpected wrapper text.
            # Return curated guidance, never raw stdout, stderr or credentials.
            checks.append({'id': name, 'label': label, 'status': status,
                           'guidance': '' if status == 'ok' else fix})
        if engine == 'opencode':
            checks.append({'id': 'opencode-accounts', 'label': 'OpenCode accounts',
                       'status': 'unverified', 'guidance': 'OpenCode accounts are managed by OpenCode. Use opencode auth list to inspect connected providers and opencode auth login to connect one. No credential is requested or saved here.'})
        return {'checked_at': datetime.now(timezone.utc).isoformat(), 'engine': engine,
                'workspace': str(project) if project else None, 'checks': checks,
                'scope': 'Local dependencies and project only; no model request was sent.'}

    def setup_project(self, data):
        if set(data) - {'mode', 'path', 'request_id', 'models'}:
            raise ValueError('Project setup does not accept credentials or model settings.')
        mode, ident = data.get('mode'), data.get('request_id')
        if mode not in ('existing', 'new') or not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', ident):
            raise ValueError('Choose a project mode and include a stable setup request ID.')
        models = data.get('models', {})
        allowed = {role + '_model' for role in ('glm', 'plan_reviewer', 'astra', 'terra', 'sol', 'completion')}
        allowed |= {role + '_reasoning_effort' for role in ('plan_reviewer', 'astra', 'terra', 'sol', 'completion')}
        if not isinstance(models, dict) or set(models) - allowed:
            raise ValueError('Choose models using the existing per-role settings. Credentials are not accepted.')
        chosen, efforts = self.joint_models(models), self.joint_efforts(models)
        settings = {role + '_model': value for role, value in chosen.items()}
        settings.update({role + '_reasoning_effort': value for role, value in efforts.items()})
        project = canonical_project(data.get('path'), new=mode == 'new')
        store = SetupStore(root=self._project_store_root)
        with store._guard(write=True):
            records = store.read()
            record = records.get(ident)
            if record and (record.get('mode'), record.get('path'), record.get('models', {})) != (mode, str(project), settings):
                raise ValueError('This setup request already names a different project. Use a new request.')
            if record and record.get('conversation_id'):
                problem = self._setup_record_problem(record)
                if problem:
                    raise ValueError(problem)
                self._require_visible_project(project)
                return {'conversation': self.conversations.get(record['conversation_id']),
                        'project': str(project), 'created': mode == 'new', 'replayed': True}
            if record is None:
                if mode == 'new' and os.path.lexists(project):
                    raise ValueError('That folder already exists. Choose an existing project or a new folder name.')
                record = {'mode': mode, 'path': str(project), 'models': settings, 'status': 'prepared'}
                records[ident] = record
                store.write(records)
                if mode == 'new':
                    project.mkdir(mode=0o700)
                    stat = project.stat()
                    record.update(status='created', identity=[stat.st_dev, stat.st_ino])
                    store.write(records)
            if mode == 'new':
                if not project.is_dir() or record.get('identity') != [project.stat().st_dev, project.stat().st_ino]:
                    raise ValueError('Ownership of the partially created folder is uncertain. It was preserved; inspect it before using a new setup request.')
                if any(child.name != '.git' for child in project.iterdir()):
                    raise ValueError('The new folder now contains files. They were preserved; finish setup as an existing project.')
                if not (project / '.git').exists():
                    if git(project, 'init', '-q').returncode:
                        raise ValueError('Git initialization failed. The new folder was preserved for retry.')
                top = git(project, 'rev-parse', '--show-toplevel')
                if top.returncode or top.stdout.strip() != str(project) or (project / '.git').is_symlink():
                    raise ValueError('The new folder is no longer the expected Git project. It was preserved.')
                if git(project, 'rev-parse', '--verify', 'HEAD').returncode:
                    result = git(project, '-c', 'user.name=AutoCode', '-c', 'user.email=autocode@localhost',
                                 '-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '--only', '-qm', 'Initialize project')
                    if result.returncode:
                        raise ValueError('The first commit did not finish. The project was preserved for retry.')
            ready_project(project)
            doc = self.conversations.create_empty(str(project), request_id='setup-' + ident, models=settings)
            info = project.stat()
            record.update(status='complete', conversation_id=doc['id'], identity=[info.st_dev, info.st_ino])
            store.write(records)
            return {'conversation': doc, 'project': str(project), 'created': mode == 'new', 'replayed': False}
