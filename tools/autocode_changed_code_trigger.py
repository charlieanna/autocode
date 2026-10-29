"""Keep changed-product proof obligations across later test-only Builder batches.

The canonical diff uses the run's original base and approved source selection,
including committed, staged, dirty, untracked and explicitly scoped ignored
files. A missing diff cannot erase product changes already recorded by the
runner. No report, approval, source file or Git ref is changed here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_run_base as run_base
    from . import autocode_source_scope as source_scope
    from . import autocode_verify as verify
except ImportError:
    import autocode_run_base as run_base
    import autocode_source_scope as source_scope
    import autocode_verify as verify


def _product(paths):
    return isinstance(paths, (list, tuple, dict)) and any(
        isinstance(path, str) and verify.is_code_path(path) and not verify.is_test_path(path) for path in paths
    )


def _recorded(state: RunState):
    if _product(state.get("changed_files")):
        return True
    if any(_product(row.get("changed_files")) for row in state.get("stages") or [] if isinstance(row, dict)):
        return True
    proof = state.get("regression_proof") or {}
    if _product(proof.get("source_files")):
        return True
    return _product(
        [
            row.get("path")
            for row in (proof.get("changed_code_inputs") or {}).get("inventory") or []
            if isinstance(row, dict)
        ]
    )


def owes(state: RunState) -> bool:
    """Whether the cumulative candidate changes product code, not just its last batch.

    An authenticated successful comparison retires reverted product changes.
    Without that comparison, known prior product changes remain owed; old
    document/test-only states without product evidence remain compatible.
    """
    known = bool(_recorded(state))
    workspace = state.get("workspace")
    if not isinstance(workspace, str) or not workspace:
        return known
    try:
        root = Path(workspace)
        base = run_base.base_commit(state, root)
        if not base:
            return known
        changes = verify.changed_files(root, base, source_paths=source_scope.paths(state))
        if not isinstance(changes, dict):
            return known
        return bool(_product(changes))
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, subprocess.SubprocessError):
        return known


UNMEASURABLE = (
    "The project has no test command and no test files, so its changed product lines cannot be "
    "measured; the changed-code obligation is recorded as unmeasured instead of blocking completion."
)


def unmeasurable(state: RunState) -> str | None:
    """Why changed product lines cannot be measured in this project, or None when they can.

    AGENTS.md ("AutoCode delivers no untested code"): where coverage cannot be measured,
    AutoCode chooses the fallback deterministically and records it in evidence. A project
    with no configured test command and no test file in the workspace (tracked or not, as
    test detection reads it) gives the proof nothing to run, so requiring one would only
    loop review until a person is asked. Anything uncertain keeps the obligation.
    """
    settings = state.get("settings") or {}
    if ((settings.get("regression") or {}) if isinstance(settings, dict) else {}).get("test_command"):
        return None
    workspace = state.get("workspace")
    if not isinstance(workspace, str) or not workspace:
        return None
    try:
        listed = verify._git(Path(workspace), "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return None
    if any(verify.is_test_path(path) for path in listed.split("\0") if path):
        return None
    return UNMEASURABLE
