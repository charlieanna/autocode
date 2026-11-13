"""Admission for tasks that can edit the AutoCode controller's own repository.

This policy reads only the file system and never changes a checkout or run. Interfaces
supply the executing module path; no controller or workflow imports belong here.
"""

from __future__ import annotations

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


def setup_view(workspace: Path, controller_file: Path) -> dict:
    """Read-only admission and the public client action for selecting an isolated controller."""
    workspace = workspace.resolve()
    unsafe = is_autocode_checkout(workspace) and controller_file.resolve().is_relative_to(workspace)
    detail = (
        "Unsafe AutoCode self-build: the executing controller is inside the task workspace, "
        "so the task could edit the code that runs it. A new worktree does not isolate the controller "
        "during startup. Select an AutoCode CLI installed outside this checkout, then retry your chat. "
        "Choose an existing non-editable installation, or one sourced from a separate checkout; "
        "nothing will be installed automatically."
        if unsafe
        else "This controller is outside the task workspace or the workspace is not an AutoCode self-build."
    )
    return {
        "ready": not unsafe,
        "detail": detail,
        "actions": [
            {
                "id": "select_cli",
                "label": "Select isolated AutoCode CLI",
                "next_command": ["worktrees", "list", "--workspace", str(workspace), "--json"],
            }
        ]
        if unsafe
        else [],
    }


def require_isolated(workspace: Path, controller_file: Path) -> None:
    """Refuse an AutoCode self-build whose edits could change the controller running it.

    An AutoCode checkout is an ordinary project to a controller installed outside it:
    the task can edit the checkout in place, as any other project, and the running
    controller stays as it was (owner decision 2026-10-20).
    """
    setup = setup_view(workspace, controller_file)
    if not setup["ready"]:
        raise ValueError(setup["detail"])
