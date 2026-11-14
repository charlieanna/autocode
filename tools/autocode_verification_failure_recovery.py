"""Decide the observed pre-provider Git failure without a retry or a person.

The allocator alone establishes collected-child and owned-copy cleanup facts.
Unknown cleanup or any unsettled work remains blocked, never a terminal claim.
The existing runner-owned operational journal retains every observation and the
existing failed stop outcome is used only once preparation is confirmed quiet.
"""

from __future__ import annotations

import subprocess
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_recovery_limits as limits
    from . import autocode_run_status as run_status
    from . import autocode_source_snapshot as source
    from . import autocode_stop as stop
    from . import autocode_util as util
    from . import autocode_verification_failure as failure
except ImportError:
    import autocode_recovery_limits as limits
    import autocode_run_status as run_status
    import autocode_source_snapshot as source
    import autocode_stop as stop
    import autocode_util as util
    import autocode_verification_failure as failure


def recover(runtime, state: RunState, run_dir, workspace, error):
    if type(error) is not failure.PrelaunchFailure or type(error.failure) is not failure.GitPreparationFailed:
        return False
    observed = error.failure
    unsettled = [key for key in limits.UNSETTLED if state.get(key)]
    bound = observed.workspace == str(Path(workspace).resolve()) and observed.run_dir == str(Path(run_dir).resolve())
    binding = {
        "workspace": str(Path(workspace).resolve()),
        "run_dir": str(Path(run_dir).resolve()),
        "task_id": state.get("task_id"),
        "current_task": deepcopy(state.get("current_task")),
        "contract_hash": util.digest(state.get("goal_contract")),
        "source_revision": observed.source_revision,
        "source_paths": observed.source_paths,
        "next_stage": state.get("next_stage"),
    }
    quiet = bound and observed.cleanup_confirmed is True and bool(observed.source_revision) and not unsettled
    current_revision, source_error = None, None
    if quiet:
        # Failure-only freshness check. Successful copies reuse their existing
        # before/after snapshots and pay no extra inventory cost for this path.
        try:
            current_revision = source.snapshot(workspace, paths=observed.source_paths)["revision"]
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error_current:
            source_error = util.redact(str(error_current))[:2000]
        quiet = current_revision == observed.source_revision and source_error is None
    evidence = {
        "operation": "verification_preparation",
        "binding": binding,
        "preparation": {
            "workspace": observed.workspace,
            "run_dir": observed.run_dir,
            "control": observed.control,
            "child_collected": True,
            "returncode": observed.returncode,
            "provider_started": False,
            "cleanup_confirmed": observed.cleanup_confirmed,
            "cleanup_error": util.redact(observed.cleanup_error or "")[:2000] or None,
            "dispatch_binding_matches": bound,
        },
        "unsettled": unsettled,
        "current_source_revision": current_revision,
        "source_check_error": source_error,
    }
    reason = util.redact(str(error))
    if not quiet:
        reason += " AutoCode is blocked: source binding, cleanup or ownership is not confirmed; retained work is kept. No new stage is started."
    resolver = state.setdefault("resolver", {})
    identities = resolver.setdefault("operational_diagnostics", [])
    receipts = resolver.setdefault("operational_receipts", {})
    # An append-only local journal ordinal, not a digest/token a model can cite
    # and not authority to replay, accept source or clear a hold.
    identity = "verification-preparation-" + str(len(identities) + 1)
    at = util.now()
    record = {
        "stage": "resolver",
        "role": "resolver",
        "engine": "runner",
        "runner_owned": True,
        "iteration": state.get("iteration"),
        "finished_at": at,
        "exit_code": 0,
        "runner_calls": 0,
        "summary": reason,
        "metrics": {"provider_tokens": {"input_tokens": 0, "output_tokens": 0}},
        "decision": {"action": "could_not_finish" if quiet else "blocked", "rationale": reason},
        "receipt": {"id": identity, "callbacks_used": [], "scope": "verification_preparation", "evidence": evidence},
    }
    receipts[identity] = deepcopy(record)
    identities.append(identity)
    state.setdefault("stages", []).append(deepcopy(record))
    state.setdefault("history", []).append(deepcopy(record))
    if quiet:
        stop.end(state, reason, evidence, at=at)
    else:
        run_status.pause(state, error.status, reason)
    runtime.write_json(Path(run_dir) / "state.json", state)
    raise util.Paused(state["status"], state["stop_reason"])
