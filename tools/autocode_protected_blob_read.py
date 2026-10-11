"""Fresh descriptor-anchored reads of hash-named protected blobs.

One call shares only directory traversal, never authenticated bytes or authority
with another call. Every regular leaf is opened without following a symlink,
read once, hash checked, and rechecked before the directory descriptor closes.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path


_SHA = re.compile(r"[0-9a-f]{64}")


def _identity(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _directories(directory):
    result = {}
    for path in (directory, *directory.parents):
        value = path.lstat()
        if not stat.S_ISDIR(value.st_mode):
            raise ValueError("Protected blob directory has an aliased or non-directory ancestor")
        # Shared outer directories may legitimately change membership. Their
        # device/inode/type remain exact; the blob directory's full stamp is
        # checked separately below.
        result[path] = (value.st_dev, value.st_ino, value.st_mode)
    return result


def read(directory, names):
    """Return verified bytes keyed by exact SHA256 leaf name, failing closed.

    This is filesystem custody only. Native publication, approval, semantics
    and dispatch remain the caller's independently checked responsibilities.
    """
    directory = Path(directory)
    if not directory.is_absolute() or ".." in directory.parts:
        raise ValueError("Protected blob directory must be an absolute canonical path")
    names = tuple(names)
    if any(not isinstance(name, str) or _SHA.fullmatch(name) is None for name in names):
        raise ValueError("Protected blob names must be exact SHA256 leaves")
    names = tuple(dict.fromkeys(names))
    ancestors = _directories(directory)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        initial_directory = _identity(os.fstat(descriptor))
        if _identity(directory.lstat()) != initial_directory or _directories(directory) != ancestors:
            raise ValueError("Protected blob directory changed before read")
        result, identities = {}, {}
        for name in names:
            leaf = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
            try:
                original = os.fstat(leaf)
                if not stat.S_ISREG(original.st_mode):
                    raise ValueError("Protected blob is not a regular file")
                chunks = []
                while part := os.read(leaf, 1024 * 1024):
                    chunks.append(part)
                raw = b"".join(chunks)
                if _identity(os.fstat(leaf)) != _identity(original) or hashlib.sha256(raw).hexdigest() != name:
                    raise ValueError("Protected blob bytes changed")
                identities[name] = _identity(original)
                result[name] = raw
            finally:
                os.close(leaf)
        for name, identity in identities.items():
            if _identity(os.stat(name, dir_fd=descriptor, follow_symlinks=False)) != identity:
                raise ValueError("Protected blob was replaced or edited during read")
        if (_identity(os.fstat(descriptor)) != initial_directory
                or _identity(directory.lstat()) != initial_directory
                or _directories(directory) != ancestors):
            raise ValueError("Protected blob directory changed during read")
        return result
    finally:
        os.close(descriptor)
