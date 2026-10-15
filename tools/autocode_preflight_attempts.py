"""Durable, charged access-probe attempts, separate from workflow adoption.

Probe records use existing stages/active_seconds; no controller is imported.
The caller supplies its normal accounting and checkpoint functions. Supervision
metadata is saved before provider release, so interrupted requests cannot vanish
from usage or become an unverified cached pass.

The attempt-local preflight_complete flag gates retry and intervention boundaries;
preflight_charged_seconds lets reconciliation add only uncharged elapsed time.
"""

from __future__ import annotations

import datetime as dt
import math
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_command_receipt as receipts
    from . import autocode_process as processes
    from . import autocode_support as support
    from . import autocode_util as util
except ImportError:
    import autocode_command_receipt as receipts
    import autocode_process as processes
    import autocode_support as support
    import autocode_util as util

STAGE = "model_preflight"


def records(state: RunState, run_dir):
    root = (Path(run_dir) / "model-preflight").resolve()
    return [
        row
        for row in state.get("stages", [])
        if isinstance(row, dict)
        and row.get("stage") == STAGE
        and isinstance(row.get("events"), str)
        and Path(row["events"]).is_absolute()
        and Path(row["events"]).resolve().is_relative_to(root)
    ]


def _project_metrics(metrics):
    """Carry exact native request costs into the saved-stage budget projection."""
    result = dict(metrics)
    context = result.get("request_context") or {}
    native = context.get("accounting") or {}
    requests = native.get("requests") or []
    if requests:
        costs = [row.get("reported_cost_usd") for row in requests]
        result["provider_cost_usd"] = (
            sum(costs)
            if all(type(value) in (int, float) and math.isfinite(value) and value >= 0 for value in costs)
            else None
        )
        if not native.get("complete") or any(value is None for value in costs):
            result["provider_tokens_partial"] = True
    return result


def _metrics(record):
    path = Path(record["events"])
    return _project_metrics(support.event_metrics(path)) if path.is_file() else {"provider_tokens_partial": True}


def _charge(runner, state: RunState, record):
    duration = record["duration_seconds"]
    charged = record.get("preflight_charged_seconds", 0)
    delta = max(0, duration - charged)
    if record.get("accounted") and not delta:
        return
    record["accounted"] = False
    record["duration_seconds"] = delta
    try:
        account = getattr(runner, "account_stage", None)
        if account is not None:
            account(state, record)
        else:
            # check() also supports a minimal, in-memory runner facade.
            state["active_seconds"] = state.get("active_seconds", 0) + delta
            record["accounted"] = True
    finally:
        record["duration_seconds"] = duration
    record["preflight_charged_seconds"] = max(charged, duration)


def _clean(metadata):
    value = receipts.load(metadata)
    if not value or value["phase"] != "stopped" or value["cleanup_error"]:
        return None
    identities = [metadata["keeper"], metadata["provider"], *value.get("processes", [])]
    if processes.live_processes(identities):
        return None
    return value


def reconcile(runner, state: RunState, run_dir, checkpoint=None):
    """Settle only bound probe attempts; no workflow-stage recovery is invoked."""
    if checkpoint is None:
        checkpoint = lambda: runner.write_json(Path(run_dir) / "state.json", state)
    for record in records(state, run_dir):
        if record.get("preflight_complete"):
            continue
        try:
            value = _clean(record.get("supervision"))
            if value is None:
                raise ValueError("provider cleanup has not been verified")
            started = dt.datetime.fromisoformat(record["started_at"])
            ended = dt.datetime.fromisoformat(value["observed_at"])
            duration = (ended - started).total_seconds()
            if duration < 0:
                raise ValueError("provider receipt precedes admission")
        except (OSError, ValueError, KeyError, TypeError, processes.ProcessError) as error:
            raise support.Paused(
                "PAUSED_PROCESS_CLEANUP",
                f"Model access probe cannot be retried: {error}. Evidence: {Path(record['events']).parent}",
            ) from error
        record.update(
            metrics=_metrics(record),
            duration_seconds=duration,
            finished_at=value["observed_at"],
            preflight_complete=True,
            interrupted=True,
        )
        # A recovered event prefix never proves a successful access response.
        record["preflight_outcome"] = {
            "model": record["model"],
            "at": record["finished_at"],
            "seconds": round(duration, 2),
            "status": "PAUSED_PROVIDER_UNCERTAIN",
            "detail": "the previous probe was interrupted",
            "blocking": False,
            "evidence": str(Path(record["events"]).parent),
        }
        _charge(runner, state, record)
        checkpoint()


class Attempt:
    """Publish admission before exec and the charged result before caching it."""

    def __init__(self, runner, state: RunState, run_dir, adapter, model, checkpoint, clock):
        self.runner, self.state, self.checkpoint, self.clock = runner, state, checkpoint, clock
        identity = uuid.uuid4().hex
        self.directory = Path(run_dir) / "model-preflight" / model.replace("/", "__") / f"attempt-{identity}"
        self.record = {
            "stage": STAGE,
            "role": STAGE,
            "engine": "opencode",
            "runner_owned": True,
            "model": model,
            "iteration": state.get("iteration", 1),
            "attempt_id": f"preflight/{identity}",
            "events": str(self.directory / "events.jsonl"),
            "output": str(self.directory / "report.json"),
            "launch_route": {
                "engine": "opencode",
                "provider": getattr(adapter, "NAME", "opencode"),
                "model": model,
                "effort": "low",
            },
            "started_at": util.now(),
            "preflight_complete": False,
        }
        self.started = clock()
        self.published = False

    def _publish(self):
        if not self.published:
            self.state.setdefault("stages", []).append(self.record)
            self.published = True
        self.checkpoint()

    def admit(self, metadata):
        self.record["supervision"] = metadata
        self._publish()

    def finish(self, outcome=None, error=None):
        duration = max(0, self.clock() - self.started)
        self.record.update(duration_seconds=duration, finished_at=util.now())
        if outcome is not None:
            self.record.update(
                metrics=_project_metrics(outcome.get("metrics") or {}),
                exit_code=outcome.get("exit_code"),
                timed_out=bool(outcome.get("timed_out")),
                preflight_complete=True,
            )
            saved = {
                key: value
                for key, value in outcome.items()
                if key not in ("metrics", "exit_code", "timed_out", "duration_seconds")
            }
        else:
            status = (
                "PAUSED_INTERRUPTED"
                if isinstance(error, KeyboardInterrupt)
                else getattr(error, "status", "PAUSED_PROVIDER_UNCERTAIN")
            )
            self.record.update(metrics=_metrics(self.record), interrupted=True)
            metadata = self.record.get("supervision")
            if metadata is None:
                # Owned transport cannot exec before its admission callback.
                self.record.update(engine="runner", metrics={"provider_cost_usd": 0}, preflight_complete=True)
            else:
                try:
                    self.record["preflight_complete"] = _clean(metadata) is not None
                except (OSError, ValueError, KeyError, TypeError, processes.ProcessError):
                    self.record["preflight_complete"] = False
            saved = {
                "model": self.record["model"],
                "at": self.record["finished_at"],
                "seconds": round(duration, 2),
                "status": status,
                "detail": str(error),
                "blocking": False,
                "evidence": str(self.directory),
            }
        self.record["preflight_outcome"] = saved
        _charge(self.runner, self.state, self.record)
        self._publish()
        return saved
