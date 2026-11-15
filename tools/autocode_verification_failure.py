"""Typed observations of a collected Git failure before a provider starts.

These exceptions carry facts from the allocator, not model reports or exception
text matching. They grant no retry, cleanup or source-acceptance authority.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_operational_block_view as operational_block
    from . import autocode_util as util
except ImportError:
    import autocode_operational_block_view as operational_block
    import autocode_util as util


class GitPreparationFailed(subprocess.CalledProcessError):
    """The synchronous preparation child exited; retain its actual diagnostic."""

    def __init__(
        self, error, *, workspace, run_dir, control, source_revision, source_paths, cleanup_confirmed, cleanup_error
    ):
        super().__init__(error.returncode, error.cmd, output=error.output, stderr=error.stderr)
        self.workspace = str(workspace)
        self.run_dir = str(run_dir)
        self.control = str(control)
        self.source_revision = source_revision
        self.source_paths = list(source_paths)
        self.cleanup_confirmed = cleanup_confirmed
        self.cleanup_error = cleanup_error


class PrelaunchFailure(util.Paused):
    """A preparation failure whose provider was never launched."""

    def __init__(self, message: str, failure: GitPreparationFailed):
        super().__init__("PAUSED_VERIFICATION_PREPARATION", message)
        self.failure = failure


def _record(state: RunState):
    # Compatibility entry point: all runner-owned denials share one reader.
    return operational_block.receipt(state, "verification_preparation")


def blocked(state: RunState):
    """A retained runner hold is denial only; clearing markers grants no retry."""
    record = _record(state)
    return record if record and (record.get("decision") or {}).get("action") == "blocked" else None


def presentation(state: RunState):
    """Read-only description of this typed failure; no action or authority."""
    if blocked(state) or state.get("status") == "PAUSED_INTERVENTION":
        return operational_block.presentation(state)
    return None
