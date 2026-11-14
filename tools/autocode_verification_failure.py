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
    from . import autocode_util as util
except ImportError:
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
    resolver = state.get("resolver") or {}
    receipts = resolver.get("operational_receipts") or {}
    for identity in reversed(resolver.get("operational_diagnostics") or []):
        record = receipts.get(identity)
        if not isinstance(record, dict):
            continue
        if (
            record.get("runner_owned") is True
            and record.get("engine") == "runner"
            and (record.get("receipt") or {}).get("scope") == "verification_preparation"
        ):
            return record
    return None


def blocked(state: RunState):
    """A retained runner hold is denial only; clearing markers grants no retry."""
    record = _record(state)
    return record if record and (record.get("decision") or {}).get("action") == "blocked" else None


def presentation(state: RunState):
    """Read-only description of this typed failure; no action or authority."""
    if blocked(state):
        return {
            "category": "blocked",
            "title": "AutoCode is blocked on verification preparation",
            "what_happened": "Verification preparation failed before a provider started. Source binding, cleanup or ownership remains unconfirmed; retained workers and work are kept. AutoCode will not start another stage or ask you to decide its recovery.",
        }
    if state.get("status") != "PAUSED_INTERVENTION":
        return None
    for receipt in state.get("applied_interventions") or []:
        if not isinstance(receipt, dict):
            continue
        ended = receipt.get("outcome") or {}
        if (
            receipt.get("kind") == "stop"
            and receipt.get("actor") == "autocode"
            and ended.get("result") == "could_not_finish"
            and (ended.get("evidence") or {}).get("operation") == "verification_preparation"
        ):
            return {
                "category": "stopped",
                "title": "AutoCode could not finish this task",
                "what_happened": ended.get("reason"),
            }
    return None
