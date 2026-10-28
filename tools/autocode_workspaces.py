"""One Git worktree and branch per task; source checkout locks stay independent."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import contextlib
import json
import os
import re
import stat
import subprocess
import uuid
from pathlib import Path

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util

# Everything AutoCode keeps in a project (run state, logs, evidence, nested task
# worktrees) lives in directories that ignore themselves, as .pytest_cache and venvs
# do, so `git add -A` in the user's checkout never stages them.
IGNORE_EVERYTHING = "# Created by AutoCode: its run state, logs and worktrees stay out of Git.\n*\n"
# .autocode/ also holds the settings a team commits for its new runs (autocode_project_config).
KEEP_PROJECT_SETTINGS = "# The project's AutoCode settings are committed like any other file.\n!/config.toml\n"
KEEP_PROJECT_MEMORY = "# Reviewable learned project conventions may be shared with the team.\n!/project.json\n"


def keep_out_of_git(root, name=".autocode"):
    """Create ``root/name`` with a ``.gitignore`` that ignores the whole directory, apart from
    ``.autocode/config.toml`` and learned ``project.json``.

    An exact older generated template is upgraded; custom ignore rules are preserved.
    """
    directory = Path(root) / name
    directory.mkdir(parents=True, exist_ok=True)
    try:
        with (directory / ".gitignore").open("x") as handle:
            handle.write(
                IGNORE_EVERYTHING + (KEEP_PROJECT_SETTINGS + KEEP_PROJECT_MEMORY if name == ".autocode" else "")
            )
    except FileExistsError:
        if name == ".autocode":
            _migrate_project_ignore(directory)
    return directory


def _migrate_project_ignore(directory):
    """Upgrade only an exact old generated template; preserve custom ignore rules."""
    parent = descriptor = None
    temporary = None
    try:
        parent = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptor = os.open(".gitignore", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > 4096:
            return
        raw = os.read(descriptor, 4097)
        old = (IGNORE_EVERYTHING, IGNORE_EVERYTHING + KEEP_PROJECT_SETTINGS)
        if raw not in tuple(text.encode() for text in old):
            return
        temporary = ".project-ignore-" + uuid.uuid4().hex
        handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(handle, "wb") as stream:
            stream.write((IGNORE_EVERYTHING + KEEP_PROJECT_SETTINGS + KEEP_PROJECT_MEMORY).encode())
            stream.flush()
            os.fsync(stream.fileno())
        current = os.stat(".gitignore", dir_fd=parent, follow_symlinks=False)

        def identity(info):
            return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

        if identity(before) != identity(current):
            return
        os.replace(temporary, ".gitignore", src_dir_fd=parent, dst_dir_fd=parent)
        temporary = None
        os.fsync(parent)
    except OSError:
        pass  # Ignore migration is advisory; never replace a redirect or a user's custom rules.
    finally:
        if temporary is not None and parent is not None:
            with contextlib.suppress(OSError):
                os.unlink(temporary, dir_fd=parent)
        if descriptor is not None:
            os.close(descriptor)
        if parent is not None:
            os.close(parent)


git = util.git  # hooks off and a time limit; see autocode_util.git


@contextlib.contextmanager
def creating(project):
    """Serialize task worktree creation across autocode processes in one project.

    Two tasks started at the same moment each run `git worktree add` on the same repository, and git
    does not guarantee that is safe (ref and worktree-metadata locks): one can fail, and the CLI then
    exits 2 without a run.
    """
    # Beside .autocode/worktrees, not in it: everything in there is read as a worktree.
    with (keep_out_of_git(project) / "worktree-create.lock").open("a+") as handle:
        util.lock_file(handle, blocking=True)
        yield


def metadata(workspace):
    path = Path(workspace) / ".autocode/task-workspace.json"
    if not path.is_file() or path.is_symlink():
        return None
    data = json.loads(path.read_text())
    project = Path(data["project_workspace"]).resolve()
    root = Path(workspace).resolve()
    if data.get("workspace") != str(root) or not root.is_relative_to(project / ".autocode/worktrees"):
        raise ValueError("Task worktree metadata does not match its project")
    common = lambda p: Path(git(p, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    if common(project) != common(root):
        raise ValueError("Task worktree no longer belongs to the selected project")
    return data


def create(project, task):
    project = Path(project).resolve()
    previous = metadata(project)
    if previous:
        project = Path(previous["project_workspace"])
    if Path(git(project, "rev-parse", "--show-toplevel")).resolve() != project:
        raise ValueError("Select the root of a Git checkout")
    try:
        base = git(project, "rev-parse", "--verify", "HEAD")
    except ValueError as error:
        raise ValueError(
            "Create an initial Git commit before starting an isolated task: "
            f"git -C {project} commit --allow-empty -m 'Start' (autocode doctor checks this too)"
        ) from error
    name = (re.sub("[^a-z0-9]+", "-", task.lower()).strip("-") or "task")[:40]
    name += "-" + uuid.uuid4().hex[:10]
    parent = project / ".autocode/worktrees"
    if not parent.resolve().is_relative_to(project):
        raise ValueError("Task worktree storage must stay inside the selected project")
    keep_out_of_git(project)
    parent.mkdir(parents=True, exist_ok=True)
    workspace = parent / name
    branch = "autocode/" + name
    with creating(project):
        git(project, "worktree", "add", "-b", branch, str(workspace), base)
    # kind 'task': this worktree and its branch belong to one task (autocode_worktrees
    # delivers to and cleans up only these; worktrees an archived `autocode program` made have no kind).
    data = {
        "version": 1,
        "kind": "task",
        "project_workspace": str(project),
        "workspace": str(workspace),
        "branch": branch,
        "base_commit": base,
    }
    artifact = keep_out_of_git(workspace) / "task-workspace.json"
    artifact.write_text(json.dumps(data, indent=2) + "\n")
    return data


def discard_unused(data) -> bool:
    """Remove a task worktree and branch this invocation created when no run was saved in it.

    A launch that stops before its first save (a missing provider, a refused setting) leaves
    nothing to resume: ``autocode runs`` cannot list it and ``clean-worktrees`` finds no
    complete run, so each retry would leave another worktree and branch behind. A worktree
    holding a saved run, a change, or a branch moved off its base is kept.
    """
    if not data:
        return False
    tree = Path(data["workspace"])
    try:
        if any((tree / ".autocode" / "runs").glob("*/state.json")):
            return False
        if git(tree, "status", "--porcelain", "--untracked-files=all"):
            return False
        if git(tree, "rev-parse", "HEAD") != data["base_commit"]:
            return False
        project = Path(data["project_workspace"])
        git(project, "worktree", "remove", "--force", str(tree))
        git(project, "branch", "-D", data["branch"])
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    return True


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
        raise ValueError(f"workspace does not exist: {selected}")
    inside = subprocess.run(
        ["git", "-C", str(selected), "rev-parse", "--show-toplevel"], capture_output=True, text=True
    )
    if inside.returncode == 0:
        raise ValueError(
            f"workspace is inside the Git repository {inside.stdout.strip()}; select that repository root instead"
        )
    if any(entry.name != ".DS_Store" for entry in selected.iterdir()):
        name = (re.sub("[^a-z0-9]+", "-", task.lower()).strip("-") or "task")[:40]
        project = selected / "autocode-projects" / f"{name}-{uuid.uuid4().hex[:8]}"
        project.mkdir(parents=True)
    else:
        project = selected
    git(project, "init", "-q")
    git(
        project,
        "-c",
        "user.name=Autocode",
        "-c",
        "user.email=autocode@localhost",
        "commit",
        "--allow-empty",
        "-qm",
        "Autocode project baseline",
    )
    return project


def resume_workspace(selected, state: RunState):
    selected = Path(selected).resolve()
    saved = Path(state["workspace"]).resolve()
    if selected == saved:
        return saved
    if state.get("project_workspace") != str(selected):
        raise ValueError("workspace differs from checkpoint; use the original project or task worktree")
    data = metadata(saved)
    if not data or data["project_workspace"] != str(selected):
        raise ValueError("Saved task worktree is missing or does not belong to this project")
    return saved
