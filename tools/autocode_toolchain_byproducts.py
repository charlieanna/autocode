"""Lockfiles and build directories a project's own toolchain writes while a stage runs it.

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
wrote.

A build directory is the same kind of file, many times over. Maven writes ``target/``, Gradle
``build/`` and ``.gradle/``, Cargo ``target/``, and a CMake project is configured into ``build/``.
In a project that does not ignore the directory, a Builder that ran the project's tests was
refused for every file its build tool wrote there: a real C++ bug fix ended
``PAUSED_INVALID_OUTPUT`` with a correct fix, and the same fix in a Maven project took two refused
Builder attempts and twice the cost (Claude models, 2026-11-14). Files a tool wrote into such a
directory, beside the manifest it builds, are removed the same way, and only when the directory
held no file when the stage began: one that did is the project's own.

Pure over the workspace and the stage's before-snapshot; imports nothing from the runner.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
from pathlib import Path, PurePosixPath

try:
    from . import autocode_project_kinds as project_kinds
except ImportError:
    import autocode_project_kinds as project_kinds

# The lockfile a toolchain creates on its own, and the manifest it resolves beside it: each language's
# ``lockfiles`` in autocode_project_kinds.
LOCKFILES = project_kinds.LOCKFILES
# The directory a build tool creates on its own, and the manifests it builds beside it: each
# language's ``build_dirs`` in autocode_project_kinds.
BUILD_DIRS = project_kinds.BUILD_DIRS


def _untracked(workspace, pathspecs=None) -> list[str]:
    """Untracked, unignored paths in ``workspace`` under ``pathspecs`` (the lockfiles when None), as the
    source snapshot would list them."""
    pathspecs = pathspecs or [spec for name in LOCKFILES for spec in (name, f":(glob)**/{name}")]
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


def _build_dir(root, name) -> str | None:
    """The outermost build directory the file ``name`` lies in: a BUILD_DIRS name beside its manifest."""
    parts = PurePosixPath(name).parts
    for index, part in enumerate(parts[:-1]):
        if any(root.joinpath(*parts[:index], manifest).is_file() for manifest in BUILD_DIRS.get(part, ())):
            return "/".join(parts[: index + 1])
    return None


def built(workspace, before_files) -> dict[str, list[str]]:
    """The build directories written since ``before_files``, each with the untracked files in it.

    A directory that held any file the snapshot lists (committed or not) is the project's own, and so
    is one reached through a link."""
    root = Path(workspace)
    pathspecs = [spec for name in BUILD_DIRS for spec in (f"{name}/", f":(glob)**/{name}/**")]
    found: dict[str, list[str]] = {}
    for name in _untracked(root, pathspecs):
        if directory := _build_dir(root, name):
            found.setdefault(directory, []).append(name)
    return {
        directory: names
        for directory, names in found.items()
        if not any(existing.startswith(directory + "/") for existing in before_files)
        and not any((root / parent).is_symlink() for parent in (directory, *map(str, PurePosixPath(directory).parents)))
    }


def _owns(owned, name) -> bool:
    return any(name == root.rstrip("/") or name.startswith(root.rstrip("/") + "/") for root in owned)


def _touches(owned, directory) -> bool:
    """Whether an assigned path is ``directory``, holds it or lies inside it."""
    return _owns(owned, directory) or any(root.rstrip("/").startswith(directory + "/") for root in owned)


def discard(workspace, before, *, owned=()) -> list[str]:
    """Remove the lockfiles and build output a tool created during the stage; return the lockfiles'
    names and each build directory as ``NAME/``.

    ``before`` is the stage's before-snapshot. ``owned`` is a writing stage's assigned paths: a
    lockfile inside them, or a build directory they touch, is part of its task and stays. Nothing is
    removed on any doubt.
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
    for directory, names in sorted(built(workspace, files).items()):
        if _touches(owned, directory):
            continue
        for name in names:
            try:
                os.unlink(Path(workspace) / name)
            except OSError:
                continue
        # The directories the files were in, deepest first; one that still holds a file (an ignored
        # one, or one that could not be removed) stays.
        for current, _dirs, _files in os.walk(Path(workspace) / directory, topdown=False):
            with contextlib.suppress(OSError):
                os.rmdir(current)
        removed.append(directory + "/")
    return removed
