"""Route one reviewer, Resolver or Builder decision for the controller (#2753).

Autopilot's ``_apply_result`` interprets every unit result. The larger branches it
takes for one decision live here: a report that asks the user, a Validator REWORK,
a progressive slice checkpoint and a Resolver assignment that routes a Builder
failure. Each one changes run state in the order the controller did before; the
controller still saves the record and decides whether to return. Callables owned
by Autopilot (``queue_resolution``, ``assert_within_assignment``, the Builder
failure route) are passed in, so this module never imports the controller.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_acceptance_test_repair as acceptance_test_repair
    from . import autocode_acceptance_tests as acceptance_tests
    from . import autocode_builder_failure as builder_failure
    from . import autocode_builder_policy as builder_policy
    from . import autocode_findings as findings_ledger
    from . import autocode_goal_lifecycle as lifecycle
    from . import autocode_names as names
    from . import autocode_progressive_state as progressive_state
    from . import autocode_resolver_recovery as resolver_recovery
    from . import autocode_rework_policy as rework_policy
    from . import autocode_roles as roles
    from . import autocode_run_status as run_status
    from . import autocode_source_scope as source_scope
    from . import autocode_util as util
    from . import autocode_verification_config as verification_config
except ImportError:
    import autocode_acceptance_test_repair as acceptance_test_repair
    import autocode_acceptance_tests as acceptance_tests
    import autocode_builder_failure as builder_failure
    import autocode_builder_policy as builder_policy
    import autocode_findings as findings_ledger
    import autocode_goal_lifecycle as lifecycle
    import autocode_names as names
    import autocode_progressive_state as progressive_state
    import autocode_resolver_recovery as resolver_recovery
    import autocode_rework_policy as rework_policy
    import autocode_roles as roles
    import autocode_run_status as run_status
    import autocode_source_scope as source_scope
    import autocode_util as util
    import autocode_verification_config as verification_config


def user_request(
    runtime, state: RunState, stage, value, record, workspace, *, queue_resolution, assert_within_assignment
):
    """Route a report that asks the user something; True when the controller should save and stop.

    False means the request was recorded as the agent's and the shared report handling continues.
    """
    support, goals, workflow = runtime.support, runtime.goals, runtime.workflow
    request = value["user_request"]
    origin = {
        "stage": stage,
        **{key: record[key] for key in ("output", "source_revision", "task_id") if key in record},
    }
    if workflow.final_only(state) and not names.task_planner_stage(stage):
        if stage == "builder":
            assert_within_assignment(state, record)
        if request["kind"] in ("blocker", "clarification"):
            queue_resolution(state, value, record, source_stage=stage, source_report=True)
        else:
            lifecycle.wait_for_user(
                state, request, origin=origin, evidence={"provenance": "source_report_not_accepted_review"}
            )
        return True
    if names.task_planner_stage(stage):
        if value["status"] != "BLOCKED":
            raise ValueError(
                f"The {roles.screen_name(stage, state)} must choose BLOCKED when requesting a user decision"
            )
        # Record the defects already identified, then pause. The controller's early return
        # never reaches the normal review path.
        if stage != "resolve":
            definitions = support.criteria_definition(value["acceptance_criteria"])
            if len({row["id"] for row in definitions}) != len(
                definitions
            ) or definitions != support.criteria_definition(state["acceptance_criteria"]):
                raise support.Paused("PAUSED_CRITERIA_CHANGE", "A blocked decision cannot change approved criteria")
            findings_ledger.record_decision(state, value, record)
        if stage != "resolve" and request["kind"] in ("blocker", "clarification"):
            queue_resolution(state, value, record, source_stage=stage)
        else:
            evidence = {}
            if stage == "resolve":
                evidence = {
                    "diagnosis": value["diagnosis"],
                    "output": record["output"],
                    "hashes": dict(cast(dict, state["resolution_request"])["evidence_hashes"]),
                }
                evidence["hashes"][record["output"]] = support.file_hash(Path(record["output"]))
                resolver_recovery.retain_diagnosis(state, record)
            lifecycle.wait_for_user(state, request, origin=origin, evidence=evidence, next_stage="review_completion")
            if stage == "resolve" and verification_config.settle(state, at=util.now()) == "corrected":
                # A verification-command correction is AutoCode's to settle, not a person's: the
                # explicit commands are dropped and the Tester proves the change at once.
                run_status.running(state, "EXECUTING", next_stage=state["next_stage"])
        goals.record_decision(state, value)
        state.pop("agent_request", None)
        return True
    state["agent_request"] = {"role": stage, "request": copy.deepcopy(value["user_request"])}
    if stage == "builder":
        state.update(
            {
                "implementation": {
                    **value,
                    "source_revision": record.get("source_revision"),
                    "workspace": str(workspace),
                },
                "changed_files": record.get("changed_files", []),
                "source_snapshot": record.get("after_ref"),
                "diff_ref": record.get("diff_ref"),
                "next_stage": "review_completion",
            }
        )
        return True
    return False


def validate_decision(state: RunState, stage: str, value: dict) -> None:
    """Validate optional protected acceptance-test repairs on modern ``review_completion`` / ``resolve`` decisions."""
    if stage in acceptance_test_repair.DECISION_STAGES:
        acceptance_test_repair.validate_report(
            value,
            stage=stage,
            covered_case_ids=acceptance_tests.covered(state),
            survivor_case_ids=acceptance_test_repair.survivor_case_ids(state),
        )


def prepare_continuation(runtime, state: RunState, stage: str, value: dict, record: dict, workspace) -> None:
    """Preflight a ``resolve`` proposal and bind any ``acceptance_test_repairs`` before assignment."""
    if stage == "resolve":
        # Reject invalid proposals before charging for failure diagnosis.
        # Milestone progress gates retain their exhaustion ordering.
        proposed = source_scope.snapshot(workspace, state, base_snapshot=runtime.support.snapshot)
        candidate = copy.deepcopy(state)
        spec = value.get("next_task")
        if isinstance(spec, dict):
            findings_ledger.assign(candidate, {"id": "proposal-preflight"}, spec, value)
        try:
            lifecycle.assign_task(candidate, value, proposed)
        except runtime.support.Paused as error:
            if not error.status.startswith("PAUSED_MILESTONE_"):
                raise
    if stage in acceptance_test_repair.DECISION_STAGES:
        acceptance_test_repair.record_decision(state, value, record, covered_case_ids=acceptance_tests.covered(state))


def review_rework(runtime, state: RunState, value, record, run_dir, *, queue_resolution):
    """Route the Validator's REWORK; the review stays authoritative whether its correction is assigned or diagnosed."""
    diagnosed = resolver_recovery.diagnosis_current(
        state, state.get("resolution_request") or {}, record.get("source_revision")
    )
    if not diagnosed:
        rework_policy.guard_pending_human(state)
    acceptance_test_repair.record_decision(state, value, record, covered_case_ids=acceptance_tests.covered(state))
    findings_ledger.record_decision(state, value, record)
    if diagnosed or (runtime.milestones.progress(state) or {}).get("builder_reassessment"):
        queue_resolution(state, value, record)
    elif getattr(runtime, "native_calls", None) is not None:
        # Native correction keeps the existing independent Resolver and its
        # genuine recovery/retry gates; no direct-assignment shortcut.
        queue_resolution(state, value, record)
    elif not rework_policy.route(runtime, state, value, record, queue_resolution, builder_policy, run_dir=run_dir):
        resolver_recovery.route_known_change(
            runtime, state, value, record, run_dir=run_dir, retry_policy=builder_policy
        )


