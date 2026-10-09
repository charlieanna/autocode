"""Merge a delivered task worktree's branch into the branch the run started from (#724).

A run in its own worktree leaves the person's checkout untouched until they
merge. ``autocode merge`` fast-forwards or merges ``autocode/<task>`` into the
base branch after the completion gate. If the base moved and the proof is
stale, it refuses and names the re-validate step, as resume does.

Lower layer only: Git and ``autocode_worktrees``; never ``autocode``.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

try:
    from . import autocode_workspaces as workspaces
    from . import autocode_worktrees as worktrees
except ImportError:
    import autocode_workspaces as workspaces
    import autocode_worktrees as worktrees


def _git(root, *args, check=True):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if check and result.returncode:
        raise ValueError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def merge(project: Path, tree: Path, *, into: str | None = None) -> tuple[bool, str]:
    """Merge the delivered task branch into ``into`` (default: the base's branch).

    Returns (ok, message). Refuses when the base moved since the run started and
    the worktree's proof is not current: re-validate first, as resume does.
    """
    project, tree = Path(project).resolve(), Path(tree).resolve()
    meta = workspaces.metadata(tree)
    if not meta or meta.get("kind") not in (None, "task"):
        return False, "not a task worktree; nothing to merge"
    branch = meta.get("branch") or worktrees._task_branch(tree, legacy=True)
    if not branch:
        return False, "no delivered branch; the run must reach TASK_COMPLETE first"
    base_commit = meta.get("base_commit")
    target = into or meta.get("base_branch") or _git(project, "symbolic-ref", "--short", "HEAD", check=False)
    if not target:
        return False, "cannot tell which branch to merge into; pass --into NAME"
    try:
        delivered = _git(project, "rev-parse", "--verify", f"refs/heads/{branch}")
        target_tip = _git(project, "rev-parse", "--verify", f"refs/heads/{target}")
    except ValueError as error:
        return False, str(error)
    if delivered == base_commit:
        return False, "nothing delivered on this branch yet; the run must reach TASK_COMPLETE and commit its source first"
    if base_commit and target_tip != base_commit and _git(project, "merge-base", "--is-ancestor",
                                                         base_commit, target_tip, check=False) == "":
        # The base moved past the run's start: the completion proof may be stale.
        return False, (
            f"the base branch {target} moved after this run started "
            f"({base_commit[:12]} -> {target_tip[:12]}); re-validate on the current source "
            f"before merging (as --resume-paused does), then merge again")
    if _git(project, "merge-base", "--is-ancestor", target_tip, delivered, check=False) == "":
        # Fast-forward: the delivered branch contains the base tip.
        try:
            _git(project, "checkout", target)
            _git(project, "merge", "--ff-only", branch)
        except ValueError as error:
            return False, f"fast-forward failed: {error}"
        return True, f"fast-forwarded {target} to {delivered[:12]} from {branch}"
    try:
        _git(project, "checkout", target)
        _git(project, "merge", "--no-ff", "-m", f"Merge {branch}", branch)
    except ValueError as error:
        return False, f"merge failed: {error}"
    return True, f"merged {branch} into {target}"


def cli(argv) -> int:
    parser = argparse.ArgumentParser(
        prog="autocode merge",
        description="Merge a delivered task worktree's branch into the branch the run started from.")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(),
                        help="The project (or one of its task worktrees)")
    parser.add_argument("--into", default=None,
                        help="Branch to merge into (default: the base branch the run started from)")
    parser.add_argument("worktree", nargs="?", default=None,
                        help="The task worktree to merge (default: the only one under .autocode/worktrees)")
    args = parser.parse_args(argv)
    try:
        project = Path(_git(args.workspace, "rev-parse", "--show-toplevel")).resolve()
        data = workspaces.metadata(project)
        if data:
            project = Path(data["project_workspace"]).resolve()
        if args.worktree:
            tree = Path(args.worktree).resolve()
        else:
            parent = project / ".autocode" / "worktrees"
            trees = sorted(p for p in worktrees._registered(project)
                           if p.parent == parent and worktrees._task_branch(p, legacy=True))
            if len(trees) != 1:
                parser.error("name the worktree to merge; there is not exactly one task worktree")
            tree = trees[0]
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    ok, message = merge(project, tree, into=args.into)
    print(message)
    return 0 if ok else 1
