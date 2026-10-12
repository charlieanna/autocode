"""Projected run storage versus free space: fail before spend (issue #971).

A serious run multiplies the workspace: parallel worktrees, a fresh verification
copy per validation, and per-attempt source snapshots were measured at about
three times the workspace size on a large repository (issue #971). This
projection refuses only the provably doomed floor — not even one source copy
fits — and warns below the measured projection, so the operator sees the numbers
before hours of model spend instead of an opaque ENOSPC git error mid-run.
"""

import shutil
from pathlib import Path

SNAPSHOT_MULTIPLIER = 3
_INTERNAL = {".git", ".autocode", ".autocode-ui"}
_SKIP_DIRS = {"__pycache__"}


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
    anyway, so the projection stays conservative.
    """
    root = Path(workspace)
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_symlink() or not path.is_file():
                continue
            parts = path.relative_to(root).parts
            if parts[0] in _INTERNAL or any(part in _SKIP_DIRS for part in parts) or path.suffix == ".pyc":
                continue
            total += path.stat().st_size
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
