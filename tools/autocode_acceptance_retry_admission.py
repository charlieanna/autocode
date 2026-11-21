"""One read-only admission check for a retained acceptance verification report."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_acceptance_tests as acceptance
    from . import autocode_contract_identity as identity
    from . import autocode_source_scope as source_scope
    from . import autocode_util as util
except ImportError:
    import autocode_acceptance_tests as acceptance
    import autocode_contract_identity as identity
    import autocode_source_scope as source_scope
    import autocode_util as util

ARTIFACTS = ("output", "events", "schema", "before_ref", "after_ref")


def shape_problem(pending):
    if not isinstance(pending, dict):
        return "The saved acceptance verification retry is malformed"
    if type(pending.get("attempts")) is not int or pending["attempts"] < 0:
        return "The saved acceptance verification retry count is invalid"
    investigations = pending.get("investigations", [])
    if not isinstance(investigations, list) or any(not isinstance(row, str) or not row for row in investigations):
        return "The saved acceptance verification retry investigations are invalid"
    if "provenance_retry" in pending and type(pending["provenance_retry"]) is not bool:
        return "The saved acceptance verification retry provenance is invalid"
    return None


def evidence_intact(pending):
    """The retained report and all of its pinned artifacts still match the saved bytes."""
    try:
        record, pins = pending["record"], pending["pins"]
        return bool(
            isinstance(record, dict)
            and isinstance(pins, dict)
            and set(pins) == {record[key] for key in ARTIFACTS}
            and all(util.file_hash(path) == pin for path, pin in pins.items())
            and util.read(record["output"]) == pending["value"]
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
        return False


def replacement(state: RunState, pending):
    """A uniquely authenticated later plan may supersede the old retained report."""
    try:
        return _replacement(state, pending)
    except (KeyError, ValueError, TypeError, AttributeError, RecursionError):
        return None


def _replacement(state: RunState, pending):
    if shape_problem(pending):
        return None
    if any(
        state.get(key)
        for key in ("active_stage", "active_runner_check", "uncertain_artifacts", "pending_report_repair")
    ):
        return None
    current = state.get("goal_contract") or {}
    task = state.get("current_task")
    record = pending.get("record")
    if (
        not isinstance(record, dict)
        or not identity.approved(state)
        or not isinstance(task, dict)
        or not task.get("id")
        or any(state.get(key) for key in ("pending_questions", "user_request"))
        or record.get("stage") != acceptance.STAGE
        or record.get("accounted") is not True
        or type(record.get("exit_code")) is not int
        or record["exit_code"] != 0
        or any(record.get(key) for key in ("timed_out", "interrupted", "rejected", "abandoned", "report_only"))
        or not record.get("contract_hash")
        or record.get("contract_hash") == current.get("hash")
    ):
        return None
    previous = [
        row
        for row in state.get("contract_history") or []
        if isinstance(row, dict) and row.get("hash") == record["contract_hash"] and identity.sealed(row)
    ]
    if len(previous) != 1:
        return None
    old = previous[0]
    approval = identity.historical_approval(state, old)
    events = state.get("user_events") or []
    if (
        not approval
        or old.get("task_id") != current.get("task_id")
        or old["revision"] >= current["revision"]
        or events.index(approval) >= events.index(current["approval_event"])
        or identity.equivalent_reviewed_body(old["body"], current["body"])
        or not evidence_intact(pending)
    ):
        return None
    return {"revision": current["revision"], "contract_hash": current["hash"]}


def record_current(state: RunState, record, workspace):
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


def problem(state: RunState, pending, workspace):
    """Why this exact retained report cannot be rechecked; never mutate or execute it."""
    if invalid := shape_problem(pending):
        return invalid
    if any(
        state.get(key)
        for key in ("active_stage", "active_runner_check", "uncertain_artifacts", "pending_report_repair")
    ):
        return "Acceptance verification still has active or uncertain work"
    try:
        record = pending["record"]
        if not isinstance(record, dict) or not evidence_intact(pending) or not record_current(state, record, workspace):
            return "Retained acceptance report, approved task or source changed"
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError, util.Paused):
        return "Retained acceptance report, approved task or source could not be verified"
    return None
