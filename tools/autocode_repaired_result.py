"""Apply a repaired report transaction while retaining the original command owner.

The CLI supplies its existing report, rejection and commit services. This helper
owns only repaired-result acceptance and imports no controller or cyclic policy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

from copy import deepcopy
from pathlib import Path

try:
    from . import autocode_draft_assignment as draft_assignment
    from . import autocode_report_repair_route as report_repair_route
    from . import autocode_requirements_observations as requirements_observations
    from . import autocode_result_application as result_application
    from . import autocode_source_scope as source_scope
except ImportError:
    import autocode_draft_assignment as draft_assignment
    import autocode_report_repair_route as report_repair_route
    import autocode_requirements_observations as requirements_observations
    import autocode_result_application as result_application
    import autocode_source_scope as source_scope


class RevalidationRejected(ValueError):
    """The saved report failed a current validation gate; no candidate was committed."""


def accept(
    runtime, owner, run_dir, workspace, value, repair_record, *, revalidate=False, resolver_request=None, on_accept=None
):
    """Prepare the speculative repair under its real owner's durable checkpoint."""

    # A repair whose job rewrites one of its own pinned inputs (issue #968)
    # invalidates the pre-repair hash and deadlocks every retry as stale. Re-pin
    # exactly the repair's written paths that were already pinned before any
    # validation compares or persists them. The schema is an input the repair
    # never writes, so its pin keeps guarding external changes. All hashes are
    # computed first and a claimed-written pinned file must exist, so a failure
    # leaves every pin untouched in memory and in the saved state.
    owner_pending = resolver_request["pending_report_repair"] if resolver_request else owner["pending_report_repair"]
    pinned = owner_pending["pins"]
    refreshed = {}
    for key in () if revalidate else ("output", "response_text", "events", "before_ref", "after_ref"):
        path = repair_record.get(key)
        if path and path in pinned:
            if not Path(path).is_file():
                raise runtime.support.Paused(
                    "PAUSED_STALE_VALIDATION",
                    "A repaired artifact is missing from disk; the completed repair cannot be accepted",
                )
            refreshed[path] = runtime.support.file_hash(path)
    pinned.update(refreshed)

    def apply(state: RunState):
        if resolver_request is not None:
            # Keep the actual Resolver owner durable while validation commands
            # run. Only the speculative candidate returns to the original stage.
            state.pop("resolve_repair_request")
            report_repair_route.release(state, "repair_resolver")
            state["pending_report_repair"] = deepcopy(owner_pending)
            state["next_stage"] = owner_pending["original"]["stage"]
            state.setdefault("resolve_repair_history", []).append(
                {
                    "kind": "report_revalidated_before_dispatch",
                    "stage": state["next_stage"],
                    "original_output": owner_pending["original"]["output"],
                    "source_revision": owner_pending["original"]["source_revision"],
                    "requested_at": resolver_request.get("requested_at"),
                    "reason": "Current validation accepted the pinned report before Resolver launched",
                    "at": runtime.now(),
                }
            )
        pending = state["pending_report_repair"]
        original = deepcopy(pending["original"])
        report_value = value
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
            if revalidate:
                # Identity adoption is allowed only for the exact admitted record. The
                # temporary output is used only on this candidate; no provider is active.
                previous_active = state.get("active_stage")
                state["active_stage"] = repair_record
                try:
                    try:
                        report_value = runtime.load_stage_report(
                            repair_record,
                            workspace,
                            original if repair_record.get("report_only") else None,
                            state=state,
                        )
                    except runtime.support.Paused:
                        raise
                    except RuntimeError as error:
                        raise RevalidationRejected(str(error)) from error
                finally:
                    if previous_active is None:
                        state.pop("active_stage", None)
                    else:
                        state["active_stage"] = previous_active
            # History comes from the original report, not from the repair's draft of it.
            if runtime.restore_builder_history(original, report_value):
                runtime.write_json(Path(repair_record["output"]), report_value)
            output_hash = runtime.support.file_hash(repair_record["output"])
            # Restoring history rewrites the repair's output after the refresh
            # above; keep a pinned output on the file's final bytes (#968).
            if repair_record["output"] in pending["pins"]:
                pending["pins"][repair_record["output"]] = output_hash
                owner["pending_report_repair"]["pins"][repair_record["output"]] = output_hash
            runtime.assert_repair_preserves_builder_history(original, report_value)
            # Only unchanged dispositions from the pinned fresh review may survive.
            original.update(
                runtime.retained_dispositions(
                    original,
                    runtime.original_report_for_repair(original)["report"]
                    if runtime.stage_completed(state, original)
                    else None,
                    report_value,
                )
            )
            original.update(
                output=repair_record["output"],
                rejected=False,
            )
            if revalidate and not repair_record.get("report_only"):
                original["report_revalidated"] = True
            else:
                original.update(repaired_by=repair_record["events"], report_repaired=True)
            runtime.apply_result(state, original["stage"], report_value, original, workspace, run_dir)
            draft_assignment.retain_review_allowance(state, previous_planning, original)
            if revalidate and (
                source_scope.snapshot(workspace, owner, base_snapshot=runtime.support.snapshot)["revision"]
                != original["source_revision"]
                or (owner.get("goal_contract") or {}).get("hash") != pending["contract_hash"]
                or any(not Path(p).is_file() or runtime.support.file_hash(p) != h for p, h in pending["pins"].items())
            ):
                raise runtime.support.Paused("PAUSED_STALE_VALIDATION", "Report evidence changed during revalidation")
        except (ValueError, KeyError, runtime.support.Paused) as error:
            if revalidate:
                if isinstance(error, runtime.support.Paused):
                    raise
                raise RevalidationRejected(str(error)) from error
            # Rejection is authoritative. Uncertain command ownership propagates
            # before archival; ordinary report rejection updates the real owner.
            runtime.reject_completed_stage(owner, run_dir, repair_record, error)
        # One charge per provider call; preserve any outer repair restored by apply_result.
        if revalidate:
            # The rejected row is the same paid provider call, not a new one.
            # Its old record stays in the repair history below.
            matches = [
                index
                for index, row in enumerate(state["stages"][:-1])
                if row.get("events") == repair_record.get("events") and row.get("rejected")
            ]
            if len(matches) != 1:
                raise runtime.support.Paused("PAUSED_STALE_VALIDATION", "Cannot identify the exact rejected report")
            del state["stages"][matches[0]]
            repair_record.update(rejected=False, report_revalidated=True)
        repair_record["applied_original_events"] = original["events"]
        state["stages"][-1] = repair_record
        state["history"][-1] = repair_record
        state.setdefault("report_repair_history", []).append(
            {
                "original_output": pending["original"]["output"],
                "repair": repair_record,
                "output_hash": output_hash,
                "rejections": list(pending.get("rejections") or filter(None, [pending.get("original_error")])),
                "attempts": pending["attempts"],
                "result": "revalidated" if revalidate else "accepted",
                "at": runtime.now(),
            }
        )
        requirements_observations.seal_repaired(state, repair_record)
        if state.get("pending_report_repair") == pending:
            state.pop("pending_report_repair")
        if state["status"] == "RUNNING":
            state["phase"] = "PLANNING" if runtime.planning.is_planning(state, state["next_stage"]) else "EXECUTING"
            state.pop("stop_reason", None)
        if on_accept is not None:
            on_accept(state)

    original_stage = owner_pending["original"]["stage"]
    candidate = result_application.prepare(owner, run_dir, original_stage, apply, persist=runtime.write_json)
    planning_record = repair_record if original_stage == "plan_finalize" else None
    runtime.commit_boundary_candidate(owner, candidate, run_dir, workspace, planning_record=planning_record)
