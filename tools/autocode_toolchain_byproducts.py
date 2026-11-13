"""Lockfiles a project's own toolchain writes while a stage runs it.

Cargo writes ``Cargo.lock`` beside ``Cargo.toml`` the first time any Cargo command resolves the
project. In a project that neither commits nor ignores it, a read-only stage that ran Cargo to
read the code therefore changed the repository, and the run paused for a person
(``PAUSED_STALE_VALIDATION``, a live Planner on Claude models, 2026-11-13); a Builder that ran
``cargo test`` was rejected for a file outside its assigned paths, on every retry.

Such a file is the tool's by-product, not an edit by the stage: nobody chose its content, and the
tool writes it again whenever it is missing. The runner removes it when the stage ends, before
the stage's source is measured, so the repository is as the stage found it. Only a file that was
absent when the stage began is ever removed. One that existed (committed or not) is the project's
own and is measured like any other file, and a Builder whose task owns the path keeps what it
wrote. Pure over the workspace and the stage's before-snapshot; imports nothing from the runner.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path, PurePosixPath

# The lockfile a toolchain creates on its own, and the manifest it resolves beside it.
LOCKFILES = {"Cargo.lock": "Cargo.toml"}


def _untracked(workspace) -> list[str]:
    """Untracked, unignored lockfile paths in ``workspace``, as the source snapshot would list them."""
    pathspecs = [spec for name in LOCKFILES for spec in (name, f":(glob)**/{name}")]
    try:
        listed = subprocess.run(
            ["git", "-C", str(workspace), "ls-files", "-z", "--others", "--exclude-standard", "--", *pathspecs],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return sorted(name for name in listed.stdout.split("\0") if name) if listed.returncode == 0 else []


def created(workspace, before_files) -> list[str]:
    """The lockfiles written since ``before_files`` (a snapshot's ``files``) beside their manifest."""
    root = Path(workspace)
    found = []
    for name in _untracked(root):
        path = PurePosixPath(name)
        target = root / name
        if (
            name in before_files
            or path.name not in LOCKFILES
            or target.is_symlink()
            or not target.is_file()
            or not (target.parent / LOCKFILES[path.name]).is_file()
        ):
            continue
        found.append(name)
    return found


def _owns(owned, name) -> bool:
    return any(name == root.rstrip("/") or name.startswith(root.rstrip("/") + "/") for root in owned)


def discard(workspace, before, *, owned=()) -> list[str]:
    """Remove the lockfiles a tool created during the stage; return their names.

    ``before`` is the stage's before-snapshot. ``owned`` is a writing stage's assigned paths: a
    lockfile inside them is part of its task and stays. Nothing is removed on any doubt.
    """
    files = (before or {}).get("files")
    if not workspace or not isinstance(files, dict):
        return []
    removed = []
    for name in created(workspace, files):
        if _owns(owned, name):
            continue
        try:
            os.unlink(Path(workspace) / name)
        except OSError:
            continue
        removed.append(name)
    return removed
