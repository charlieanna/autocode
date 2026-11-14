"""Installed dependency directories: third-party code a package manager writes, never the task's product code.

A project that does not ignore its node_modules shows one `npm install` as thousands of untracked changed
files. Counted as changed product lines, one install made 274,546 obligations, and the Tester's handoff
(68 MB) and report schema (32 MB) were too large to launch (#3164).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path, PurePosixPath

NAMES = ("node_modules", ".venv", "venv")
REASON = "Installed third-party code, not product code the task changed: no test or reason is required."
# Where a scratch tree keeps its views of installed packages: AutoCode's own directory there.
VIEWS = ".autocode/dependencies"


def installed(path: str) -> bool:
    """Whether ``path`` is a file inside an installed dependency directory, at any depth."""
    return any(part in NAMES for part in PurePosixPath(path).parts[:-1])


def summary(paths, *, listed: int = 20) -> dict:
    """The record of the installed files an inventory left out: their count and (the first ``listed``)
    dependency directories, never every path."""
    chosen = [path for path in paths if installed(path)]
    directories = set()
    for path in chosen:
        parts = PurePosixPath(path).parts
        depth = next(index for index, part in enumerate(parts[:-1]) if part in NAMES)
        directories.add("/".join(parts[: depth + 1]))
    return {"files": len(chosen), "directories": sorted(directories)[:listed], "reason": REASON}


def expose(original, target, view) -> None:
    """Let the scratch copy at ``target`` use what is installed at ``original``, the person's own directory.

    A link to ``node_modules`` itself hands a command run in the copy the person's installed packages:
    `npm ci` empties the directory behind the link before it installs, so one planned check deleted every
    package in the workspace, and each later check failed with "Cannot find module". The copy gets a
    directory of its own, ``view``, instead: a link to each installed package and a copy of each file the
    package manager keeps beside them (`npm install` rewrites `.package-lock.json`). Emptying, replacing or
    reinstalling the copy's ``node_modules`` then changes ``view`` and nothing the person installed.
    ``target`` is still one link, so the copy's files look as they did. A virtualenv or a vendor directory
    is linked whole, as before: its layout does not survive being rebuilt entry by entry.
    """
    original, target, view = Path(original), Path(target), Path(view)
    if original.name != "node_modules":
        target.symlink_to(original, target_is_directory=True)
        return
    view.mkdir(parents=True)
    with os.scandir(original) as entries:
        for entry in entries:
            if entry.is_file(follow_symlinks=False):
                shutil.copy2(entry.path, view / entry.name)
            else:
                (view / entry.name).symlink_to(entry.path, target_is_directory=entry.is_dir())
    target.symlink_to(view, target_is_directory=True)


def view_in(tree, package=".") -> Path:
    """Where the scratch ``tree`` keeps its view of the ``node_modules`` of ``package`` (a directory in it).

    Under AutoCode's own directory in the tree, which Git is told to ignore there, so no listing of the
    tree's files shows the view, and removing the tree removes it."""
    views = Path(tree) / VIEWS
    views.mkdir(parents=True, exist_ok=True)
    ignore = views / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n")
    return views / package / "node_modules"
