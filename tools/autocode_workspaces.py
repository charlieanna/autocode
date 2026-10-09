"""One Git worktree and branch per task; source checkout locks stay independent."""
from __future__ import annotations

import contextlib
import fcntl
import json
import re
import subprocess
import uuid
from pathlib import Path

# Everything AutoCode keeps in a project (run state, logs, evidence, nested task
# worktrees) lives in directories that ignore themselves, as .pytest_cache and venvs
# do, so `git add -A` in the user's checkout never stages them.
IGNORE_EVERYTHING = "# Created by AutoCode: its run state, logs and worktrees stay out of Git.\n*\n"


def keep_out_of_git(root, name='.autocode'):
    """Create ``root/name`` with a ``.gitignore`` that ignores the whole directory.

    An existing ``.gitignore`` there is left exactly as it is.
    """
    directory = Path(root) / name
    directory.mkdir(parents=True, exist_ok=True)
    try:
        with (directory / '.gitignore').open('x') as handle:
            handle.write(IGNORE_EVERYTHING)
    except FileExistsError:
        pass
    return directory


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True)
    if result.returncode:
        raise ValueError(result.stderr.strip() or 'Git command failed')
    return result.stdout.strip()


@contextlib.contextmanager
def _creating(project):
    """Serialize task worktree creation across autocode processes in one project.

    Two tasks started at the same moment each run `git worktree add` on the same repository, and git
    does not guarantee that is safe (ref and worktree-metadata locks): one can fail, and the CLI then
    exits 2 without a run. autocode_program serializes its own threads the same way (WORKTREE_LOCK).
    """
    # Beside .autocode/worktrees, not in it: everything in there is read as a worktree.
    with (keep_out_of_git(project) / "worktree-create.lock").open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


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
    keep_out_of_git(project)
    parent.mkdir(parents=True, exist_ok=True)
    workspace = parent / name
    branch = 'autocode/' + name
    with _creating(project):
        git(project, 'worktree', 'add', '-b', branch, str(workspace), base)
    # kind 'task': this worktree and its branch belong to one task (autocode_worktrees
    # delivers to and cleans up only these; programs write this file without a kind).
    data = {'version': 1, 'kind': 'task', 'project_workspace': str(project), 'workspace': str(workspace),
            'branch': branch, 'base_commit': base}
    artifact = keep_out_of_git(workspace) / 'task-workspace.json'
    artifact.write_text(json.dumps(data, indent=2) + '\n')
    return data


def bootstrap(selected, task):
    """Create a local Git-backed task project without taking over user files.

    An empty folder is a safe project root. A populated folder (including a
    home directory) receives a new child project, leaving its existing files
    and Git state untouched. A missing path is refused so a mistyped
    --workspace cannot create directories, and a folder inside an existing
    repository is refused so no nested repository is created in it.
    """
    selected = Path(selected).resolve()
    if not selected.is_dir():
        raise ValueError(f'workspace does not exist: {selected}')
    inside = subprocess.run(['git', '-C', str(selected), 'rev-parse', '--show-toplevel'],
                            capture_output=True, text=True)
    if inside.returncode == 0:
        raise ValueError(f'workspace is inside the Git repository {inside.stdout.strip()}; '
                         'select that repository root instead')
    if any(entry.name != '.DS_Store' for entry in selected.iterdir()):
        name = (re.sub('[^a-z0-9]+', '-', task.lower()).strip('-') or 'task')[:40]
        project = selected / 'autocode-projects' / f'{name}-{uuid.uuid4().hex[:8]}'
        project.mkdir(parents=True)
    else:
        project = selected
    git(project, 'init', '-q')
    git(project, '-c', 'user.name=Autocode', '-c', 'user.email=autocode@localhost',
        'commit', '--allow-empty', '-qm', 'Autocode project baseline')
    return project


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
