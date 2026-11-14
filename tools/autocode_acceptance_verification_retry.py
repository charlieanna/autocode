"""Bounded runner-only retries of a completed acceptance writer's verification.

The acceptance_tests record owns the pending report, its pins and spent retries.
No receipt, model call, usage reset or plan authority is supplied here.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_acceptance_tests as acceptance
    from . import autocode_contract_identity as identity
    from . import autocode_planning_advice as recovery_binding
    from . import autocode_report_source as report_source
    from . import autocode_result_application as application
    from . import autocode_run_status as run_status
    from . import autocode_source_scope as source_scope
    from . import autocode_util as util
except ImportError:
    import autocode_acceptance_tests as acceptance
    import autocode_contract_identity as identity
    import autocode_planning_advice as recovery_binding
    import autocode_report_source as report_source
    import autocode_result_application as application
    import autocode_run_status as run_status
    import autocode_source_scope as source_scope
    import autocode_util as util

KEY = "verification_retry"
LIMIT = 1
ARTIFACTS = ("output", "events", "schema", "before_ref", "after_ref")


def _record_current(state: RunState, record, workspace):
    return bool(
        identity.approved(state)
        and record.get("stage") == acceptance.STAGE
        and record.get("contract_hash") == (state.get("goal_contract") or {}).get("hash")
        and record.get("task_id") == (state.get("current_task") or {}).get("id")
        and record.get("accounted") is True
        and type(record.get("exit_code")) is int
        and record["exit_code"] == 0
        and not any(state.get(key) for key in ("pending_questions", "user_request"))
        and not any(record.get(key) for key in ("timed_out", "interrupted", "rejected", "abandoned", "report_only"))
        and record.get("source_revision") == source_scope.snapshot(workspace, state)["revision"]
    )


def retain(state: RunState, value, record, workspace, reason):
    """Queue one fresh local verification; never replenish a saved allowance."""
    saved = state.setdefault(acceptance.KEY, {})
    pending = saved.get(KEY)
    if pending:
        pending.setdefault("failures", []).append({"at": util.now(), "reason": str(reason)})
        return False
    try:
        if not _record_current(state, record, workspace) or value != util.read(record["output"]):
            return False
        pins = {record[key]: util.file_hash(record[key]) for key in ARTIFACTS}
    except (OSError, KeyError, TypeError, ValueError):
        return False
    saved[KEY] = {
        "record": copy.deepcopy(record),
        "value": copy.deepcopy(value),
        "pins": pins,
        "attempts": 0,
        "failures": [{"at": util.now(), "reason": str(reason)}],
    }
    run_status.running(state, "EXECUTING", next_stage=acceptance.STAGE)
    return True


def retain_legacy(state: RunState, workspace):
    """An explicit resume may recover this old execution-only pause from pinned events."""
    if (
        state.get("status") != "PAUSED_ACCEPTANCE_TESTS"
        or state.get("next_stage") != acceptance.STAGE
        or not str(state.get("stop_reason", "")).startswith(
            "The runner could not establish the approved pre-build proof: Cannot classify passing tests "
            "against the original reference:"
        )
        or any(
            state.get(key)
            for key in ("active_stage", "active_runner_check", "uncertain_artifacts", "pending_report_repair")
        )
        or (state.get(acceptance.KEY) or {}).get(KEY)
    ):
        return False
    try:
        record = state["stages"][-1]
        if record.get("engine") != "opencode" or record.get("output_mode") != "opencode_events":
            return False
        pin = record["metrics"]["request_context"]["accounting"]["event_file"]
        if pin["path"] != record["events"] or util.file_hash(pin["path"]) != pin["sha256"]:
            return False
        extracted = report_source.original_report_for_repair(record)
        value = extracted["report"]
        if extracted.get("extraction_error") or not isinstance(value, dict) or value.get("cannot_write"):
            return False
        return retain(state, value, record, workspace, state["stop_reason"])
    except (OSError, IndexError, KeyError, TypeError, ValueError):
        return False


def finish(state: RunState):
    """Archive the retry and its failures after a genuine verification transition."""
    saved = state.get(acceptance.KEY) or {}
    if pending := saved.pop(KEY, None):
        saved.setdefault("verification_retries", []).append(pending)


def _diagnosed_retry(state: RunState, pending):
    advice = state.get("stuck_investigation") or {}
    granted = advice.get("identity")
    return (
        granted
        if (
            advice.get("in_force") is True
            and advice.get("stage") == acceptance.STAGE
            and advice.get("status") == "PAUSED_INVALID_OUTPUT"
            and isinstance(granted, str)
            and granted
            and granted not in pending.get("investigations", [])
            and recovery_binding.applies(state, advice, acceptance.STAGE)
        )
        else None
    )


def dispatch(runtime, state: RunState, stage, workspace, run_dir, *, apply):
    """Apply a pinned report through ordinary owned checks, without saving/charging it twice."""
    pending = (state.get(acceptance.KEY) or {}).get(KEY)
    if stage != acceptance.STAGE or not pending:
        return None
    record = copy.deepcopy(pending["record"])
    checking = False
    try:
        if any(
            state.get(key)
            for key in ("active_stage", "active_runner_check", "uncertain_artifacts", "pending_report_repair")
        ):
            raise ValueError("Acceptance verification still has active or uncertain work")
        attempts = pending.get("attempts")
        if type(attempts) is not int or attempts < 0:
            raise ValueError("The saved acceptance verification retry count is invalid")
        diagnosis = _diagnosed_retry(state, pending) if attempts >= LIMIT else None
        if attempts >= LIMIT and not diagnosis:
            raise ValueError("The bounded acceptance verification retry is exhausted")
        if (
            set(pending["pins"]) != {record[key] for key in ARTIFACTS}
            or not _record_current(state, record, workspace)
            or any(util.file_hash(path) != pin for path, pin in pending["pins"].items())
        ):
            raise ValueError("Retained acceptance report, approved task or source changed")
        value = runtime.load_stage_report(record, workspace, state=state)
        if value != pending["value"]:
            raise ValueError("Retained acceptance report no longer matches its validated value")
        pending["attempts"] += 1
        if diagnosis:
            pending.setdefault("investigations", []).append(diagnosis)
        checking = True
        state.setdefault("reconciliation_notes", []).append(
            {
                "at": util.now(),
                "stage": stage,
                "reason": "Retrying local acceptance verification without another model call",
                "output": record["output"],
                "attempt": pending["attempts"],
            }
        )
        # Spend before execution: a crash or another Resume never resets this bound.
        runtime.write_json(Path(run_dir) / "state.json", state)
        application.commit(
            state,
            run_dir,
            stage,
            lambda candidate: apply(stage, candidate, value, record, workspace, run_dir=run_dir),
            lambda candidate: runtime.commit_boundary_candidate(state, candidate, run_dir, workspace),
            persist=runtime.write_json,
        )
        return record
    except (OSError, ValueError, KeyError) as error:
        if checking:
            pending["failures"].append({"at": util.now(), "reason": str(error)})
            runtime.write_json(Path(run_dir) / "state.json", state)
        # Existing bounded investigation owns machinery after the local retry.
        raise util.Paused(
            "PAUSED_INVALID_OUTPUT", f"Acceptance verification retry could not finish: {error}"
        ) from error
