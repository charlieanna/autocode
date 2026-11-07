"""Where a task started outside any Git checkout keeps its project.

A folder that is not in Git and already holds files gets a private scratch project
in the folder's own AutoCode store, ``FOLDER/.autocode/projects/ID``. A chat that
never builds anything (a greeting, a question) leaves nothing among the person's
files. A build or bug fix that completes there is delivered to a named project
beside those files (``autocode_project_delivery``). The scratch project never moves:
the run's saved state names its path throughout.

Projects bootstrapped before this, ``FOLDER/autocode-projects/NAME``, are still
found and their runs continue where they are.

Imports nothing from AutoCode.
"""

from __future__ import annotations

from pathlib import Path

SCRATCH = (".autocode", "projects")
LEGACY = ("autocode-projects",)


def root(selected) -> Path:
    """The folder that holds the scratch projects of ``selected``."""
    return Path(selected).joinpath(*SCRATCH)


def roots(selected) -> list[Path]:
    """Every folder a bootstrapped project of ``selected`` may live in, the current one first."""
    return [root(selected), Path(selected).joinpath(*LEGACY)]


def holds(selected, project) -> bool:
    """Whether ``project`` is a project AutoCode bootstrapped for ``selected``, in either place."""
    return Path(project).parent in roots(selected)


def is_scratch(selected, project) -> bool:
    """Whether ``project`` is a private scratch project of ``selected`` (not a legacy visible one)."""
    return Path(project).parent == root(selected)
