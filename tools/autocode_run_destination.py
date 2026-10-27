"""Reserve an exact fresh task-run directory without rebinding saved artifacts.

The new-run interface calls this; resumptions keep their existing paths. A
client may name the destination under the folder it selected even when AutoCode
bootstraps a child project there: the name is kept and only the store moves, so
the client's path stays a valid handle (resolve_relocated() finds the run it
names). No launch, lock, state, or provider authority is implied by an empty
directory.
"""

from __future__ import annotations

import json
import os
from contextlib import ExitStack, suppress
from pathlib import Path


def reserve(workspace: Path, destination: Path, *, named_from: Path | None = None) -> Path:
    """Exclusively create one immediate child of the checkout's run store.

    ``named_from`` is the folder the caller selected when ``workspace`` is a
    child project bootstrapped inside it. The destination was then named under
    ``named_from/.autocode/runs``; its name is reserved under the bootstrapped
    project's store instead. Reject links and existing destinations, including
    empty ones. In particular, never normalize a symlink into another run or
    overwrite an earlier attempt.
    """
    workspace = Path(workspace).resolve()
    destination = Path(destination).absolute()
    store = workspace / ".autocode" / "runs"
    if ".." in destination.parts or destination.name in ("", ".", ".."):
        raise ValueError("destination must be an immediate child of workspace/.autocode/runs")
    if destination.parent != store:
        # The workspace is resolved; the destination is not. Resolve only its
        # parent so a symlinked ancestor (macOS /tmp, /var) matches the store,
        # while the run directory itself is still never followed.
        selected = Path(named_from).resolve() if named_from is not None else None
        named_under_selected = selected is not None and destination.parent.resolve() == selected / ".autocode" / "runs"
        if not named_under_selected and destination.parent.resolve() != store:
            raise ValueError("destination must be an immediate child of workspace/.autocode/runs")
    # Keep the run under the resolved store so its state names the same workspace
    # the run finder derives from the path.
    destination = store / destination.name
    for path in (workspace / ".autocode", store, destination):
        if path.is_symlink():
            raise ValueError("fresh run destination must not follow a symlink")
    # Pin each directory and refuse links on open, so replacing a pathname
    # during admission cannot redirect this write outside the run store.
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with ExitStack() as stack:
        fd = os.open(workspace, flags)
        stack.callback(os.close, fd)
        for name in (".autocode", "runs"):
            with suppress(FileExistsError):
                os.mkdir(name, dir_fd=fd)
            fd = os.open(name, flags, dir_fd=fd)
            stack.callback(os.close, fd)
        os.mkdir(destination.name, dir_fd=fd)
        identity = os.fstat(fd)
        current = store.stat(follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
            raise ValueError("run store changed during fresh admission")
    return destination


def discard_unsaved(run_dir: Path | None) -> bool:
    """Remove a fresh run directory the launch never saved a checkpoint in: at most its ``writer.lock``.

    A launch refused after the directory was reserved and locked but before ``state.json`` (a missing
    engine, an unsupported host) otherwise leaves a directory that run listings show as unreadable for
    ever. Anything else in it, a symlink, or a missing directory is left alone; never raises."""
    if run_dir is None:
        return False
    run_dir = Path(run_dir)
    try:
        if run_dir.is_symlink() or not run_dir.is_dir():
            return False
        entries = list(run_dir.iterdir())
    except OSError:
        return False
    if any(entry.name != "writer.lock" or entry.is_symlink() or not entry.is_file() for entry in entries):
        return False
    try:
        for entry in entries:
            entry.unlink()
        run_dir.rmdir()
    except OSError:
        return False
    return True


def resolve_relocated(workspace: Path, run_dir: Path) -> tuple[Path, Path] | None:
    """The run a destination named under ``workspace`` denotes after relocation.

    A client names a fresh run under the folder it selected. When bootstrap()
    moved that task into ``autocode-projects/``, the named path never exists;
    the run's name under a bootstrapped project's store is the stable part of
    the handle. Return ``(project, run_dir)`` when exactly one bootstrapped
    project holds a run of that name, and None otherwise.
    """
    workspace = Path(workspace).resolve()
    named = Path(run_dir).absolute()
    if ".." in named.parts or named.parent != workspace / ".autocode" / "runs" or not named.name:
        return None
    projects = workspace / "autocode-projects"
    try:
        if projects.is_symlink() or not projects.is_dir():
            return None
        children = sorted(projects.iterdir())
    except OSError:
        return None
    found = []
    for project in children:
        store = project / ".autocode" / "runs"
        candidate = store / named.name
        state_path = candidate / "state.json"
        if (
            project.is_symlink()
            or not project.is_dir()
            or not (project / ".git").exists()
            or store.is_symlink()
            or not store.is_dir()
            or store.resolve() != store
            or candidate.is_symlink()
            or state_path.is_symlink()
            or not state_path.is_file()
            or _saved_workspace(state_path) != str(project.resolve())
        ):
            continue
        found.append((project.resolve(), candidate))
    return found[0] if len(found) == 1 else None


def _saved_workspace(state_path: Path) -> str | None:
    try:
        state = json.loads(state_path.read_text())
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return state.get("workspace") if isinstance(state, dict) else None
