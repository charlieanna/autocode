"""Bounded runner-only retries of a completed acceptance writer's verification.

The acceptance_tests record owns the pending report, its pins and spent retries.
No receipt, model call, usage reset or plan authority is supplied here.

A retry whose ``kind`` is RUNNER_NOT_STARTED (#3877: the test command could not start, so the
written tests were never judged) runs again on each explicit resume instead of stopping at LIMIT:
it makes no model call, and once its runner starts it is archived (``runner_started``) and the
ordinary verdict paths, with the normal per-case proof, decide.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_acceptance_provenance as provenance
    from . import autocode_acceptance_tests as acceptance
    from . import autocode_contract_identity as identity
    from . import autocode_planning_advice as recovery_binding
    from . import autocode_report_source as report_source
    from . import autocode_result_application as application
    from . import autocode_run_status as run_status
    from . import autocode_source_scope as source_scope
    from . import autocode_util as util
except ImportError:
    import autocode_acceptance_provenance as provenance
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
RUNNER_NOT_STARTED = "runner_not_started"
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


def retain(state: RunState, value, record, workspace, reason, *, kind=None, receipt=None):
    """Queue one fresh local verification; never replenish a saved allowance.

    ``kind`` (RUNNER_NOT_STARTED) is kept on the record, also on one already pending, so a re-run whose
    test command could not start is retried on the next resume; ``receipt`` (the failed command's command,
    exit code, output and tail) is kept in its failure entry."""
    saved = state.setdefault(acceptance.KEY, {})
    pending = saved.get(KEY)
    failure = {"at": util.now(), "reason": str(reason), **({"kind": kind} if kind else {}), **(receipt or {})}
    if pending:
        pending.setdefault("failures", []).append(failure)
        if kind == RUNNER_NOT_STARTED:
            pending["kind"] = kind
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
        "failures": [failure],
        **({"kind": kind} if kind else {}),
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


def runner_started(state: RunState):
    """A runner-not-started retry whose test command now started is archived; the ordinary paths continue."""
    if ((state.get(acceptance.KEY) or {}).get(KEY) or {}).get("kind") == RUNNER_NOT_STARTED:
        finish(state)


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


def _replacement_plan(state: RunState, pending):
    """A distinct, later approved plan can retire a previous plan's retained report."""
    current = state.get("goal_contract") or {}
    task = state.get("current_task")
    record = pending.get("record") or {}
    if (
        not identity.approved(state)
        or not isinstance(task, dict)
        or not task.get("id")
        or any(state.get(key) for key in ("pending_questions", "user_request"))
        or record.get("stage") != acceptance.STAGE
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
    old_approval = identity.historical_approval(state, old)
    events = state.get("user_events") or []
    if (
        not old_approval
        or old.get("task_id") != current.get("task_id")
        or old["revision"] >= current["revision"]
        or events.index(old_approval) >= events.index(current["approval_event"])
        or identity.equivalent_reviewed_body(old["body"], current["body"])
    ):
        return None
    return {"revision": current["revision"], "contract_hash": current["hash"]}


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
        if replacement := _replacement_plan(state, pending):
            candidate = copy.deepcopy(state)
            retained = candidate[acceptance.KEY][KEY]
            retained["superseded_by"] = replacement
            finish(candidate)
            candidate.setdefault("user_events", []).append(
                {"kind": "acceptance_report_superseded", "actor": "runner", "at": util.now(), **replacement}
            )
            runtime.write_json(Path(run_dir) / "state.json", candidate)
            cast(dict, state).clear()
            state.update(candidate)
            return None
        attempts = pending.get("attempts")
        if type(attempts) is not int or attempts < 0:
            raise ValueError("The saved acceptance verification retry count is invalid")
        # A runner that never started is retried on each explicit resume, without a model call.
        exhausted = attempts >= LIMIT and pending.get("kind") != RUNNER_NOT_STARTED
        diagnosis = _diagnosed_retry(state, pending) if exhausted else None
        if (
            set(pending["pins"]) != {record[key] for key in ARTIFACTS}
            or not _record_current(state, record, workspace)
            or any(util.file_hash(path) != pin for path, pin in pending["pins"].items())
        ):
            raise ValueError("Retained acceptance report, approved task or source changed")
        value = runtime.load_stage_report(record, workspace, state=state)
        if value != pending["value"]:
            raise ValueError("Retained acceptance report no longer matches its validated value")
        passing = value.get("already_passing") or []
        passing_ids = {row.get("case_id") for row in passing if isinstance(row, dict)}
        inherited = (
            provenance.inherited_cases(state, reports=value.get("tests"), workspace=workspace)
            if exhausted and not diagnosis and not pending.get("provenance_retry") and passing_ids
            else set()
        )
        provenance_retry = bool(passing_ids and len(passing_ids) == len(passing) and passing_ids <= inherited)
        if exhausted and not diagnosis and not provenance_retry:
            raise ValueError("The bounded acceptance verification retry is exhausted")
        pending["attempts"] += 1
        if diagnosis:
            pending.setdefault("investigations", []).append(diagnosis)
        if provenance_retry:
            pending["provenance_retry"] = True
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