def progressive_checkpoint(runtime, state: RunState, value, record, workspace):
    """Record a progressive slice checkpoint the Validator chose, after checking it claims nothing unproven."""
    if value["status"] != "CONTINUE" or not progressive_state.enabled(state):
        raise ValueError("progressive checkpoint requires explicit CONTINUE within an approved progressive run")
    if value["next_task"]["kind"] != "none":
        raise ValueError("slice checkpoint cannot manufacture a next assignment before independent slice review")
    proven = {
        row["id"] for row in (state.get("validation") or {}).get("criterion_results", []) if row["status"] == "PASS"
    }
    if any(row["status"] == "verified" and row["id"] not in proven for row in value["acceptance_criteria"]):
        raise ValueError(
            "slice checkpoint cannot claim verified original criteria without the Validator's current product proof"
        )
    progressive_state.checkpoint(
        state,
        source_scope.snapshot(workspace, state, base_snapshot=runtime.support.snapshot),
        record,
        product_findings=findings_ledger.blocking_entries(state),
    )


def resolver_builder_failure(runtime, state: RunState, value, record, workspace, *, route):
    """Route the Builder failure behind a Resolver's implement assignment; returns the route's action.

    The failure's evidence carries the Validator's checks, verified again, when that validation judged the
    same source revision, task and contract the failing Builder round produced.
    """
    support = runtime.support
    request = cast(dict, state["resolution_request"])
    source_output = request.get("source_output", request.get("review_output"))
    original: dict = next((row for row in reversed(state.get("stages", [])) if row.get("output") == source_output), {})
    evidence = builder_failure.evidence(state, original)
    evidence["diagnosis"] = request.get("failure_diagnosis")
    validation = state.get("validation") or {}
    if (
        validation.get("source_revision") == original.get("source_revision")
        and validation.get("task_id") == cast(dict, state.get("current_task", {})).get("id")
        and validation.get("contract_hash") == cast(dict, state["goal_contract"])["hash"]
    ):
        accepted = next(
            (
                row
                for row in state.get("stages", [])
                if row.get("output") == validation.get("output")
                and row.get("stage") == "tester"
                and not row.get("rejected")
            ),
            None,
        )
        if accepted:
            checked = copy.deepcopy(validation.get("checks", []))
            support.verify_checks(checked, workspace, accepted["events"], **runtime.check_evidence_options(accepted))
            evidence["checks"] = builder_failure.check_facts(checked, accepted, workspace, read_events=support.events)
            evidence["checks_verified"] = True
    action = route(state, evidence, value["diagnosis"], completed_record=record)
    pending = state.get("pending_builder_failure") or state.get("stuck_investigation")
    if action == "investigate" and pending:
        pending["resolver_continuation"] = {"value": copy.deepcopy(value), "record": copy.deepcopy(record)}
    return action
