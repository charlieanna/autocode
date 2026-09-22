"""One Git worktree and branch per task; source checkout locks stay independent."""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import uuid


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True)
    if result.returncode:
        raise ValueError(result.stderr.strip() or 'Git command failed')
    return result.stdout.strip()


def metadata(workspace):
    path = Path(workspace) / '.autocode/task-workspace.json'
    if not path.is_file() or path.is_symlink():
        return None
    data = json.loads(path.read_text())
    project = Path(data['project_workspace']).resolve()
    root = Path(workspace).resolve()
    if data.get('workspace') != str(root) or not root.is_relative_to(project / '.autocode/worktrees'):
        raise ValueError('Task worktree metadata does not match its project')
    common = lambda p: Path(git(p, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve()
    if common(project) != common(root):
        raise ValueError('Task worktree no longer belongs to the selected project')
    return data


def create(project, task):
    project = Path(project).resolve()
    previous = metadata(project)
    if previous:
        project = Path(previous['project_workspace'])
    if Path(git(project, 'rev-parse', '--show-toplevel')).resolve() != project:
        raise ValueError('Select the root of a Git checkout')
    try:
        base = git(project, 'rev-parse', '--verify', 'HEAD')
    except ValueError as error:
        raise ValueError('Create an initial Git commit before starting an isolated task') from error
    name = (re.sub('[^a-z0-9]+', '-', task.lower()).strip('-') or 'task')[:40]
    name += '-' + uuid.uuid4().hex[:10]
    parent = project / '.autocode/worktrees'
    if not parent.resolve().is_relative_to(project):
        raise ValueError('Task worktree storage must stay inside the selected project')
    parent.mkdir(parents=True, exist_ok=True)
    workspace = parent / name
    branch = 'autocode/' + name
    git(project, 'worktree', 'add', '-b', branch, str(workspace), base)
    data = {'version': 1, 'project_workspace': str(project), 'workspace': str(workspace),
            'branch': branch, 'base_commit': base}
    artifact = workspace / '.autocode/task-workspace.json'
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps(data, indent=2) + '\n')
    return data


def resume_workspace(selected, state):
    selected = Path(selected).resolve()
    saved = Path(state['workspace']).resolve()
    if selected == saved:
        return saved
    if state.get('project_workspace') != str(selected):
        raise ValueError('workspace differs from checkpoint; use the original project or task worktree')
    data = metadata(saved)
    if not data or data['project_workspace'] != str(selected):
        raise ValueError('Saved task worktree is missing or does not belong to this project')
    return saved
