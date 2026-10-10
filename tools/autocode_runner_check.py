"""Durable activity for checks the runner executes without a model.

Only this module writes ``active_runner_check``. Status, the task-run view and
the dashboard read it separately from provider attempts: a test suite must not
look like either an idle Validator or an interrupted model call. The caller
owns the run lock and supplies its normal state persistence function.
"""

import os
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

try:
    from . import autocode_command_supervision as command_supervision
    from . import autocode_process as processes
    from . import autocode_util as util
    from . import autocode_verification_schedule as schedule
except ImportError:
    import autocode_command_supervision as command_supervision
    import autocode_process as processes
    import autocode_util as util
    import autocode_verification_schedule as schedule


def _reconcile(record):
    if "supervision" in record:
        command_supervision.reconcile(record["supervision"])


def clear(state, run_dir, persist):
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


def recover_interrupted(state, run_dir, persist):
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


@contextmanager
def track(state, run_dir, stage, summary, persist, *, deferred=False):
    clear(state, run_dir, persist)
    record = {"stage": stage, "summary": summary, "started_at": util.now()}
    published = False
    if not deferred:
        pid = os.getpid()
        owner = processes.identity(processes.process_table({pid})[pid])
        record["processes"] = [owner]
        state["active_runner_check"] = record
        published = True

    def update(summary, *, command=None, output=None):
        _reconcile(record)
        record.pop("supervision", None)
        record.update(summary=summary, updated_at=util.now(), command=command, output=str(output) if output else None)
        if published:
            persist(Path(run_dir) / "state.json", state)

    def checkpoint(command, output, metadata):
        nonlocal published
        _reconcile(record)
        if not published:
            record["processes"] = [deepcopy(metadata["owner"])]
            state["active_runner_check"] = record
            published = True
        record.update(command=command, output=str(output), updated_at=util.now(), supervision=deepcopy(metadata))
        persist(Path(run_dir) / "state.json", state)

    token = command_supervision.CHECKPOINT.set(checkpoint)
    try:
        if not deferred:
            update(summary)
        yield update
    finally:
        command_supervision.CHECKPOINT.reset(token)
        if published:
            clear(state, run_dir, persist)
