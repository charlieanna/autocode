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
import json
import math
import os
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


def records(state: RunState, run_dir, earlier_dirs=()):
    """This run's probe attempts: those under ``run_dir``, and under the run directories it moved from."""
    roots = [(Path(directory) / "model-preflight").resolve() for directory in (run_dir, *earlier_dirs)]
    return [
        row
        for row in state.get("stages", [])
        if isinstance(row, dict)
        and row.get("stage") == STAGE
        and isinstance(row.get("events"), str)
        and Path(row["events"]).is_absolute()
        and any(Path(row["events"]).resolve().is_relative_to(root) for root in roots)
    ]


def _project_metrics(metrics):
    """Carry exact native request costs into the saved-stage budget projection.

    A request without a reported cost is not missing usage: a subscription route reports none
    for every request, and its complete token counts are priced like any other stage's
    (autocode_usage.stage_rows, which also marks a part-reported cost as a floor). Only
    incomplete native accounting makes the probe's usage a lower bound.
    """
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
        if not native.get("complete"):
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


def _valid_identity(row) -> bool:
    return (
        isinstance(row, dict)
        and type(row.get("pid")) is int
        and row["pid"] > 0
        and type(row.get("birth_identity")) in (int, float)
        and (type(row["birth_identity"]) is int or math.isfinite(row["birth_identity"]))
        and row["birth_identity"] > 0
    )


def _recorded_processes(directory: Path) -> list[dict]:
    path = directory / "processes.json"
    if not path.exists():
        return []
    if path.is_symlink():
        raise ValueError("probe process inventory is symlinked")
    rows = json.loads(path.read_text())
    if not isinstance(rows, list) or not all(_valid_identity(row) for row in rows):
        raise ValueError("probe process inventory is malformed")
    return rows


def _same_current_process(saved: dict) -> bool:
    pid = os.getpid()
    if saved.get("pid") != pid:
        return False
    current = processes.process_table({pid}).get(pid)
    return bool(current and processes.matches(saved, current))


def _settled_cleanup(metadata, directory: Path):
    """Verify process, group and session absence when a probe's keeper cleanup was uncertain.

    A clean stopped keeper receipt uses ``_clean`` directly. When the keeper exited with
    ``phase == "uncertain"`` or a ``cleanup_error`` (such as transient ``SessionScanIncomplete``),
    or lost its keeper after pre-exec admission of a dedicated group and session, a later
    reconciliation can settle the interrupted probe only after confirming the previous owner,
    keeper, provider and every recorded process are gone by birth, and every owned group and
    session is empty across a settled native scan.
    """
    value = _clean(metadata)
    if value is not None:
        return value, None
    value = receipts.load(metadata)
    if not value or value["phase"] not in ("stopped", "uncertain", "armed", "stopping"):
        return None, None
    provider = metadata["provider"]
    dedicated_group = type(provider.get("group")) is int and provider["group"] == provider["pid"]
    dedicated_session = type(provider.get("session")) is int and provider["session"] == provider["pid"]
    if value["phase"] in ("armed", "stopping") and not (dedicated_group and dedicated_session):
        return None, None
    if "session_admissions" in value and not isinstance(value["session_admissions"], list):
        raise ValueError("provider session admissions are malformed")
    extra = _recorded_processes(directory)
    owner = metadata["owner"]
    identities = [
        *([owner] if not _same_current_process(owner) else []),
        metadata["keeper"],
        provider,
        *value.get("processes", []),
        *extra,
    ]
    if processes.live_processes(identities):
        return None, None
    all_rows = [provider, *value.get("processes", []), *extra]
    groups = [row for row in all_rows if type(row.get("group")) is int and row["group"] == row["pid"]]
    sessions = [provider] if dedicated_session else []
    session_admissions = value.get("session_admissions", ())
    if groups or sessions or session_admissions:
        tree = processes.ProcessTree(
            provider["pid"],
            lambda rows: None,
            groups=groups,
            sessions=sessions,
            session_admissions=session_admissions,
        )
        tree.known.update({row["pid"]: dict(row) for row in all_rows})
        if processes.settled_sample(tree, notify=False) or tree.groups or tree.sessions or tree.session_admissions:
            return None, None
    settled = {
        "settled_at": util.now(),
        "prior_phase": value["phase"],
        "prior_cause": value.get("cause"),
        "prior_cleanup_error": value.get("cleanup_error"),
        "reason": "recorded processes and admitted groups/sessions confirmed gone",
    }
    return value, settled


