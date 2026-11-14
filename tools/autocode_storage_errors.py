"""Explain storage and opted-in subprocess failures without changing authority."""

from __future__ import annotations

import errno
import re
import subprocess

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util

# Git's captured diagnostic must end in the OS error, rather than merely name a
# file containing that phrase. Arbitrary exception or model prose is not enough.
_GIT_NO_SPACE = re.compile(r"^(?:fatal|error): (?:.*: )?No space left on device[.!]?\s*$", re.MULTILINE)
_CAPTURED_STDERR_LIMIT = 2000


def _text(value):
    return value.decode(errors="replace") if isinstance(value, bytes) else value or ""


def describe(error, *, operation, location, git_stderr=False, include_captured_stderr=False):
    """Keep other errors exact; add recovery guidance for ENOSPC or captured Git stderr.

    ``location`` identifies the operation's storage. A concrete OS filename takes
    precedence. Git can use a common object store on another filesystem, so its
    workspace path is a starting point rather than a claim about the failing disk.
    Verification preparation may opt into bounded, redacted actual subprocess
    stderr. Other callers keep their original non-storage error descriptions.
    """
    original = str(error)
    current, seen = error, set()
    filename = None
    diagnostic = _text(error) if git_stderr else ""
    captured = ""
    disk_full = bool(git_stderr and _GIT_NO_SPACE.search(diagnostic))
    while isinstance(current, BaseException) and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, OSError) and current.errno == errno.ENOSPC:
            disk_full = True
            filename = current.filename2 or current.filename
        if isinstance(current, subprocess.CalledProcessError):
            stderr = _text(current.stderr)
            if include_captured_stderr and not captured and isinstance(stderr, str):
                captured = stderr.strip()
            if _GIT_NO_SPACE.search(stderr):
                disk_full, diagnostic = True, stderr.strip()
                if include_captured_stderr:
                    captured = diagnostic
        current = current.__cause__ or current.__context__
    if include_captured_stderr:
        original = util.redact(original)
        # Redact before cutting: truncating a credential first can hide its
        # identifying shape and leave the remaining secret visible.
        detail = util.redact(captured)
        if len(detail) > _CAPTURED_STDERR_LIMIT:
            detail = detail[:_CAPTURED_STDERR_LIMIT] + "\n[stderr truncated]"
        if detail and detail not in original:
            original += ": " + detail
    if not disk_full:
        return original
    if filename:
        storage = f"the filesystem containing {filename}"
    elif git_stderr:
        storage = f"workspace {location} and its Git storage (which may be on another filesystem)"
    else:
        storage = f"the filesystem containing {location}"
    if diagnostic and not include_captured_stderr and diagnostic not in original:
        original += ": " + diagnostic
    if include_captured_stderr:
        return f"Disk full while {operation}. Storage: {storage}. Original error: {original}"
    return f"Disk full while {operation}. Free space for {storage}, then resume this run. Original error: {original}"
