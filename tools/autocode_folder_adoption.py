"""Whether a folder of files outside Git is a project to work on where it is (owner decision 2026-11-13).

A task started in a folder of code that is not a Git repository got an empty private project
(``autocode_workspaces.bootstrap``), so every stage read an empty workspace: Requirements wrote that
it could not find the file the person named, and the Planner asked the person where the code was.
Such a folder is now made a repository where it is, with none of the person's files committed, and
the task works on the files there.

A folder that is not plainly one project keeps the private project, which changes nothing among the
person's files until a build is delivered: the person's home folder or a folder above it, a folder
that holds Git checkouts, a folder with no code in it, and one too large to record as a run's start.
The rule is decided here, from directory entries alone, and never asked of the person.

Reads directory entries and file sizes only: no Git command, no symlink followed, and the walk stops
at the first limit. Lower layer: imports nothing above ``autocode_project_kinds``.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from . import autocode_dependency_dirs as dependency_dirs
    from . import autocode_project_kinds as project_kinds
except ImportError:
    import autocode_dependency_dirs as dependency_dirs
    import autocode_project_kinds as project_kinds

FILES = 2000  # files, installed dependencies and caches left out
BYTES = 100 * 1024 * 1024
# Never the person's source: AutoCode's own store, installed dependencies and bytecode caches.
LEFT_OUT = frozenset({".git", ".autocode", ".autocode-ui", "__pycache__", *dependency_dirs.NAMES})
# What the new repository keeps out of Git without a .gitignore among the person's files, so a run's
# start (autocode_verify.commit_worktree) never copies installed dependencies into Git's store.
EXCLUDED = tuple(f"{name}/" for name in dependency_dirs.NAMES)

PROJECT, EMPTY, HOME, CHECKOUTS, NO_CODE, LARGE = "project", "empty", "home", "checkouts", "no_code", "large"
# Why a folder keeps the private project, as the end of "…, because it ".
BECAUSE = {
    HOME: "is your home folder or holds it",
    CHECKOUTS: "holds Git checkouts of its own",
    NO_CODE: "holds no code",
    LARGE: f"holds more than {FILES:,} files or {BYTES // (1024 * 1024)} MB",
}


def _above_home(folder: Path) -> bool:
    try:
        home = Path.home().resolve()
    except (OSError, RuntimeError):
        return False
    return folder == home or folder in home.parents


def reason(folder) -> str:
    """``PROJECT`` when a task works on the files of ``folder`` (not in Git) where they are, ``EMPTY`` when
    it holds nothing of the person's, else why it keeps a private project: a key of ``BECAUSE``."""
    folder = Path(folder).resolve()
    try:
        if not any(entry.name != ".DS_Store" for entry in folder.iterdir()):
            return EMPTY
    except OSError:
        return NO_CODE
    if _above_home(folder):
        return HOME
    files = size = 0
    code = False
    for directory, names, filenames in os.walk(folder):
        if directory != str(folder) and (".git" in names or ".git" in filenames):
            return CHECKOUTS
        names[:] = sorted(name for name in names if name not in LEFT_OUT)
        for name in filenames:
            path = os.path.join(directory, name)
            try:
                size += os.lstat(path).st_size
            except OSError:
                continue
            files += 1
            if files > FILES or size > BYTES:
                return LARGE
            code = code or os.path.splitext(name)[1] in project_kinds.CODE_SUFFIXES
    return PROJECT if code else NO_CODE


def in_place(folder) -> bool:
    """Whether a task in ``folder`` (not in Git, holding files) works on those files where they are."""
    return reason(folder) == PROJECT
