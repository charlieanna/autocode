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


def budgeted(function):
    """Share one allowance through nested status and recovery projections."""

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        if _deadline.get() is not None:
            return function(*args, **kwargs)
        token = _deadline.set(time.monotonic() + TIMEOUT)
        try:
            return function(*args, **kwargs)
        finally:
            _deadline.reset(token)

    return wrapper


def _observe(operation, payload, *, timeout=None):
    """Collect a worker result only after the owned process family is gone."""
    deadline = _deadline.get()
    remaining = TIMEOUT if deadline is None else deadline - time.monotonic()
    if timeout is not None:
        remaining = min(remaining, timeout)
    if remaining <= 0:
        raise subprocess.TimeoutExpired("status-worker", TIMEOUT)
    result = captured.run(
        [sys.executable, str(Path(__file__).with_name("autocode_status_observation_worker.py").resolve()), "--worker"],
        input=json.dumps({"operation": operation, "payload": payload}),
        timeout=remaining,
        check=True,
    )
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


def catalog_offer(state: RunState, *, controller=None, stop_pending=False):
    """Only offer a catalog action after a bounded current observation."""
    try:
        from . import autocode_catalog_reconciliation as catalog
    except ImportError:
        import autocode_catalog_reconciliation as catalog
    if stop_pending or not isinstance(controller, dict) or controller.get("kind") not in ("ended", "boundary"):
        return None
    if not catalog.eligible(state):
        return None
    try:
        result = _observe("catalog_offer", {"state": state, "controller": controller})
        return result if isinstance(result, dict) else None
    except (OSError, TypeError, ValueError, subprocess.SubprocessError, captured.ProcessError):
        return None
