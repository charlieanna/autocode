"""Source-level identities for traceback locations; no result grants proof authority.

The caller must independently bind the runner's traceback and every executing
source input to the exact execution before comparing these identities. A stamp
for one file does not establish that another executing file was unchanged.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import stat
from contextlib import ExitStack
from pathlib import Path

MAX_SOURCE_BYTES = 2_000_000
MAX_NODES = 100_000


def _stamp(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink]


def _read_captured(root, name, expected):
    """Read bounded, pinned source without following a file or parent link."""
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY") or os.open not in os.supports_dir_fd:
        return None
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        return None
    relative = Path(name)
    if (
        not relative.parts
        or relative.is_absolute()
        or relative.as_posix() != name
        or any(part.casefold() in ("", ".", "..", ".git", ".autocode", ".autocode-ui") for part in relative.parts)
    ):
        return None
    try:
        root = Path(root)
        if root.is_symlink():
            return None
        with ExitStack() as opened:
            parent = os.open(root.resolve(strict=True), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            opened.callback(os.close, parent)
            directories = [parent]
            for part in relative.parts[:-1]:
                parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                opened.callback(os.close, parent)
                directories.append(parent)
            descriptor = os.open(relative.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(descriptor, "rb") as handle:
                before_dirs = [_stamp(os.fstat(directory)) for directory in directories]
                before = os.fstat(handle.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_SOURCE_BYTES:
                    return None
                content = handle.read(MAX_SOURCE_BYTES + 1)
                after = os.fstat(handle.fileno())
                after_dirs = [_stamp(os.fstat(directory)) for directory in directories]
            actual = ("executable:" if before.st_mode & 0o111 else "") + hashlib.sha256(content).hexdigest()
            if (
                len(content) > MAX_SOURCE_BYTES
                or actual != expected
                or _stamp(before) != _stamp(after)
                or before_dirs != after_dirs
            ):
                return None
            return {"text": content.decode("utf-8"), "stamp": {"file": _stamp(before), "directories": before_dirs}}
    except (OSError, ValueError, TypeError, UnicodeError, RuntimeError, OverflowError):
        return None


def source_stamp(root, name, expected):
    """Capture a source file and its ancestor identities before the owned execution."""
    observed = _read_captured(root, name, expected)
    return observed["stamp"] if observed else None


def pinned_identity(root, name, line, expected, before):
    """Return a site only if pinned source and every ancestor remain unchanged."""
    observed = _read_captured(root, name, expected)
    if not observed or before is None or observed["stamp"] != before:
        return None
    return identity(observed["text"], line, path=name)


def identity(source, line, *, path):
    """Identify one unique statement within an unambiguous lexical scope, or hold.

    Repeated identical statements can be distinguished only while the entire
    enclosing scope is identical; this prevents a shifted occurrence from being
    equated with another failure site. The caller retains all exception text.
    """
    if (
        not isinstance(path, str)
        or not path
        or "\\" in path
        or "\x00" in path
        or not Path(path).parts
        or Path(path).is_absolute()
        or Path(path).as_posix() != path
        or any(part.casefold() in ("", ".", "..", ".git", ".autocode", ".autocode-ui") for part in Path(path).parts)
        or not isinstance(source, str)
        or type(line) is not int
        or line < 1
    ):
        return None
    try:
        if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
            return None
        root = ast.parse(source)
        scopes = {(): root}
        repeated_scopes = set()
        entries = []
        stack = [(root, ())]
        while stack:
            node, scope = stack.pop()
            if len(entries) > MAX_NODES:
                return None
            entries.append((node, scope))
            child_scope = scope
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                child_scope = (*scope, node.name)
                if child_scope in scopes:
                    repeated_scopes.add(child_scope)
                else:
                    scopes[child_scope] = node
            stack.extend((child, child_scope) for child in ast.iter_child_nodes(node))
        matches = [
            (node.end_lineno - node.lineno, scope, node)
            for node, scope in entries
            if isinstance(node, ast.stmt)
            and not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.lineno <= line <= node.end_lineno
        ]
        if not matches:
            return None
        matches.sort(key=lambda row: row[0])
        span, scope, statement = matches[0]
        if scope in repeated_scopes or (len(matches) > 1 and matches[1][0] == span):
            return None
        shape = ast.dump(statement, include_attributes=False)
        peers = [
            node
            for node, owner in entries
            if owner == scope and type(node) is type(statement) and ast.dump(node, include_attributes=False) == shape
        ]
        if not peers:
            return None
        value = {"path": path, "scope": scope, "statement": shape}
        if len(peers) > 1:
            ordered = sorted(peers, key=lambda node: (node.lineno, node.col_offset))
            value.update(
                scope_shape=ast.dump(scopes[scope], include_attributes=False), occurrence=ordered.index(statement)
            )
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    except (SyntaxError, ValueError, TypeError, UnicodeError, RecursionError, MemoryError):
        return None
