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
    if not isinstance(pending, dict):
        return "The saved acceptance verification retry is malformed"
    if any(
        state.get(key)
        for key in ("active_stage", "active_runner_check", "uncertain_artifacts", "pending_report_repair")
    ):
        return "Acceptance verification still has active or uncertain work"
    if type(pending.get("attempts")) is not int or pending["attempts"] < 0:
        return "The saved acceptance verification retry count is invalid"
    try:
        record, pins = pending["record"], pending["pins"]
        if (
            not isinstance(record, dict)
            or not isinstance(pins, dict)
            or set(pins) != {record[key] for key in ARTIFACTS}
            or not record_current(state, record, workspace)
            or any(util.file_hash(path) != pin for path, pin in pins.items())
            or util.read(record["output"]) != pending["value"]
        ):
            return "Retained acceptance report, approved task or source changed"
    except (OSError, ValueError, KeyError, TypeError, AttributeError, util.Paused):
        return "Retained acceptance report, approved task or source could not be verified"
    return None
