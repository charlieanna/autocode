"""Upfront rejection triage separating check failures from report defects.

Tools-off report repair can only fix JSON wrappers, missing/misformatted fields,
and uncited event references; it can never re-run a failed test or fabricate an
unexecuted check. Separating ``tests_failed`` and ``missing_execution`` before
queuing report repair avoids wasted same-model repair loops.

A check a reviewer reported as passing that fails the clean replay (``ReportedCheckFailure``) is not a
failing test: the code may be fine and the report false. autocode_reported_check_rerun owns that refusal
(the reviewer runs again), then the existing report repair and pause; sending the Builder back would leave
it nothing to build. A ``tests_failed`` send-back is bounded to ``ROUNDS`` for each stage of a task, not
one per source revision: each Builder round makes a new revision, so a per-revision bound looped Builder
and reviewer until the iteration ceiling paused the run for the person.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_acceptance_tests_job as acceptance_tests_job
    from . import autocode_builder_policy as builder_policy
    from . import autocode_goals as goals
    from . import autocode_report_repair_queue as report_repair_queue
    from . import autocode_resolve_repair_job as resolve_repair_job
    from . import autocode_run_status as run_status
    from . import autocode_stop as stop
    from . import autocode_support as support
    from . import autocode_util as util
    from . import autocode_verification_plan as verification_plan
except ImportError:
    import autocode_acceptance_tests_job as acceptance_tests_job
    import autocode_builder_policy as builder_policy
    import autocode_goals as goals
    import autocode_report_repair_queue as report_repair_queue
    import autocode_resolve_repair_job as resolve_repair_job
    import autocode_run_status as run_status
    import autocode_stop as stop
    import autocode_support as support
    import autocode_util as util
    import autocode_verification_plan as verification_plan

REVIEW_STAGES = ("tester", "review_completion", "review_checkpoint", "verify_build", "judge_acceptance")
EXECUTION_STAGES = ("builder", *REVIEW_STAGES)
ROUNDS = 1  # tests_failed send-backs to the Builder for each stage of a task


def executed_checks(record: dict) -> list[dict]:
    """Return executed shell command records extracted from ``record['events']``."""
    events_path = record.get("events")
    if not isinstance(events_path, str) or not events_path or not Path(events_path).is_file():
        return []
    return [
        {
            "command": e["item"]["command"],
            "exit_code": e["item"]["exit_code"],
            "evidence_ref": "event:" + e["item"]["id"],
        }
        for e in support.events(events_path)
        if e.get("type") == "item.completed"
        and isinstance(e.get("item"), dict)
        and e["item"].get("type") == "command_execution"
        and isinstance(e["item"].get("command"), str)
        and isinstance(e["item"].get("id"), str)
        and type(e["item"].get("exit_code")) is int
    ]


def classify_rejection(state: RunState, record: dict, error: Exception) -> str:
    """Classify a stage rejection as ``tests_failed``, ``missing_execution``, or ``report_defect``."""
    if isinstance(error, verification_plan.ReportedCheckFailure):
        return "report_defect"  # a false report, settled by autocode_reported_check_rerun and the existing route
    if isinstance(error, verification_plan.PlannedCheckFailure):
        return "tests_failed"
    if isinstance(error, report_repair_queue.ExecutionEvidenceRequired) and not isinstance(
        error, acceptance_tests_job.TestsNeedRewriting
    ):
        text = str(error).lower()
        checks = executed_checks(record)
        if "exit " in text or "failed check" in text or any(row["exit_code"] != 0 for row in checks):
            return "tests_failed"
        return "missing_execution"
    if (
        isinstance(error, ValueError)
        and "lacks successful executed checks" in str(error)
        and not any(row["exit_code"] == 0 for row in executed_checks(record))
    ):
        return "missing_execution"
    return "report_defect"


def reroute(state: RunState, record: dict, error: Exception) -> bool:
    """Route non-repairable test/execution failures away from tools-off report repair."""
    if (
        stop.applied_stop(state)
        or record.get("report_only")
        or record.get("exit_code") != 0
        or record.get("timed_out")
        or record.get("interrupted")
        or not record.get("source_revision")
        or not goals.approved(state)
        or not (state.get("current_task") or {}).get("id")
    ):
        return False
    kind = classify_rejection(state, record, error)
    stage = record.get("stage")
    task_id = (state.get("current_task") or {})["id"]
    contract_hash = (state.get("goal_contract") or {}).get("hash")
    revision = record["source_revision"]
    if kind == "tests_failed" and stage in REVIEW_STAGES:
        if (
            builder_policy.enabled(state)
            and ((state.get("builder_retries") or {}).get(builder_policy.key(state)) or {}).get("action") == "pause"
        ):
            return resolve_repair_job.intercept_stall(
                state,
                "PAUSED_BUILDER_RETRY_LIMIT",
                str(error),
                before_investigator=True,
            )
        history = state.get("resolve_repair_history") or []
        earlier = [
            row
            for row in history
            if isinstance(row, dict)
            and row.get("kind") == "triage_tests_failed"
            and row.get("stage") == stage
            and row.get("task_id") == task_id
            and row.get("contract_hash") == contract_hash
        ]
        if len(earlier) >= ROUNDS:
            return False
        state.setdefault("resolve_repair_history", []).append(
            {
                "kind": "triage_tests_failed",
                "stage": stage,
                "task_id": task_id,
                "contract_hash": contract_hash,
                "source_revision": revision,
                "error": str(error),
                "requested_at": util.now(),
                "runner_owned": True,
            }
        )
        state.pop("pending_report_repair", None)
        state["iteration"] = int(state.get("iteration") or 0) + 1
        run_status.running(
            state,
            "EXECUTING",
            next_stage="builder",
            next_action=(
                f"Verification check failed during {stage}: {error}\n"
                "Fix the failing behavior and run the verification commands from the repository root before reporting."
            ),
        )
        return True
    if kind == "missing_execution" and stage in EXECUTION_STAGES:
        return resolve_repair_job.queue_missing_execution(state, record, error)
    return False
