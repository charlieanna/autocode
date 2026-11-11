"""Installed dependency directories: third-party code a package manager writes, never the task's product code.

A project that does not ignore its node_modules shows one `npm install` as thousands of untracked changed
files. Counted as changed product lines, one install made 274,546 obligations, and the Tester's handoff
(68 MB) and report schema (32 MB) were too large to launch (#3164).
"""

from __future__ import annotations

from pathlib import PurePosixPath

NAMES = ("node_modules", ".venv", "venv")
REASON = "Installed third-party code, not product code the task changed: no test or reason is required."


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
