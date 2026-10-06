"""Durable activity for checks the runner executes without a model.

Only this module writes ``active_runner_check``. Status, the task-run view and
the dashboard read it separately from provider attempts: a test suite must not
look like either an idle Validator or an interrupted model call. The caller
owns the run lock and supplies its normal state persistence function.
"""
from contextlib import contextmanager
from copy import deepcopy
import os
from pathlib import Path

try:
    from . import autocode_process as processes, autocode_util as util
    from . import autocode_command_supervision as command_supervision
except ImportError:
    import autocode_process as processes
    import autocode_util as util
    import autocode_command_supervision as command_supervision


def _reconcile(record):
    if "supervision" in record:
        command_supervision.reconcile(record["supervision"])


def clear(state, run_dir, persist):
    """Retire a check only after its retained command has verified cleanup."""
    record = state.get("active_runner_check")
    if record is not None:
        _reconcile(record)
        state.pop("active_runner_check")
        persist(Path(run_dir) / "state.json", state)


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
        record.update(summary=summary, updated_at=util.now(), command=command,
                      output=str(output) if output else None)
        if published:
            persist(Path(run_dir) / "state.json", state)

    def checkpoint(command, output, metadata):
        nonlocal published
        _reconcile(record)
        if not published:
            record["processes"] = [deepcopy(metadata["owner"])]
            state["active_runner_check"] = record
            published = True
        record.update(command=command, output=str(output), updated_at=util.now(),
                      supervision=deepcopy(metadata))
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
