"""Durable activity for checks the runner executes without a model.

Only this module writes ``active_runner_check``. Status, the task-run view and
the dashboard read it separately from provider attempts: a test suite must not
look like either an idle Validator or an interrupted model call. The caller
owns the run lock and supplies its normal state persistence function.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import os
import time
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

try:
    from . import autocode_command_supervision as command_supervision
    from . import autocode_interventions as interventions
    from . import autocode_process as processes
    from . import autocode_stop as stop_policy
    from . import autocode_uncertain_cleanup as uncertain_cleanup
    from . import autocode_util as util
    from . import autocode_verification_recovery as verification_recovery
    from . import autocode_verification_schedule as schedule
except ImportError:
    import autocode_command_supervision as command_supervision
    import autocode_interventions as interventions
    import autocode_process as processes
    import autocode_stop as stop_policy
    import autocode_uncertain_cleanup as uncertain_cleanup
    import autocode_util as util
    import autocode_verification_recovery as verification_recovery
    import autocode_verification_schedule as schedule


def _reconcile(record):
    if "supervision" in record:
        command_supervision.reconcile(record["supervision"])


def clear(state: RunState, run_dir, persist):
    """Retire a check after verified cleanup, or after a dead terminal inventory.

    A readable stopped or uncertain receipt whose owner, keeper, provider and
    recorded processes are all gone can be dropped so the check runs again.
    That retirement is an audit event, not a passing result. A live process,
    an unreadable receipt, or denied inspection keeps the hold.
    """
    record = state.get("active_runner_check")
    if record is None:
        return
    if "supervision" in record:
        try:
            _reconcile(record)
        except command_supervision.receipts.OwnershipUncertain:
            try:
                gone = command_supervision.processes_absent(record["supervision"])
            except (processes.ProcessError, OSError, ValueError, KeyError, TypeError):
                gone = False
            if not gone:
                raise
            state.setdefault("user_events", []).append(
                {
                    "kind": "runner_check_retired",
                    "actor": "runner",
                    "at": util.now(),
                    "receipt": record["supervision"].get("receipt"),
                    "reason": "recorded processes were confirmed gone; the check will run again",
                }
            )
    state.pop("active_runner_check")
    persist(Path(run_dir) / "state.json", state)


def recover_interrupted(state: RunState, run_dir, persist):
    """Explicit resume after abandoning a model may retire its stopped check.

    Existing active_runner_check is the sole state-derived input. All admission
    authentication and native absence checks happen below this stateful layer.
    """
    if state.get("active_stage"):
        raise command_supervision.receipts.OwnershipUncertain(
            "Abandon the exact uncertain model attempt shown by status before recovering its verification"
        )
    recovered = schedule.recover_interrupted(
        Path(run_dir) / "check-replay" / "obligations", state.get("active_runner_check")
    )
    if recovered:
        state.setdefault("user_events", []).append(
            {
                "kind": "verification_interrupted_recovered",
                "actor": "user_cli",
                "at": util.now(),
                **recovered,
                "reason": (
                    "Authenticated stopped ownership reconciled with existing completed evidence"
                    if recovered["disposition"]
                    in ("existing_completed_receipt", "published_existing_completed_receipt")
                    else "Authenticated interrupted ownership reconciled; fresh verification is required"
                ),
            }
        )
    clear(state, run_dir, persist)
    return recovered


def _proof_scratch(run_root, output_dir):
    """The scratch tree of the same run-owned proof, including nested evidence directories."""
    regression = run_root / "regression"
    if not output_dir.is_relative_to(regression):
        return None
    parts = output_dir.relative_to(regression).parts
    if not parts:
        return None
    proof = verification_recovery.owned_path(regression / parts[0], run_root, directory=True)
    return verification_recovery.owned_path(proof / "scratch", proof, directory=True)


def auto_recover(state: RunState, run_dir, persist, *, source_revision, source_current, all_due=False):
    """Retire one verified scan-churn interruption and require a fresh proof."""
    check = state.get("active_runner_check")
    pending = Path(run_dir) / "check-replay" / "obligations" / "pending.json"
    if (
        state.get("status") != "RUNNING"
        or state.get("active_stage")
        or not isinstance(check, dict)
        or check.get("stage") != "regression_proof"
        or not check.get("supervision")
    ):
        return False
    check = deepcopy(check)
    scope = {"task_id": state.get("task_id"), "source_revision": source_revision, "all_due": all_due}
    if any(
        row.get("kind") == "runner_check_auto_recovered" and row.get("scope") == scope
        for row in state.get("user_events", [])
    ):
        return False
    try:
        if interventions.pending(run_dir) or source_current() != source_revision:
            return False
        root = Path(run_dir).resolve()
        out = verification_recovery.owned_path(Path(check["output"]).parent, root, directory=True)
        bound = verification_recovery.bind_command(out, check, scratch_root=_proof_scratch(root, out))
        settled = uncertain_cleanup.settle_with_retry(bound)
        if (
            not settled
            or interventions.pending(run_dir)
            or source_current() != source_revision
            or state.get("active_runner_check") != check
        ):
            return False
        recovered = None
        if pending.exists() or pending.is_symlink():
            recovered = schedule.recover_interrupted(pending.parent, check, automatic=True)
            if not recovered:
                return False
        if (
            uncertain_cleanup.settle_with_retry(bound) != settled
            or interventions.pending(run_dir)
            or source_current() != source_revision
            or state.get("active_runner_check") != check
        ):
            return False
    except (
        command_supervision.receipts.OwnershipUncertain,
        processes.ProcessError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        interventions.InterventionError,
    ):
        return False
    state.setdefault("user_events", []).append(
        {
            "kind": "runner_check_auto_recovered",
            "actor": "runner",
            "at": util.now(),
            "scope": scope,
            "scheduled_recovery": recovered,
            **settled,
            "reason": "Native cleanup was rechecked; the interrupted proof must run again",
        }
    )
    state.pop("active_runner_check")
    persist(Path(run_dir) / "state.json", state)
    return True


@contextmanager
def track(state: RunState, run_dir, stage, summary, persist, *, deferred=False):
    clear(state, run_dir, persist)
    started_monotonic = time.monotonic()
    record: dict = {"stage": stage, "summary": summary, "started_at": util.now()}
    published = False
    if not deferred:
        pid = os.getpid()
        owner = processes.identity(processes.process_table({pid})[pid])
        record["processes"] = [owner]
        state["active_runner_check"] = record
        published = True

    def update(
        summary,
        *,
        command=None,
        output=None,
        phase=None,
        current=None,
        total=None,
        elapsed_seconds=None,
    ):
        _reconcile(record)
        record.pop("supervision", None)
        elapsed = (
            round(max(0.0, float(elapsed_seconds)), 1)
            if elapsed_seconds is not None
            else round(max(0.0, time.monotonic() - started_monotonic), 1)
        )
        record.update(
            summary=summary,
            updated_at=util.now(),
            command=command,
            output=str(output) if output else None,
            elapsed_seconds=elapsed,
        )
        for key, value in (("phase", phase), ("current", current), ("total", total)):
            if value is None:
                record.pop(key, None)
            else:
                record[key] = value
        if published:
            persist(Path(run_dir) / "state.json", state)

    def checkpoint(command, output, metadata):
        nonlocal published
        _reconcile(record)
        if not published:
            record["processes"] = [deepcopy(metadata["owner"])]
            state["active_runner_check"] = record
            published = True
        record.update(
            command=command,
            output=str(output),
            updated_at=util.now(),
            elapsed_seconds=round(max(0.0, time.monotonic() - started_monotonic), 1),
            supervision=deepcopy(metadata),
        )
        persist(Path(run_dir) / "state.json", state)

    token = command_supervision.CHECKPOINT.set(checkpoint)

    def cancellation():
        applied = {item.get("id") for item in state.get("applied_interventions", [])}
        try:
            return next(
                (
                    item
                    for item in interventions.pending(run_dir)
                    if item["id"] not in applied and item["kind"] == "pause" and item.get("now") is True
                ),
                None,
            )
        except (interventions.InterventionError, OSError):
            return None

    cancellation_token = command_supervision.CANCELLATION.set(cancellation)
    cancelled = False
    try:
        if not deferred:
            update(summary)
        yield update
    except command_supervision.CheckCancelled as error:
        cancelled = True
        # Uncertain cleanup must keep its owner and unacknowledged request.
        # clear() alone also permits audit retirement, which is insufficient here.
        _reconcile(record)
        if published:
            clear(state, run_dir, persist)
        with interventions.serialized(run_dir):
            if error.request not in interventions.pending(run_dir):
                raise util.Paused(
                    "PAUSED_INTERVENTION_PENDING", "The observed stop request changed before application"
                ) from None
            stop_policy.consume(
                state, Path(run_dir), Path(state["workspace"]), write_json=persist, now=util.now, lock_held=True
            )
        raise util.Paused(state["status"], state.get("stop_reason")) from None
    except command_supervision.receipts.OwnershipUncertain as error:
        if getattr(error, "check_cancelled", False):
            cancelled = True  # retain even a dead uncertain inventory until cancellation cleanup is authenticated
        raise
    finally:
        command_supervision.CANCELLATION.reset(cancellation_token)
        command_supervision.CHECKPOINT.reset(token)
        if published and not cancelled:
            clear(state, run_dir, persist)
