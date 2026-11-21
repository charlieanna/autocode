"""Bound potentially slow status observations; never grant execution authority.

Operations are allowlisted in the worker. The action that uses an observation
must independently revalidate under its normal lock before it can execute.
"""

from __future__ import annotations

import contextvars
import functools
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_acceptance_retry_admission as admission
    from . import autocode_acceptance_retry_next as retry_next
    from . import autocode_acceptance_tests as acceptance
    from . import autocode_captured_process as captured
except ImportError:
    import autocode_acceptance_retry_admission as admission
    import autocode_acceptance_retry_next as retry_next
    import autocode_acceptance_tests as acceptance
    import autocode_captured_process as captured

# The extension allows 30 seconds; leave room for the capture helper's eight-second
# cleanup allowance and the rest of the status projection.
TIMEOUT = 15.0
_deadline = contextvars.ContextVar("autocode_status_observation_deadline", default=None)
_poisoned = contextvars.ContextVar("autocode_status_observation_uncertain_cleanup", default=False)


def budgeted(function):
    """Share one allowance through nested status and recovery projections."""

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        if _deadline.get() is not None:
            return function(*args, **kwargs)
        token = _deadline.set(time.monotonic() + TIMEOUT)
        cleanup_token = _poisoned.set(False)
        try:
            return function(*args, **kwargs)
        finally:
            _poisoned.reset(cleanup_token)
            _deadline.reset(token)

    return wrapper


def _observe(operation, payload, *, timeout=None):
    """Collect a worker result only after the owned process family is gone."""
    deadline = _deadline.get()
    if _poisoned.get():
        raise captured.ProcessError("An earlier status observation has uncertain process cleanup")
    remaining = TIMEOUT if deadline is None else deadline - time.monotonic()
    if timeout is not None:
        remaining = min(remaining, timeout)
    if remaining <= 0:
        raise subprocess.TimeoutExpired("status-worker", TIMEOUT)
    try:
        result = captured.run(
            [
                sys.executable,
                str(Path(__file__).with_name("autocode_status_observation_worker.py").resolve()),
                "--worker",
            ],
            input=json.dumps({"operation": operation, "payload": payload}),
            timeout=remaining,
            check=True,
        )
    except captured.ProcessError:
        if deadline is not None:
            _poisoned.set(True)
        raise
    value = json.loads(result.stdout)
    if not isinstance(value, dict) or set(value) != {"result"}:
        raise ValueError("Invalid status observation")
    return value["result"]


def retained_retry(state: RunState, workspace):
    """Project a retained retry or conservatively hold if observation is unavailable."""
    saved = state.get(acceptance.KEY)
    if saved is None:
        saved = {}
    if not isinstance(saved, dict):
        return {
            "next": retry_next.BLOCKED,
            "rechecks": 0,
            "allowed": None,
            "reason": "The saved acceptance verification is malformed",
        }
    pending = saved.get(retry_next.KEY)
    if pending is None or admission.preflight(state, pending):
        return retry_next.decide(state, workspace)
    attempts = pending.get("attempts")
    unavailable = {
        "next": retry_next.BLOCKED,
        "rechecks": attempts if type(attempts) is int and attempts >= 0 else 0,
        "allowed": None,
        "reason": "The saved acceptance verification could not be checked in time",
    }
    try:
        answer = _observe("retained_retry", {"state": state, "workspace": str(workspace)})
        if not isinstance(answer, dict) or answer.get("next") not in (
            retry_next.RECHECK,
            retry_next.TESTER,
            retry_next.EXHAUSTED,
            retry_next.BLOCKED,
        ):
            return unavailable
        return answer
    except (OSError, TypeError, ValueError, subprocess.SubprocessError, captured.ProcessError):
        return unavailable


def report_repair(state: RunState, workspace, revision=None):
    """Bound both snapshot and pinned report reads; None means unavailable."""
    try:
        value = _observe("report_repair", {"state": state, "workspace": str(workspace), "revision": revision})
        if isinstance(value, dict) and isinstance(value.get("revision"), str) and type(value.get("stale")) is bool:
            return value
    except (OSError, TypeError, ValueError, subprocess.SubprocessError, captured.ProcessError):
        pass
    return None


def source_snapshot(state: RunState, workspace):
    """Return a bounded snapshot or None when freshness is unknown."""
    try:
        value = _observe("source_snapshot", {"state": state, "workspace": str(workspace)})
        if isinstance(value, dict) and isinstance(value.get("revision"), str):
            return value
    except (OSError, TypeError, ValueError, subprocess.SubprocessError, captured.ProcessError):
        pass
    return None


