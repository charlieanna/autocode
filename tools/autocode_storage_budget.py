"""Projected run storage versus free space: fail before spend (issue #971).

A serious run multiplies the workspace: parallel worktrees, a fresh verification
copy per validation, and per-attempt source snapshots were measured at about
three times the workspace size on a large repository (issue #971). This
projection refuses only the provably doomed floor — not even one source copy
fits — and warns below the measured projection, so the operator sees the numbers
before hours of model spend instead of an opaque ENOSPC git error mid-run.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

SNAPSHOT_MULTIPLIER = 3
_INTERNAL = {".git", ".autocode", ".autocode-ui", ".scenario-runs"}
_SKIP_DIRS = {"__pycache__"}


def conversation_only(state: RunState):
    """Requirements chat does not prepare the workspace copies being budgeted."""
    return bool((state.get("settings") or {}).get("spec_first") and state.get("next_stage") == "requirements_gather")


def _other_worktrees(root):
    """Other checkouts are not source inputs, even when stored below this one."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "worktree", "list", "--porcelain", "-z"],
            capture_output=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    return {
        path
        for field in result.stdout.split(b"\0")
        if field.startswith(b"worktree ")
        and (path := Path(os.fsdecode(field[len(b"worktree ") :])).resolve()) != root
        and path.is_relative_to(root)
    }


def human(size):
    """Round sizes the way an operator reads them."""
    for limit, unit in ((1024**3, "GiB"), (1024**2, "MiB"), (1024, "KiB")):
        if abs(size) >= limit:
            return f"{size / limit:.1f} {unit}"
    return f"{size} B"


def workspace_bytes(workspace):
    """Source bytes a run's copies multiply.

    Git objects and the run's own storage are excluded: worktrees share the
    object store, and a nested .autocode run dir is the consumer being measured,
    not its input. Dependency trees a verification copy only links are counted
    anyway, so the projection stays conservative. Prune excluded trees before
    descending: run history can contain many complete workspace copies.
    """
    root = Path(workspace).resolve()
    other_worktrees = _other_worktrees(root)
    total = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.name in _SKIP_DIRS or entry.name in _INTERNAL:
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            path = Path(entry.path)
                            if path not in other_worktrees:
                                pending.append(path)
                        elif entry.is_file(follow_symlinks=False) and Path(entry.name).suffix != ".pyc":
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def projection(workspace, *, multiplier=SNAPSHOT_MULTIPLIER, disk_usage=shutil.disk_usage):
    """What one run is expected to need on the workspace's filesystem, versus what is free."""
    root = Path(workspace)
    size = workspace_bytes(root)
    usage = disk_usage(root)
    return {
        "workspace_bytes": size,
        "multiplier": multiplier,
        "projected_bytes": size * multiplier,
        "free_bytes": usage.free,
        "total_bytes": usage.total,
    }


def refusal(projection):
    """The doomed floor: not one source copy fits. Nonempty means do not start."""
    if projection["free_bytes"] < projection["workspace_bytes"]:
        return (
            f"preparing even one source copy of the workspace ({human(projection['workspace_bytes'])}) exceeds the "
            f"free space ({human(projection['free_bytes'])}); free disk space or move the workspace to a larger disk"
        )
    return ""


def dispatch_refusal(workspace, run_dir):
    """Recheck every storage destination before spend; free space is never reusable evidence.

    Workspace copies, run artifacts and tools' temporary files can be on different
    filesystems. Use the existing one-copy floor, without treating it as proof that
    a complete build will fit. Check the nearest existing ancestor of a fresh run.
    """
    size = workspace_bytes(workspace)
    for label, destination in (
        ("workspace", workspace),
        ("run artifacts", run_dir),
        ("temporary files", tempfile.gettempdir()),
    ):
        root = Path(destination).resolve()
        while not root.exists() and root != root.parent:
            root = root.parent
        free = shutil.disk_usage(root).free
        if free < max(1, size):
            return (
                f"Insufficient storage for {label} at {root}: {human(free)} free; "
                f"even one source copy needs {human(size)}. Free space or move the storage before resuming."
            )
    return ""


def warning(projection):
    """Below the measured projection; the run may start, but with the numbers first."""
    if projection["free_bytes"] < projection["projected_bytes"]:
        return (
            f"low disk space for a run: the workspace ({human(projection['workspace_bytes'])}) times its measured "
            f"{projection['multiplier']}x copy growth (worktrees, verification copies, attempt snapshots) projects "
            f"{human(projection['projected_bytes'])}, but only {human(projection['free_bytes'])} is free; "
            f"prune or move before a long run"
        )
    return ""
