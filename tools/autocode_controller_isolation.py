"""Admission for tasks that can edit the AutoCode controller's own repository.

This policy reads Git metadata but never changes a checkout or run. Interfaces
supply the executing module path; no controller or workflow imports belong here.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path


def _git(workspace: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(workspace), *args],
            env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.removesuffix("\n") if result.returncode == 0 else None


def _linked_worktree(workspace: Path) -> bool:
    top = _git(workspace, "rev-parse", "--show-toplevel")
    git_dir = _git(workspace, "rev-parse", "--absolute-git-dir")
    common = _git(workspace, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not top or not git_dir or not common:
        return False
    if Path(top).resolve() != workspace or Path(git_dir).resolve() == Path(common).resolve():
        return False
    listing = _git(workspace, "worktree", "list", "--porcelain", "-z")
    return listing is not None and any(
        Path(line.removeprefix("worktree ")).resolve() == workspace
        for line in listing.split("\0")
        if line.startswith("worktree ")
    )


def require_isolated(workspace: Path, controller_file: Path) -> None:
    """Refuse an AutoCode self-build unless its controller and edits are separated."""
    workspace = workspace.resolve()
    if not (workspace / "tools" / "autocode.py").is_file() or not (workspace / "AGENTS.md").is_file():
        return
    controller = controller_file.resolve()
    if not controller.is_relative_to(workspace) and _linked_worktree(workspace):
        return
    target = workspace.with_name(workspace.name + "-self-build")
    command = shlex.join(["git", "-C", str(workspace), "worktree", "add", "--detach", str(target), "HEAD"])
    reason = (
        "the executing controller is inside the task workspace"
        if controller.is_relative_to(workspace)
        else "the task workspace is not a linked Git worktree"
    )
    raise ValueError(
        f"Unsafe AutoCode self-build: {reason}. Self-builds require a linked Git worktree "
        f"and a controller outside it. Create one with: {command}. "
        f"Then run this separate controller with --workspace {shlex.quote(str(target))} --in-place. "
        "A new CLI task without --in-place creates its own isolated worktree."
    )