def reconcile(runner, state: RunState, run_dir, checkpoint=None):
    """Settle only bound probe attempts; no workflow-stage recovery is invoked."""
    if checkpoint is None:
        checkpoint = lambda: runner.write_json(Path(run_dir) / "state.json", state)
    for record in records(state, run_dir):
        if record.get("preflight_complete"):
            continue
        directory = Path(record["events"]).parent
        try:
            value, settled = _settled_cleanup(record.get("supervision"), directory)
            if value is None:
                raise ValueError("provider cleanup has not been verified")
            started = dt.datetime.fromisoformat(record["started_at"])
            ended = dt.datetime.fromisoformat(value["observed_at"])
            receipt_duration = (ended - started).total_seconds()
            if receipt_duration < 0:
                raise ValueError("provider receipt precedes admission")
            prior_duration = record.get("duration_seconds", 0)
            duration = max(prior_duration if isinstance(prior_duration, (int, float)) else 0, receipt_duration)
            finished_at = value["observed_at"]
            if isinstance(record.get("finished_at"), str) and record["finished_at"]:
                prior_end = dt.datetime.fromisoformat(record["finished_at"])
                if prior_end > ended:
                    finished_at = record["finished_at"]
        except (OSError, ValueError, KeyError, TypeError, processes.ProcessError) as error:
            raise support.Paused(
                "PAUSED_PROCESS_CLEANUP",
                f"Model access probe cannot be retried: {error}. Evidence: {directory}",
            ) from error
        if settled is not None:
            if directory.is_dir() and not directory.is_symlink():
                util.atomic_json(directory / "cleanup-settled.json", settled)
            record["preflight_cleanup_settled"] = settled
        record.update(
            metrics=_metrics(record),
            duration_seconds=duration,
            finished_at=finished_at,
            preflight_complete=True,
            interrupted=True,
        )
        # A recovered event prefix never proves a successful access response.
        record["preflight_outcome"] = {
            "model": record["model"],
            "at": record["finished_at"],
            "seconds": round(duration, 2),
            "status": "PAUSED_PROVIDER_UNCERTAIN",
            "detail": (
                "the previous probe was interrupted; provider cleanup confirmed gone on recheck"
                if settled is not None
                else "the previous probe was interrupted"
            ),
            "blocking": False,
            "evidence": str(directory),
        }
        _charge(runner, state, record)
        checkpoint()


class Attempt:
    """Publish admission before exec and the charged result before caching it."""

    def __init__(self, runner, state: RunState, run_dir, adapter, model, checkpoint, clock, *, preflight_identity=None):
        self.runner, self.state, self.checkpoint, self.clock = runner, state, checkpoint, clock
        identity = uuid.uuid4().hex
        self.directory = Path(run_dir) / "model-preflight" / model.replace("/", "__") / f"attempt-{identity}"
        self.record = {
            "stage": STAGE,
            "role": STAGE,
            "engine": "opencode",
            "runner_owned": True,
            "model": model,
            "preflight_identity": preflight_identity,
            "iteration": state.get("iteration", 1),
            "attempt_id": f"preflight/{identity}",
            "events": str(self.directory / "events.jsonl"),
            "output": str(self.directory / "report.json"),
            "launch_route": {
                "engine": "opencode",
                "provider": getattr(adapter, "NAME", "opencode"),
                "model": model,
                "effort": (preflight_identity or {}).get("reasoning_effort", "low"),
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
