"""Resolve advisory memory through the selected project's existing task-worktree interface."""

from pathlib import Path

try:
    from . import autocode_project_memory as memory
    from . import autocode_workspaces as workspaces
except ImportError:
    import autocode_project_memory as memory
    import autocode_workspaces as workspaces


def selected(workspace):
    root = Path(workspace).resolve()
    try:
        metadata = workspaces.metadata(root)
        return Path(metadata["project_workspace"]).resolve() if metadata else root
    except (KeyError, TypeError) as error:
        raise ValueError("The task-worktree project mapping is malformed.") from error


def load(workspace):
    try:
        project = selected(workspace)
    except (OSError, ValueError):
        return {
            "status": "invalid",
            "record": None,
            "sha256": None,
            "source": None,
            "note": "Project memory could not identify the selected project; it was ignored.",
        }
    return {**memory.load(project), "source": str(project / ".autocode" / "project.json")}
