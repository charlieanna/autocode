"""Admission for tasks that can edit the AutoCode controller's own repository.

This policy reads only the file system and never changes a checkout or run. Interfaces
supply the executing module path; no controller or workflow imports belong here.
"""

from __future__ import annotations

import shlex
from pathlib import Path


def is_autocode_checkout(workspace: Path) -> bool:
    """Whether ``workspace`` is an AutoCode checkout this controller could be built from.

    A project that vendors an old copy of tools/autocode.py beside its own AGENTS.md (idlecampus
    kept one from 2026-10-12) is not AutoCode: it lacks this admission module, which every
    checkout that can refuse a self-build has.
    """
    tools = workspace / "tools"
    return all(
        (path).is_file() for path in (tools / "autocode.py", tools / Path(__file__).name, workspace / "AGENTS.md")
    )


def require_isolated(workspace: Path, controller_file: Path) -> None:
    """Refuse an AutoCode self-build whose edits could change the controller running it.

    An AutoCode checkout is an ordinary project to a controller installed outside it:
    the task can edit the checkout in place, as any other project, and the running
    controller stays as it was (owner decision 2026-10-20).
    """
    workspace = workspace.resolve()
    if not is_autocode_checkout(workspace) or not controller_file.resolve().is_relative_to(workspace):
        return
    target = workspace.with_name(workspace.name + "-self-build")
    command = shlex.join(["git", "-C", str(workspace), "worktree", "add", "--detach", str(target), "HEAD"])
    raise ValueError(
        "Unsafe AutoCode self-build: the executing controller is inside the task workspace, "
        "so the task could edit the code that runs it. Run an installed autocode from outside "
        f"this checkout, or create a separate workspace with: {command}. "
        f"Then run this controller with --workspace {shlex.quote(str(target))} --in-place."
    )
