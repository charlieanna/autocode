"""Which saved run an addressed ``.autocode/runs/<id>/`` path names, checked as the command uses it.

Commands that act on a saved run address it by a directory path. Issues #3892 and #3890 share one
cause: the path was used without checking, at that moment, that it is the run's canonical, owned
record — a mistyped run id under ``.autocode/runs/`` created a run-shaped directory no run owns
(#3892), and a follower that resolved the directory once kept polling a record whose run had
already moved to its task worktree (#3890). This module is the one mechanism both consume:

- ``addressed_run`` reads the run id an evidence path is aimed at from the path itself, before
  symlink resolution, so a ``runs/<alias>`` symlink to a real run is that alias, never the run;
- ``checkpoint`` says whether a run directory holds a canonical checkpoint: a ``state.json`` of its
  own, not one reached through a symlink;
- ``expected_owner`` names the run an evidence output was expected to belong to: the runner's
  capture context, else the single active run — counted only through authenticated records: each
  task-worktree candidate verified by ``workspaces.metadata``'s Git ownership check, a record
  claiming a move only through the mutual moved-record check ``build_worktree`` performs, and a
  run that started in a worktree still counting;
- ``follow`` reads the canonical record a directory addresses now, following an authenticated move
  to a task worktree, again through ``build_worktree``'s one check.

Lower layer: files, Git worktrees and moved-run records; never the interface layer.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_build_worktree as build_worktree
    from . import autocode_workspaces as workspaces
except ImportError:
    import autocode_build_worktree as build_worktree
    import autocode_workspaces as workspaces

# A controller is doing work in these statuses; they need no person yet. `autocode runs` groups its
# table by them for the interface; this module states the names locally rather than import upward.
WORKING_STATUSES = ("RUNNING", "DISCOVERING", "WAITING_FOR_USER", "RESOLVER_PENDING")


def addressed_run(path, roots) -> tuple[Path, str] | None:
    """(run directory, run id) an evidence path is aimed inside, read from the path before symlink
    resolution.

    Only a path strictly inside ``<root>/runs/<id>/`` addresses a run: one directly under ``runs/``
    creates no run-shaped directory and keeps today's evidence-root validation. The id is the
    path's own component, so an alias symlink to another run's directory is that alias, never the
    run it points at."""
    absolute = Path(os.path.abspath(path))
    for base in roots:
        root = Path(base).resolve()
        if not absolute.is_relative_to(root):
            continue
        inside = absolute.relative_to(root).parts
        if len(inside) >= 3 and inside[0] == "runs":
            return root / "runs" / inside[1], inside[1]
    return None


def checkpoint(run_dir) -> Path | None:
    """The run's canonical checkpoint (``state.json``) in ``run_dir``, or None when the directory is
    not an existing run of its own: missing, not a directory, or its record reached through a
    symlink."""
    directory = Path(run_dir)
    if directory.is_symlink() or not directory.is_dir():
        return None
    state = directory / "state.json"
    if state.is_symlink() or not state.is_file():
        return None
    return state


def _record(run_dir) -> RunState | None:
    state = checkpoint(run_dir)
    if state is None:
        return None
    try:
        record = json.loads(state.read_text())
    except (OSError, ValueError):
        return None
    return cast("RunState", record) if isinstance(record, dict) else None


def _authenticated_worktree(tree: Path) -> bool:
    """Whether ``tree`` is one of this project's task worktrees: a metadata record of the right
    shape whose Git common directory is still the project's (``workspaces.metadata``)."""
    if tree.is_symlink() or not tree.is_dir():
        return False
    try:
        return bool(workspaces.metadata(tree))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def active_runs(root) -> dict[str, list[Path]]:
    """The ids of the runs working now, as one evidence root sees them, each with its run directory.

    A run counts when its checkpoint reads with a working status: in the root's own ``runs/``, or
    saved in a task worktree of the root's project. Only authenticated records count: the worktree
    passes ``workspaces.metadata``'s Git ownership check, a record claiming it moved into the tree
    counts only while the record it names as its origin names this run back (the one mutual
    moved-record check ``build_worktree`` performs), and a run that started in a worktree counts as
    it is. A record a move left behind never counts: it is not working, and it is not here."""
    working: dict[str, list[Path]] = {}
    runs = Path(root) / "runs"
    if runs.is_dir():
        for run_dir in sorted(runs.iterdir()):
            record = _record(run_dir)
            if record is None or build_worktree.moved(record) is not None:
                continue
            if record.get("status") in WORKING_STATUSES:
                working.setdefault(run_dir.name, []).append(run_dir)
    trees = Path(root) / "worktrees"
    if trees.is_dir():
        for tree in sorted(trees.iterdir()):
            if not _authenticated_worktree(tree):
                continue
            saved = tree / ".autocode" / "runs"
            if not saved.is_dir():
                continue
            for run_dir in sorted(saved.iterdir()):
                record = _record(run_dir)
                if record is None or build_worktree.moved(record) is not None:
                    continue
                if record.get("status") not in WORKING_STATUSES:
                    continue
                claim = record.get(build_worktree.KEY) or {}
                if (
                    isinstance(claim, dict)
                    and claim.get("state") == build_worktree.BUILDING
                    and build_worktree.origin(record, run_dir) is None
                ):
                    continue  # a claimed move the two records do not name mutually is not this project's run
                if Path(str(record.get("workspace") or "")).resolve() != tree.resolve():
                    continue
                working.setdefault(run_dir.name, []).append(run_dir)
    return working


