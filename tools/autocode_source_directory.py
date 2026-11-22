"""Observe the pinned contents of a repository-local fixture directory."""

from __future__ import annotations

import hashlib
import os
import stat
from collections import deque
from contextlib import contextmanager
from pathlib import Path, PurePosixPath, PureWindowsPath

_INTERNAL = frozenset({".git", ".autocode", ".autocode-ui"})
_MAX_LINKS = 8


def _supported() -> bool:
    return (
        all(hasattr(os, flag) for flag in ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK"))
        and {os.open, os.readlink, os.stat} <= os.supports_dir_fd
        and os.stat in os.supports_follow_symlinks
        and os.listdir in os.supports_fd
    )


@contextmanager
def _directory(root: Path, path: PurePosixPath):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(root, flags)
    try:
        for part in path.parts:
            if part == ".." or part.casefold() in _INTERNAL:
                raise ValueError("Source path leaves the source inventory")
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def link_is_bound(root, name, files, tracked, checked_directories, *, allow_untracked, code_suffixes):
    """Observe every component of a relative fixture link against the pinned tree."""
    path = PurePosixPath(name)
    if not _supported() or path.is_absolute() or not path.parts:
        return False
    parent = PurePosixPath()
    todo = deque(path.parts)
    visited = []
    try:
        while todo:
            part = todo.popleft()
            if part in (".", ""):
                continue
            if part == "..":
                if not parent.parts:
                    return False
                parent = parent.parent
                continue
            if part.casefold() in _INTERNAL:
                return False
            current = parent / part
            with _directory(root, parent) as directory:
                info = os.stat(part, dir_fd=directory, follow_symlinks=False)
                identity = files.get(current.as_posix())
                if stat.S_ISLNK(info.st_mode):
                    if len(visited) >= _MAX_LINKS or current.as_posix() not in tracked or not identity:
                        return False
                    target = os.readlink(part, dir_fd=directory)
                    if (
                        identity != "symlink:" + target
                        or not target
                        or target.startswith("/")
                        or "\\" in target
                        or PureWindowsPath(target).drive
                    ):
                        return False
                    visited.append(current)
                    todo.extendleft(reversed(target.split("/")))
                elif stat.S_ISDIR(info.st_mode):
                    parent = current
                else:
                    return bool(
                        not todo
                        and identity
                        and stat.S_ISREG(info.st_mode)
                        and (allow_untracked or current.as_posix() in tracked)
                        and _entry(directory, part, identity)
                    )
        if not parent.parts or any(path.suffix in code_suffixes or path.is_relative_to(parent) for path in visited):
            return False
        relative = parent.as_posix()
        if relative not in checked_directories:
            checked_directories[relative] = matches(root, relative, files)
        return checked_directories[relative]
    except (OSError, ValueError, RuntimeError):
        return False


def matches(root: Path, relative: str, files: dict[str, str]) -> bool:
    """Compare complete directory membership and identities without following links."""
    name = PurePosixPath(relative)
    if (
        not name.parts
        or name.is_absolute()
        or any(part == ".." or part.casefold() in _INTERNAL for part in name.parts)
        or not _supported()
    ):
        return False
    listing: dict[PurePosixPath, dict[str, str | None]] = {}
    prefix = name.as_posix() + "/"
    for path, identity in files.items():
        if identity == "deleted" or not path.startswith(prefix):
            continue
        member = PurePosixPath(path)
        listing.setdefault(member.parent, {})[member.name] = identity
        for parent in member.parents:
            if parent == name:
                break
            listing.setdefault(parent.parent, {})[parent.name] = None
    if not listing:
        return False
    try:
        with _directory(root, name) as directory:
            return _members(directory, name, listing, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except (OSError, ValueError, RecursionError):
        return False


def _members(
    directory: int, path: PurePosixPath, listing: dict[PurePosixPath, dict[str, str | None]], flags: int
) -> bool:
    expected = listing[path]
    if set(os.listdir(directory)) != expected.keys():
        return False
    for name, identity in expected.items():
        if identity is None:
            child = os.open(name, flags, dir_fd=directory)
            try:
                if not _members(child, path / name, listing, flags):
                    return False
            finally:
                os.close(child)
        elif not _entry(directory, name, identity):
            return False
    return True


def _entry(directory: int, name: str, identity: str) -> bool:
    if identity.startswith("symlink:"):
        return os.readlink(name, dir_fd=directory) == identity.removeprefix("symlink:")
    leaf = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(leaf, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return False
        prefix = "executable:" if info.st_mode & 0o111 else ""
        return prefix + hashlib.file_digest(stream, "sha256").hexdigest() == identity
