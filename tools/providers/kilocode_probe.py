"""Bounded retries and observations for Kilo's model-free setup commands only."""

import subprocess
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar

PROBE_SECONDS = 120.0
FIRST_ATTEMPT_SECONDS = 60.0
_observer = ContextVar("kilo_setup_observer", default=None)


class KiloProbeTimeout(RuntimeError):
    """Setup exhausted its bound before any model request was launched."""


class KiloProbeIncomplete(RuntimeError):
    """Every attempt's answer ended before it was whole; no model request was launched (#3078)."""


def observer():
    return _observer.get()


@contextmanager
def capture():
    """Keep bounded metadata, never native output, credentials or config."""
    rows, lock = [], threading.Lock()
    parent = observer()

    def record(row):
        with lock:
            rows.append(row)
        if parent is not None:
            parent(row)

    token = _observer.set(record)
    try:
        yield rows
    finally:
        _observer.reset(token)


def probe(words, run, *, seconds=PROBE_SECONDS, observe=None, complete=None):
    """Retry one timed-out setup check once, after subprocess.run has reaped it.

    The first attempt is bounded by FIRST_ATTEMPT_SECONDS. A retry receives only
    the remaining shared budget. Nonzero exits, malformed answers and OS errors
    are not retried, and this helper never receives a model-running command.
    With ``complete(stdout)``, a successful exit whose answer ends before it is
    whole is retried the same way (#3078): a listing cut off under load reads as
    models that offer nothing, so it is never returned as an answer.
    """
    if list(words[1:]) not in (["models", "--verbose"], ["debug", "config"]) and not (
        len(words) == 4 and list(words[1:3]) == ["debug", "agent"]
    ):
        raise ValueError("Kilo setup retries require a model-free catalog or policy command")
    observe = observe if observe is not None else observer()
    started = time.monotonic()
    deadline, count, cause, cut = started + seconds, 0, None, False
    for index in range(2):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        timeout = min(FIRST_ATTEMPT_SECONDS, remaining) if index == 0 else remaining
        count += 1
        attempt_started = time.monotonic()
        row = {
            "check": " ".join(words[1:3]),
            "attempt": count,
            "timeout_seconds": timeout,
            "reason": "initial_check"
            if index == 0
            else "incomplete_answer_retry"
            if cut
            else "model_free_timeout_retry",
            "outcome": "exception",
        }
        try:
            result = run(timeout)
            cut = result.returncode == 0 and complete is not None and not complete(result.stdout)
            row.update(
                outcome="incomplete" if cut else "completed" if result.returncode == 0 else "failed",
                exit_code=result.returncode,
            )
            if not cut:
                return result
        except subprocess.TimeoutExpired as error:
            row["outcome"], cause, cut = "timed_out", error, False
        finally:
            row["duration_seconds"] = max(0, time.monotonic() - attempt_started)
            if observe is not None:
                observe(row)
    if cut:
        raise KiloProbeIncomplete(
            f"Kilo's {' '.join(words[1:3])} answer was cut off before its end in each of {count} attempt(s), "
            "so it cannot be read; no model request was launched"
        )
    raise KiloProbeTimeout(
        f"Kilo's {' '.join(words[1:3])} check exhausted its {seconds:g}-second setup budget "
        f"after {count} attempt(s); no model request was launched"
    ) from cause