def suite_reports(state: RunState, workspace, run_dir):
    """Observe historical reports within the shared status deadline, never as proof."""
    try:
        rows = _observe("suite_reports", {"state": state, "workspace": str(workspace), "run_dir": str(run_dir)})
        if not isinstance(rows, list) or len(rows) > 8:
            return []
        for row in rows:
            if (
                not isinstance(row, dict)
                or row.get("kind") not in ("collection", "execution")
                or not isinstance(row.get("command"), str)
                or len(row["command"]) > 512
                or type(row.get("reported_count")) is not int
                or row["reported_count"] <= 0
                or not isinstance(row.get("paths"), list)
                or len(row["paths"]) > 40
                or any(not isinstance(path, str) or len(path) > 256 for path in row["paths"])
                or type(row.get("omitted_paths")) is not int
                or row["omitted_paths"] < 0
                or row.get("independently_verified") is not False
            ):
                return []
        return rows
    except (OSError, TypeError, ValueError, subprocess.SubprocessError, captured.ProcessError):
        return []


def evidence_inspection(state: RunState, workspace, run_dir, *, accepted_human_ids=(), initial_snapshot=None):
    """Bound source walks, evidence hashing and the checkpoint reread together."""
    try:
        value = _observe(
            "evidence_inspection",
            {
                "state": state,
                "workspace": str(workspace),
                "run_dir": str(run_dir),
                "accepted_human_ids": list(accepted_human_ids),
                "initial_snapshot": initial_snapshot,
            },
        )
        if isinstance(value, dict) and isinstance(value.get("freshness"), str):
            return value
    except (OSError, TypeError, ValueError, subprocess.SubprocessError, captured.ProcessError):
        pass
    return None


def launch_guard(state: RunState, workspace, run_dir):
    """Bound the launch-input file checks; None means the check was unavailable."""
    try:
        value = _observe("launch_guard", {"state": state, "workspace": str(workspace), "run_dir": str(run_dir)})
        if isinstance(value, dict) and type(value.get("ok")) is bool:
            return value
    except (OSError, TypeError, ValueError, subprocess.SubprocessError, captured.ProcessError):
        pass
    return None


def completion(state: RunState, current, workspace, run_dir):
    """Bound current completion and visual evidence checks together."""
    try:
        value = _observe(
            "completion", {"state": state, "current": current, "workspace": str(workspace), "run_dir": str(run_dir)}
        )
        if isinstance(value, dict) and (type(value.get("current")) is bool or value.get("current") is None):
            return value
    except (OSError, TypeError, ValueError, subprocess.SubprocessError, captured.ProcessError):
        pass
    return None


def delivery(state: RunState, current, completion_current):
    """Bound delivery proof file reads; unavailable cannot establish delivery."""
    if completion_current is not True:
        return None
    try:
        value = _observe("delivery", {"state": state, "current": current})
        return value if isinstance(value, dict) else None
    except (OSError, TypeError, ValueError, subprocess.SubprocessError, captured.ProcessError):
        return None


def catalog_offer(state: RunState, *, controller=None, stop_pending=False):
    """Only offer a catalog action after a bounded current observation."""
    try:
        from . import autocode_catalog_reconciliation as catalog
    except ImportError:
        import autocode_catalog_reconciliation as catalog
    if stop_pending or not isinstance(controller, dict) or controller.get("kind") not in ("ended", "boundary"):
        return None
    try:
        if not catalog.eligible(state):
            return None
        result = _observe("catalog_offer", {"state": state, "controller": controller})
        return result if isinstance(result, dict) else None
    except (OSError, TypeError, ValueError, AttributeError, RecursionError, RuntimeError, subprocess.SubprocessError):
        return None


def source_offer(state: RunState, workspace, inspection, *, controller=None, stop_pending=False):
    """Bound the source and catalog observation behind a source-recovery offer."""
    try:
        from . import autocode_source_reconciliation as source
    except ImportError:
        import autocode_source_reconciliation as source
    try:
        if not source.eligible(state, inspection, controller=controller, stop_pending=stop_pending):
            return None
        result = _observe(
            "source_offer",
            {"state": state, "workspace": str(workspace), "inspection": inspection, "controller": controller},
        )
        return result if isinstance(result, dict) else None
    except (OSError, TypeError, ValueError, AttributeError, RecursionError, RuntimeError, subprocess.SubprocessError):
        return None
