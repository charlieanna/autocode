"""Apply a repaired report transaction while retaining the original command owner.

The CLI supplies its existing report, rejection and commit services. This helper
owns only repaired-result acceptance and imports no controller or cyclic policy.
"""

from copy import deepcopy
from pathlib import Path

try:
    from . import autocode_draft_assignment as draft_assignment
    from . import autocode_result_application as result_application
    from . import autocode_source_scope as source_scope
except ImportError:
    import autocode_draft_assignment as draft_assignment
    import autocode_result_application as result_application
    import autocode_source_scope as source_scope


def accept(runtime, owner, run_dir, workspace, value, repair_record):
    """Prepare the speculative repair under its real owner's durable checkpoint."""

    def apply(state):
        pending = state["pending_report_repair"]
        original = deepcopy(pending["original"])
        previous_planning = deepcopy(state.get("planning") or {})
        current = source_scope.snapshot(workspace, state, base_snapshot=runtime.support.snapshot)
        if (
            current["revision"] != original["source_revision"]
            or (state.get("goal_contract") or {}).get("hash") != pending["contract_hash"]
            or any(not Path(p).is_file() or runtime.support.file_hash(p) != h for p, h in pending["pins"].items())
        ):
            raise runtime.support.Paused(
                "PAUSED_STALE_VALIDATION", "Original report evidence or goal changed during repair"
            )
        # Evidence checks use ORIGINAL tool events, not new commands from the repairer.
        try:
            # History comes from the original report, not from the repair's draft of it.
            if runtime.restore_builder_history(original, value):
                runtime.write_json(Path(repair_record["output"]), value)
            output_hash = runtime.support.file_hash(repair_record["output"])
            runtime.assert_repair_preserves_builder_history(original, value)
            # Only unchanged dispositions from the pinned fresh review may survive.
            original.update(
                runtime.retained_dispositions(
                    original,
                    runtime.original_report_for_repair(original)["report"]
                    if runtime.stage_completed(state, original)
                    else None,
                    value,
                )
            )
            original.update(
                output=repair_record["output"],
                repaired_by=repair_record["events"],
                rejected=False,
                report_repaired=True,
            )
            runtime.apply_result(state, original["stage"], value, original, workspace, run_dir)
            draft_assignment.retain_review_allowance(state, previous_planning, original)
        except (ValueError, KeyError, runtime.support.Paused) as error:
            # Rejection is authoritative. Uncertain command ownership propagates
            # before archival; ordinary report rejection updates the real owner.
            runtime.reject_completed_stage(owner, run_dir, repair_record, error)
        # One charge per provider call; preserve any outer repair restored by apply_result.
        repair_record["applied_original_events"] = original["events"]
        state["stages"][-1] = repair_record
        state["history"][-1] = repair_record
        state.setdefault("report_repair_history", []).append(
            {
                "original_output": pending["original"]["output"],
                "repair": repair_record,
                "output_hash": output_hash,
                "attempts": pending["attempts"],
                "result": "accepted",
                "at": runtime.now(),
            }
        )
        if state.get("pending_report_repair") == pending:
            state.pop("pending_report_repair")
        if state["status"] == "RUNNING":
            state["phase"] = "PLANNING" if runtime.planning.is_planning(state, state["next_stage"]) else "EXECUTING"
            state.pop("stop_reason", None)

    candidate = result_application.prepare(
        owner, run_dir, owner["pending_report_repair"]["original"]["stage"], apply, persist=runtime.write_json
    )
    runtime.commit_boundary_candidate(owner, candidate, run_dir, workspace)