def expected_owner(roots, capture_context=None) -> str | None:
    """The run id an evidence output was expected to belong to, when exactly one answer exists.

    The runner's capture context names it: its attempt path lies under the owning run's directory.
    Without it, the single active run (``active_runs``) is the answer; with none, or several, no
    name is guessed."""
    if isinstance(capture_context, dict):
        attempt = capture_context.get("attempt")
        if isinstance(attempt, str) and attempt:
            addressed = addressed_run(attempt, roots)
            if addressed is not None:
                return addressed[1]
    working: set[str] = set()
    for base in roots:
        working.update(active_runs(Path(base)))
    if len(working) == 1:
        return next(iter(working))
    return None


def output_refusal(output, roots, capture_context=None) -> str | None:
    """Why an evidence output may not be written, or None when its path names a writable location.

    The path is refused when it is aimed inside ``<root>/runs/<id>/`` whose directory is not a
    run's own canonical record: missing — capture would create a run-shaped directory no run owns —
    or a directory without a checkpoint, including a symlink to another run. A
    directory with its own canonical checkpoint keeps today's behaviour: whether the run owns
    the receipt is the acceptance rule's to check (autocode_rework_policy), not capture's. The
    refusal names the addressed run directory and, when exactly one is known, the run the output
    was expected to belong to."""
    addressed = addressed_run(output, roots)
    if addressed is None:
        return None
    run_dir, _ = addressed
    if _record(run_dir) is not None:
        return None
    owner = expected_owner(roots, capture_context)
    expected = (
        f"expected owner run: {owner}" if owner else "no single active run is saved to name as the expected owner"
    )
    return (
        f"evidence output is aimed under {run_dir}, which is not an existing run (no checkpoint of its own); {expected}"
    )


def follow_record(run_dir, workspace, state: object) -> tuple[Path, Path]:
    """Follow a move using the checkpoint already observed by the caller."""
    run_dir, workspace = Path(run_dir), Path(workspace)
    found = build_worktree.resolve(workspace, run_dir, cast("RunState", state)) if isinstance(state, dict) else None
    return (found[1], found[0]) if found else (run_dir, workspace)


def follow(run_dir, workspace) -> tuple[Path, Path]:
    """(run directory, workspace) the saved record at ``run_dir`` addresses now.

    A record an authenticated move to a task worktree left behind is read where the run is, as its
    status is: ``build_worktree.resolve`` is the one check that the two records name each other.
    Anything else — no record here, an unreadable one, a claim that does not hold — keeps the
    directory as it is."""
    run_dir, workspace = Path(run_dir), Path(workspace)
    try:
        state = json.loads((run_dir / "state.json").read_text())
    except (OSError, ValueError):
        return run_dir, workspace
    return follow_record(run_dir, workspace, state)
