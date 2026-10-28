#!/usr/bin/env python3
"""A durable plan-review → build → validate → completion loop.

The completion owner requests completion; the runner enforces goal and evidence gates.
The Builder is the only designated writer; review roles are checked for source drift.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_planning_recovery_policy as planning_recovery_policy
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_planning_recovery_policy as planning_recovery_policy
    import autocode_prompts as prompts


import copy
import json
import os
import subprocess
import sys
import time
import uuid
from contextlib import ExitStack
from pathlib import Path
from typing import Any, cast

try:
    from . import autocode_action_resolver as action_resolver
    from . import autocode_controller_owner as controller_owner  # noqa: F401 - runner compatibility export
    from . import autocode_interaction_timing as interaction_timing
    from . import autocode_provider_env as provider_env
    from . import autocode_route_advisory as route_advisory
    from .autocode_model_inventory import InvocationInventory
except ImportError:
    import autocode_action_resolver as action_resolver
    import autocode_controller_owner as controller_owner  # noqa: F401 - runner compatibility export
    import autocode_interaction_timing as interaction_timing
    import autocode_provider_env as provider_env
    import autocode_route_advisory as route_advisory
    from autocode_model_inventory import InvocationInventory  # noqa: F401 - compatibility API supplied to invocation helpers

# isort: off
# fmt: off
try:
    from . import autocode_native_image as native_image, autocode_native_invocation as native_invocation
except ImportError:
    import autocode_native_image as native_image
    import autocode_native_invocation as native_invocation
try:
    from . import autocode_support as support, autocode_completion as completion_gate, autocode_goals as goals, autocode_goal_lifecycle as lifecycle, autocode_interventions as interventions, autocode_providers, autocode_opencode as opencode, autocode_process as processes, autocode_registry as registry, autocode_planning as planning, autocode_escalation as escalation, autocode_failures as failures, autocode_jobs as jobs  # noqa: F401 - compatibility API
    from . import autocode_classification_recovery as classification_recovery
    from . import autocode_network_retry as network_retry
    from . import autocode_investigation_progress as investigation_progress
    from . import autocode_launch_inputs as launch_inputs, autocode_parent_boundary as parent_boundary  # noqa: F401 - compatibility API
    from . import autocode_regression as regression, autocode_checkout_lock as checkout_lock, autocode_format_correction as format_correction, autocode_cmd_only_report as cmd_only, autocode_check_refs as check_refs, autocode_planning_metadata as planning_metadata, autocode_preflight_attempts as preflight_attempts, model_catalogue, autocode_provider_launch as provider_launch, autocode_verification_plan as verification_plan, autocode_task_preflight as task_preflight, autocode_resolver_recovery as resolver_recovery, autocode_visual_runtime as visual_runtime, autocode_visual_profile as visual_profile  # noqa: F401 - compatibility API
    from . import autocode_dependency as dependency, autocode_status_command as status_command, autocode_json_output as json_output, autocode_follow_up as follow_up, autocode_util as util, autocode_stray_writes as stray_writes, autocode_verbose as verbose, autocode_status, autocode_artifacts as artifacts, autocode_report_repair_context as report_repair_context, autocode_receipt_repair_context as receipt_repair_context, autocode_stuck_repair_context as stuck_repair_context  # noqa: F401 - compatibility API
    from . import autocode_stop as stop_policy, autocode_status as status_records, autocode_readonly_events as readonly_events  # noqa: F401 - compatibility API
    from . import autocode_run_status as run_status, autocode_report_repair_queue as report_repair_queue, autocode_rewording_question as rewording, autocode_light_builder as light_builder, autocode_route_fit as route_fit, autocode_report_identity as report_identity
    from . import autocode_source_scope as source_scope, autocode_source_diff as source_diff, autocode_source_snapshot as source_snapshot  # noqa: F401 - compatibility API
    from . import autocode_supervision as supervision, autocode_supervision_cli as supervision_cli, autocode_supervision_recovery as supervision_recovery, autocode_result_application as result_application, autocode_repaired_result as repaired_result, autocode_detached_output as detached_output  # noqa: F401 - compatibility API
    from . import autocode_run_view as run_view, autocode_workflows as workflows, autocode_agent_env as agent_env, autocode_worktrees as worktrees, autocode_event_log as event_log, autocode_rework_policy as rework_policy, autocode_approval_provenance as approval_provenance  # noqa: F401 - compatibility API
except ImportError:
    import autocode_classification_recovery as classification_recovery
    import autocode_network_retry as network_retry
    import autocode_launch_inputs as launch_inputs  # noqa: F401 - compatibility API
    import autocode_parent_boundary as parent_boundary  # noqa: F401 - compatibility API
    import autocode_source_scope as source_scope
    import autocode_source_diff as source_diff
    import autocode_source_snapshot as source_snapshot  # noqa: F401 - compatibility API
    import autocode_verbose as verbose
    import autocode_status
    import autocode_artifacts as artifacts
    import autocode_report_repair_context as report_repair_context
    import autocode_receipt_repair_context as receipt_repair_context
    import autocode_stuck_repair_context as stuck_repair_context  # noqa: F401 - compatibility API
    import autocode_regression as regression
    import autocode_format_correction as format_correction
    import autocode_cmd_only_report as cmd_only
    import autocode_check_refs as check_refs
    import autocode_support as support
    import autocode_completion as completion_gate
    import autocode_jobs as jobs
    import autocode_investigation_progress as investigation_progress
    import autocode_agent_env as agent_env
    import autocode_worktrees as worktrees  # noqa: F401 - compatibility API
    import autocode_util as util
    import autocode_stray_writes as stray_writes
    import autocode_event_log as event_log  # noqa: F401 - compatibility API
    import autocode_goals as goals
    import autocode_goal_lifecycle as lifecycle
    import autocode_interventions as interventions
    import autocode_checkout_lock as checkout_lock  # noqa: F401 - compatibility API
    import autocode_providers  # noqa: F401 - compatibility API
    import autocode_opencode as opencode
    import autocode_provider_launch as provider_launch
    import autocode_verification_plan as verification_plan
    import autocode_task_preflight as task_preflight  # noqa: F401 - compatibility API
    import autocode_preflight_attempts as preflight_attempts
    import autocode_stop as stop_policy
    import autocode_run_status as run_status
    import autocode_report_repair_queue as report_repair_queue
    import autocode_rewording_question as rewording
    import autocode_light_builder as light_builder
    import autocode_route_fit as route_fit
    import autocode_report_identity as report_identity
    import autocode_status as status_records
    import autocode_readonly_events as readonly_events  # noqa: F401 - compatibility API
    import autocode_supervision as supervision
    import autocode_supervision_cli as supervision_cli  # noqa: F401 - compatibility API supplied to invocation helpers
    import autocode_supervision_recovery as supervision_recovery
    import autocode_result_application as result_application
    import autocode_repaired_result as repaired_result
    import autocode_detached_output as detached_output  # noqa: F401 - compatibility API
    import autocode_process as processes
    import autocode_registry as registry  # noqa: F401 - compatibility API supplied to invocation helpers
    import autocode_planning as planning
    import autocode_rework_policy as rework_policy  # noqa: F401 - compatibility API
    import autocode_escalation as escalation
    import autocode_failures as failures
    import autocode_planning_metadata as planning_metadata
    import autocode_pause_command as pause_command
    import autocode_resolver_recovery as resolver_recovery
    import autocode_visual_runtime as visual_runtime
    import autocode_visual_profile as visual_profile  # noqa: F401 - compatibility API
    import autocode_run_view as run_view  # noqa: F401 - compatibility API
    import autocode_approval_provenance as approval_provenance
    import autocode_workflows as workflows  # noqa: F401 - compatibility API
    import autocode_dependency as dependency  # noqa: F401 - compatibility API
    import autocode_status_command as status_command  # noqa: F401 - compatibility API
    import autocode_json_output as json_output
    import autocode_follow_up as follow_up  # noqa: F401 - compatibility API
    import model_catalogue  # noqa: F401 - compatibility API

try:
    from . import autocode_job_source as job_source, autocode_job_failure as job_failure, autocode_provider_auth as provider_auth, autocode_provider_refusal as provider_refusal  # noqa: F401 - compatibility API
    from . import autocode_stage_stops as stage_stops
    from . import autocode_workspaces as task_workspaces, autocode_figma as figma  # noqa: F401 - compatibility API
    from . import autopilot  # noqa: F401 - compatibility API
    from . import autocode_workflow as workflow  # noqa: F401 - compatibility API
    from . import autocode_milestones as milestones  # noqa: F401 - compatibility API
    from . import autocode_dispatch as dispatch  # noqa: F401 - compatibility API
    from . import autocode_resolver_runtime as resolver_runtime  # noqa: F401 - compatibility API
    from . import autocode_resolver_human as resolver_human  # noqa: F401 - compatibility API
    from . import autocode_reviewer_fallback as reviewer_fallback  # noqa: F401 - compatibility API
    from . import autocode_planning_artifacts as planning_artifacts  # noqa: F401 - compatibility API
    from . import autocode_pause_command as pause_command  # noqa: F401 - compatibility API supplied to invocation helpers
    from . import autocode_budget_recovery as budget_recovery, autocode_recovery_limits as recovery_limits, autocode_recovery_grants as recovery_grants  # noqa: F401 - compatibility API
    from . import autocode_recovery_accounting as recovery_accounting  # noqa: F401 - compatibility API
    from . import autocode_recovery_context as recovery_context  # noqa: F401 - compatibility API
    from . import autocode_progressive_state as progressive_state  # noqa: F401 - compatibility API
    from . import autocode_findings as findings_ledger  # noqa: F401 - compatibility API
    from . import autocode_route_ladder as route_ladder
    from . import autocode_configure, autocode_joint_transport as joint_transport, autocode_role_transport as role_transport, autocode_args as cli_args, autocode_run_actions as run_actions, autocode_build_loop as build_loop, autocode_run_setup as run_setup  # noqa: F401 - compatibility API
    from . import autocode_output_policy as output_policy, autocode_output_cap as output_cap  # noqa: F401 - compatibility API
    from .autocode_run_records import (PLANNING_STAGES, PROVENANCE_LISTS, account_stage, archive_rejected_stage, set_aside_at_completion_gate,  # noqa: F401 - compatibility API
        assert_stage_stopped, attempt_id, check_evidence_options, count_automatic_recovery, default_missing_provenance,
        normalize_human_boundary, normalize_plan_challenge_blocking, now, read_json, recovery_count,
        repair_limit, stage_completed, stage_supports_sessions, timeout_recovery_route, write_json as ordinary_write_json)  # noqa: F401 - compatibility API
    from .autocode_report_source import (REPAIR_INPUT_ROUTE, REPAIR_REPORT_BYTES, completed_provider_report, original_report_for_repair, recovered_timeout_attempt,  # noqa: F401 - compatibility API
        repair_report_instruction, repair_report_source, valid_truncated_report_attempt)  # noqa: F401 - compatibility API
    from .autocode_report_findings import REPAIR_INSTRUCTION as DISPOSITION_REPAIR_RULE, retained as retained_dispositions  # noqa: F401 - compatibility API
    from .autocode_stage_recovery import (MAX_AUTOMATIC_CAPACITY_RECOVERIES, abandon_stage,  # noqa: F401 - compatibility API
        authorize_failure_retry, automatically_recover_capacity_stage,
        automatically_recover_external_directory_denial, automatically_recover_report_repair_timeout,
        automatically_recover_timed_out_stage, automatically_recover_truncated_review,
        archive_stale_report_repair, prepare_abandoned_completion_revalidation, stale_report_repair,
        prepare_exhausted_execution_report_retry, prepare_planning_retry, reconcile_rate_limited_stage,
        recover_legacy_report_repair, retry_format_failed_report)  # noqa: F401 - compatibility API
    from .autocode_activity import ActivityMonitor, CHANGE_IDLE_LIMIT, JOB_IDLE_LIMIT  # noqa: F401 - compatibility API
    from . import autocode_idle_policy as idle_policy  # noqa: F401 - compatibility API
except ImportError:
    import autocode_job_source as job_source
    import autocode_job_failure as job_failure
    import autocode_provider_auth as provider_auth  # noqa: F401 - compatibility API
    import autocode_provider_refusal as provider_refusal  # noqa: F401 - compatibility API
    import autocode_stage_stops as stage_stops
    import autocode_workspaces as task_workspaces  # noqa: F401 - compatibility API
    import autocode_figma as figma  # noqa: F401 - compatibility API
    import autopilot  # noqa: F401 - compatibility API
    import autocode_workflow as workflow  # noqa: F401 - compatibility API
    import autocode_milestones as milestones  # noqa: F401 - compatibility API
    import autocode_dispatch as dispatch  # noqa: F401 - compatibility API
    import autocode_resolver_runtime as resolver_runtime  # noqa: F401 - compatibility API
    import autocode_resolver_human as resolver_human  # noqa: F401 - compatibility API
    import autocode_reviewer_fallback as reviewer_fallback  # noqa: F401 - compatibility API
    import autocode_planning_artifacts as planning_artifacts  # noqa: F401 - compatibility API
    import autocode_budget_recovery as budget_recovery
    import autocode_recovery_limits as recovery_limits
    import autocode_recovery_grants as recovery_grants  # noqa: F401 - compatibility API
    import autocode_recovery_accounting as recovery_accounting  # noqa: F401 - compatibility API
    import autocode_recovery_context as recovery_context  # noqa: F401 - compatibility API
    import autocode_progressive_state as progressive_state  # noqa: F401 - compatibility API
    import autocode_findings as findings_ledger  # noqa: F401 - compatibility API
    import autocode_route_ladder as route_ladder
    import autocode_configure
    import autocode_joint_transport as joint_transport
    import autocode_role_transport as role_transport
    import autocode_args as cli_args  # noqa: F401 - compatibility API supplied to invocation helpers
    import autocode_run_actions as run_actions
    import autocode_build_loop as build_loop  # noqa: F401 - compatibility API
    import autocode_run_setup as run_setup  # noqa: F401 - compatibility API
    import autocode_output_policy as output_policy
    import autocode_output_cap as output_cap  # noqa: F401 - compatibility API
    from autocode_run_records import (PLANNING_STAGES, PROVENANCE_LISTS, account_stage, archive_rejected_stage, set_aside_at_completion_gate,  # noqa: F401 - compatibility API
        assert_stage_stopped, attempt_id, check_evidence_options, count_automatic_recovery, default_missing_provenance,
        normalize_human_boundary, normalize_plan_challenge_blocking, now, read_json, recovery_count,
        repair_limit, stage_completed, stage_supports_sessions, timeout_recovery_route, write_json as ordinary_write_json)  # noqa: F401 - compatibility API
    from autocode_report_source import (REPAIR_INPUT_ROUTE, REPAIR_REPORT_BYTES, completed_provider_report, original_report_for_repair, recovered_timeout_attempt,  # noqa: F401 - compatibility API
        repair_report_instruction, repair_report_source, valid_truncated_report_attempt)  # noqa: F401 - compatibility API
    from autocode_report_findings import REPAIR_INSTRUCTION as DISPOSITION_REPAIR_RULE, retained as retained_dispositions  # noqa: F401 - compatibility API
    from autocode_stage_recovery import (MAX_AUTOMATIC_CAPACITY_RECOVERIES, abandon_stage,  # noqa: F401 - compatibility API
        authorize_failure_retry, automatically_recover_capacity_stage,
        automatically_recover_external_directory_denial, automatically_recover_report_repair_timeout,
        automatically_recover_timed_out_stage, automatically_recover_truncated_review,
        archive_stale_report_repair, prepare_abandoned_completion_revalidation, stale_report_repair,
        prepare_exhausted_execution_report_retry, prepare_planning_retry, reconcile_rate_limited_stage,
        recover_legacy_report_repair, retry_format_failed_report)  # noqa: F401 - compatibility API
    from autocode_activity import ActivityMonitor, CHANGE_IDLE_LIMIT, JOB_IDLE_LIMIT  # noqa: F401 - compatibility API
    import autocode_idle_policy as idle_policy  # noqa: F401 - compatibility API
# fmt: on
# isort: on
write_json = stop_policy.state_writer(ordinary_write_json, status_records.persist)
# Compatibility for integrations that imported the previous controller attribute.
orchestrator = autopilot

SCHEMA_DIR = Path(__file__).resolve().parent / "autocode-schemas"
# Run configuration lives in autocode_configure (which imports neither this
# module nor autopilot); the shared names stay resolvable here for argparse,
# the status command and the dashboard.
DEFAULT_ROLE_MODELS = autocode_configure.DEFAULT_ROLE_MODELS
DEFAULT_ENGINE = autocode_configure.DEFAULT_ENGINE
BUDGET_ARGUMENTS = autocode_configure.BUDGET_ARGUMENTS
check_subscription = autocode_configure.check_subscription
iteration_limit_reached = build_loop.iteration_limit_reached


def finish_human_action(state: RunState, published):
    """Consume the checked request after existing material-decision APIs succeed."""
    entry = state["resolver"]["human_escalations"][published["request_id"]]
    entry.update(
        status="consumed",
        response={
            "actor": "user_cli",
            "action": "material_decision",
            "at": now(),
            "request_id": published["request_id"],
            "request_token": published["request_token"],
            "event": copy.deepcopy((state.get("user_events") or [None])[-1]),
        },
    )
    state.pop(resolver_human.PUBLIC, None)
    if state.get(resolver_human.PRIVATE):
        return
    if state.get("status") == "WAITING_FOR_USER":
        if (state.get("user_request") or {}).get("kind") == "human_review":
            request = copy.deepcopy(cast(dict, state["user_request"]))
            request["criteria"] = goals.missing_human_reviews(state)
            lifecycle.wait_for_user(
                state, request, origin={"stage": "resolver_response"}, next_stage=state.get("next_stage")
            )
        elif state.get("pending_questions"):
            resolver_human.queue(
                state,
                "clarification",
                {"stage": "resolver_response"},
                questions=state["pending_questions"],
                phase=state.get("phase"),
                next_stage=state.get("next_stage"),
            )


def recover_default_budget(state: RunState, run_dir, workspace, kind):
    """AutoResolver may extend a proven internal default, never erase its usage."""
    with interventions.serialized(run_dir):
        if (
            state.get(resolver_human.PRIVATE)
            or resolver_human.current(state)
            or (Path(run_dir) / "pause-requested").exists()
            or interventions.pending(run_dir)
        ):
            return False
        latest = next((row for row in reversed(state.get("stages", [])) if not row.get("runner_owned")), None)
        if (
            not latest
            or latest.get("source_revision")
            != source_scope.snapshot(workspace, state, base_snapshot=support.snapshot)["revision"]
        ):
            return False
        candidate = copy.deepcopy(state)
        if not budget_recovery.recover(candidate, kind=kind, now=now()):
            return False
        extension = candidate["resolver"]["budget_extensions"][-1]
        output = extension["evidence"].get("output")
        if not output or not Path(output).is_file():
            return False
        if kind == "planning_review_call_limit":
            state["planning"]["review_call_limit"] = candidate["planning"]["review_call_limit"]
            state["planning"]["review_call_limit_origin"] = "runner_default"
        elif kind == "milestone_max_seconds":
            state["settings"]["milestone_checkpoints"]["max_seconds"] = extension["to"]
        else:
            state["settings"]["limits"][kind] = extension["to"]
        state.setdefault("resolver", {})["budget_extensions"] = candidate["resolver"]["budget_extensions"]
        receipt_id = resolver_runtime._operational_receipt(
            state,
            run_dir,
            "extend_default_budget",
            f"AutoResolver extended internal {kind} from {extension['from']} to {extension['to']} "
            "once after verified progress; usage and failure history are retained.",
            extension,
        )
        progressive_state.apply_recovery_limit(state, extension, receipt_id)
        write_json(Path(run_dir) / "state.json", state)
        return True


slug = util.slug


def load_stage_report(record, workspace=None, evidence_record=None, state: RunState | None = None):
    """Validate provider output, retaining raw bytes before hydrating review IDs."""
    readonly_events.assert_unchanged_review(record, workspace=workspace)
    if evidence_record:
        readonly_events.assert_unchanged_review(evidence_record, workspace=workspace)
    rework_policy.verify_existing(record)
    value = completed_provider_report(
        record,
        opencode_report=role_transport.report_reader(state, record, opencode, workspace),
        read_object=util.read_object,
        persist=write_json,
    )
    reported = copy.deepcopy(cmd_only.refuse(value, record.get("schema")))  # never a report, never run (#512)
    value = (
        planning.fill_trace_id(state, str(record.get("stage", "")).removesuffix("_report_repair"), value)
        if state
        else value
    )
    value = default_missing_provenance(
        normalize_plan_challenge_blocking(planning_metadata.normalize_planning_metadata(value, state, record), record),
        record,
    )
    evidence_record = evidence_record or record
    validation = value.get("validation", value)
    checks = validation.get("checks") if isinstance(validation, dict) else None
    if isinstance(checks, list) and (
        (record.get("stage") == "sol" and workspace is not None)
        or any(isinstance(check, dict) and None in (check.get("exit_code"), check.get("command")) for check in checks)
    ):
        if workspace is None:
            raise ValueError("Cannot derive check metadata without the validation workspace")
        support.verify_checks(checks, workspace, evidence_record["events"], **check_evidence_options(evidence_record))
    # A Tester's extra citation beside a verified check costs a repair; the raw report stays as .reported.json.
    check_refs.tidy_tester_citations(record, validation, workspace, (state or {}).get("settings"))
    value = report_identity.adopt(value, record, state)  # a mistyped echo on a bound launch is no repair (#2135)
    schema = support.review_validation_schema(read_json(Path(record["schema"])), state, record, value)
    value = support.normalize_loaded_report(value, schema, record)
    # finding_dispositions may be present in reports validated against schemas
    # saved before the field was introduced. Strip it before validation rather
    # than rejecting a correct report.
    if "finding_dispositions" not in schema.get("properties", {}) and "finding_dispositions" in value:
        stripped = {k: v for k, v in value.items() if k != "finding_dispositions"}
        support.validate_schema(stripped, schema)
    else:
        support.validate_schema(value, schema)
    value = support.hydrate_review_report(value, state, record)
    if value != reported:
        original = Path(record["output"]).with_suffix(".reported.json")
        if original.exists():
            if read_json(original) != reported:
                raise ValueError("Preserved raw report differs; reconcile before normalization")
        else:
            write_json(original, reported)
        record["reported_output"] = str(original)
        reported_checks = reported.get("validation", reported).get("checks") or []
        record["derived_check_metadata"] = support.derived_check_fields(
            checks or [], reported_checks, evidence_record.get("events")
        )
    if (record.get("engine") == "opencode" and record.get("output_mode") != "report_file") or value != reported:
        write_json(Path(record["output"]), value)
    return rework_policy.capture(record, value) if workspace is not None else value


ReportRepairQueued = report_repair_queue.ReportRepairQueued  # a finished request needs report-only repair


def reset_report_repair_for_resume(state: RunState):
    """Clear the bounded report-repair attempt count for an explicit resume.

    Mirrors resolver_runtime.reset_for_resume: the count is a current-cycle
    allowance an operator can renew after fixing the underlying cause, not a
    lifetime cap, but clearing it is recorded rather than silent, and the
    erased attempts fold into a lifetime total this function never resets.
    """
    pending = state.get("pending_report_repair")
    if not isinstance(pending, dict):
        return
    prior = pending.get("attempts", 0)
    pending["attempts"] = 0
    if not prior:
        return
    state["report_repair_lifetime_attempts"] = state.get("report_repair_lifetime_attempts", 0) + prior
    state.setdefault("user_events", []).append(
        {
            "kind": "report_repair_resume_epoch",
            "actor": "user_cli",
            "at": now(),
            "cleared_attempts": prior,
            "lifetime_attempts": state["report_repair_lifetime_attempts"],
        }
    )


REPAIR_HANDOFF_BYTES = 256 * 1024


def reject_completed_stage(state: RunState, run_dir, record, error):
    set_aside_at_completion_gate(state, run_dir, record, error, persist=write_json)  # a gate pause re-raises here
    result_application.raise_if_not_rejected(error)
    account_stage(state, record)
    error = stray_writes.undo(state, record, error)  # a read-only stage's writes are put back first
    failure = failures.record(state, record, error, now())
    originals = archive_rejected_stage(state, run_dir, record, error)
    asked = rewording.ask(state, record, error, resolver_human.queue)  # approved wording is the person's call (#1691)
    if asked or report_repair_queue.queue(state, record, error, completed=lambda: stage_completed(state, record)):
        if not asked:
            run_status.running(state, "REPORT_REPAIR")
        write_json(run_dir / "state.json", state)
        for artifact in originals:
            artifact.unlink(missing_ok=True)
        raise ReportRepairQueued()
    escalation.advance(state, record.get("route_role", record["role"]), trigger="rejected_output", detail=error)
    repeated = bool(failure and failures.stalled(failure))
    message = f"Completed {record['stage']} output was rejected ({error}); attempt archived. " + (
        "Consecutive attempts at this source failed with the same error; inspect the saved output probe and fix the cause before retrying."
        if repeated
        else "Resume explicitly with autocode resume to retry with a fresh request."
    )
    status = "PAUSED_REPEATED_FAILURE" if repeated else "PAUSED_INVALID_OUTPUT"
    run_status.pause(state, status, message, paused_at=now())
    write_json(run_dir / "state.json", state)
    for artifact in originals:
        artifact.unlink(missing_ok=True)
    raise support.Paused(status, message)


def run_role(
    *,
    role: str,
    prompt: str,
    sandbox: str,
    workspace: Path,
    run_dir: Path,
    state: RunState,
    schema: Path,
    model: str | None,
    allow_write: bool,
    dry_run: bool,
    report_only: bool = False,
    resume_session: str | None = None,
    retry_authorization=None,
    persist=None,
    native=None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    save = persist or write_json
    if native is not None:
        native.guard(state, state.get("next_stage"), report_only=report_only, allow_write=allow_write)
    if state.get("next_stage") == "astra_diagnose" and state.get("active_stage"):
        raise support.Paused("PAUSED_UNCERTAIN_STAGE", "Reconcile the active diagnosis before another provider request")
    if state.get("next_stage") == "sol" and visual_runtime.requested(state):
        visual_runtime.verify_reference(state)
    timeout_recovery_guard(state)
    if role in ("terra", "sol", "completion", "astra", "plan_reviewer", "glm"):
        dispatch.enforce_cross_model_verification(state)
    iteration = state["iteration"]
    original_stage = cast(str, state["next_stage"])
    stage = original_stage
    joint_stage = planning.is_planning(state, stage)
    if state.get("version", 2) >= 3 and stage not in ("astra_discovery", *jobs.STAGES) and not joint_stage:
        goals.execution_guard(state)
        if not report_only:
            native.dispatch_guard(
                state, stage, milestones.dispatch_guard
            ) if native is not None else milestones.dispatch_guard(state, stage)
    if (joint_stage or stage == "astra_discovery") and (
        role != planning.role_for(state, stage) or allow_write or sandbox != "read-only"
    ):
        raise support.Paused("PAUSED_DISCOVERY_WRITE", "Planning must use its assigned role read-only")
    if original_stage in planning.V2_STAGES and not progressive_state.revision_pending(state):
        try:
            planning_artifacts.verify_predecessor(state, original_stage, run_dir)
        except ValueError as error:
            raise support.Paused("PAUSED_INVALID_PREDECESSOR", str(error)) from error
    route_role = planning.route_for(state, original_stage, role)
    native_isolation = native.role_isolation(state, route_role) if native is not None else None
    if not dry_run:
        processes.preflight()  # fail before creating an active request
    if report_only:
        stage += "_report_repair"
    if stage == "astra_diagnose" and not dry_run:
        resolver_runtime.check_diagnostic_capacity(sys.modules[__name__], state, run_dir)
    attempt = 1 + sum(r.get("stage") == stage and r.get("iteration") == iteration for r in state.get("stages", []))
    base = artifacts.reserve(run_dir, iteration, stage, attempt)
    output, events, prompt_file = base.with_suffix(".json"), base.with_suffix(".jsonl"), base.with_suffix(".prompt.md")
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    if not report_only and original_stage in ("sol", "astra_review", "astra_checkpoint"):
        bound_schema = support.review_generation_schema(read_json(schema), state, original_stage)
        schema = base.with_suffix(".schema.json")
        save(schema, bound_schema)
    fallback_route = reviewer_fallback.pending_route(state, original_stage) if joint_stage and not report_only else None
    if fallback_route:
        route_role = fallback_route["role"]
    adapter, supports_sessions = role_transport.session_adapter(state, route_role, opencode, workspace)
    session = role_transport.resume_session(
        state, route_role, original_stage, joint_stage, report_only, supports_sessions, resume_session
    )
    configured_tool = getattr(adapter, "CONFIGURED", False)
    engine = planning.engine_for(state["settings"], route_role)
    transport_args = support.transport_arguments(state["settings"])
    route = fallback_route or state["settings"]["roles"][route_role]
    if fallback_route:
        model = route["model"]
    effort = light_builder.effort(state, original_stage, report_only, route.get("reasoning_effort"))  # #1710
    effort = route_fit.launch(state["settings"], route_role, effort, original_stage, model=route.get("model"))  # #2057
    limits = state["settings"].get("limits", {})
    stage_timeout = limits.get("stage_timeout_seconds")
    idle_timeout = limits.get("idle_timeout_seconds", 300)
    idle_limit, idle_origin = idle_policy.effective(
        idle_timeout,
        state["settings"].get("budget_origins", {}).get("idle_timeout_seconds"),
        model,
        stage=original_stage,
    )
    tool_timeout = limits.get("tool_timeout_seconds", 1800)
    native_before = (
        source_scope.snapshot(workspace, state, base_snapshot=support.snapshot) if native is not None else None
    )
    native_proof = (
        regression.complete(state, cast(dict[str, Any], native_before)["revision"])
        if native is not None and original_stage == "sol" and regression.required(state)
        else False
    )
    image_preparation = (
        native_image.NativeImagePreparation(
            native=native,
            isolation=native_isolation,
            state=state,
            workspace=workspace,
            run_dir=run_dir,
            base=base,
            snapshot=native_before,
            prompt=prompt,
            schema=schema,
            events=events,
            adapter=adapter,
            render_prompt=provider_launch.render_native_image_prompt,
            regression_proof_current=native_proof,
            regression_handoff=regression.handoff(state) if native_proof else None,
        )
        if native is not None and original_stage == "sol" and visual_runtime.requested(state)
        else None
    )
    native_environment = None
    native_capture = None
    if native is not None:
        native_environment = agent_env.scrubbed(os.environ)
        native_environment.update(output_policy.environment(state["settings"], workspace, events))
        if getattr(adapter, "OUTPUT", "opencode_events") == "report_file":
            native_capture = {
                "attempt": str(output),
                "nonce": uuid.uuid4().hex,
                "source_revision": cast(dict[str, Any], native_before)["revision"],
            }
            native_environment["AUTOCODE_CAPTURE_CONTEXT"] = json.dumps(native_capture)
    snapshot_env = readonly_events.launch_excludes(workspace, engine, adapter, native_environment, dry_run=dry_run)
    command, child_environment, overrides, worker_context = provider_launch.prepare(
        engine=engine,
        adapter=adapter,
        role=role,
        route_role=route_role,
        workspace=workspace,
        run_dir=run_dir,
        session=session,
        model=model,
        effort=effort,
        allow_write=allow_write,
        planning=joint_stage or report_only,
        report=output,
        schema=schema,
        prompt_file=prompt_file,
        sandbox=sandbox,
        transport_args=transport_args,
        chatgpt=planning.enabled(state),
        provider=route.get("provider"),
        enforce_tool_boundary=not dry_run,
        source_paths=source_scope.paths(state),
        settings=state["settings"],  # the launcher applies the saved #413 opt-out
        planning_report_recovery=report_only or planning_recovery_policy.active(state, original_stage),
        replay_context={
            "stage": stage,
            "iteration": iteration,
            "turn": len(state.get("turns") or []) + 1,
            "milestone_id": (state.get("current_task") or {}).get("milestone_id"),
            "task_id": (state.get("current_task") or {}).get("id"),
            "contract_hash": (state.get("goal_contract") or {}).get("hash"),
            "criteria_revision": state.get("criteria_revision"),
            "report_only": report_only,
            "original_stage": original_stage if report_only else None,
            "consumed": sum(isinstance(row.get("replay"), dict) for row in state.get("stages", [])),
        }
        if state["settings"].get("replay_recording")
        else None,
        tool_commands=(
            lambda: verification_plan.launch_commands(state, progressive_context=progressive_state.context(state))
        )
        if engine == "opencode" and not dry_run and not (joint_stage or report_only)
        else (),
        **({"native_image_preparation": image_preparation} if image_preparation is not None else {}),
        **(
            {"native_role_isolation": native_isolation, "native_environment": native_environment}
            if native is not None
            else {}
        ),
    )
    session = worker_context.get("provider_session", session)
    child_options = {"start_new_session": True, "env": child_environment}
    if image_preparation is not None:
        prompt = image_preparation.prompt
        save(base.with_suffix(".opencode.json"), overrides)
    elif engine == "opencode":
        prompt = adapter.prompt_for_schema(prompt, read_json(schema), events)
        current_proof = False
        if original_stage == "sol" and worker_context.get("tool_containment") and regression.required(state):
            current_proof = regression.complete(state, source_scope.snapshot(workspace, state)["revision"])
        prompt = provider_launch.containment_prompt(
            prompt,
            worker_context,
            stage=original_stage,
            regression_proof_current=current_proof,
            regression_handoff=regression.handoff(state) if current_proof else None,
        )
        if not configured_tool:
            save(base.with_suffix(".opencode.json"), overrides)
    if native is None:
        child_options["env"].update(output_policy.environment(state["settings"], workspace, events))
    if not dry_run:
        task_preflight.guard(state, workspace, run_dir, worker=worker_context, persist=save)
    visual_context = worker_context.get("visual_runtime") if image_preparation is not None else None
    if original_stage == "sol" and visual_runtime.requested(state) and not report_only and image_preparation is None:
        if engine != "opencode" or configured_tool:
            raise support.Paused(
                "PAUSED_VISUAL_EVIDENCE", "Verified visual delivery requires the supported native OpenCode route"
            )
        saved_transport = (
            state["settings"]
            .get("transport_identities", {})
            .get("opencode", state["settings"].get("transport_identity"))
        )
        if saved_transport is None or adapter.transport_drift(adapter.local_settings(workspace), saved_transport):
            raise support.Paused("PAUSED_VISUAL_EVIDENCE", "Visual transport changed since its approved profile")
        visual_context, command, child_environment, prompt = visual_runtime.prepare(
            state,
            original_stage,
            workspace,
            run_dir,
            base,
            command,
            child_environment,
            prompt,
            launch_authority=visual_profile.authority(state, worker_context, workspace, run_dir),
            current_snapshot=source_scope.snapshot(workspace, state, base_snapshot=support.snapshot),
        )
        worker_context["visual_runtime"] = visual_context
        child_options["env"] = child_environment
        worker_context.update(command=command, environment=child_environment)
        if visual_context and visual_context.get("status") == "READY" and engine == "opencode" and not configured_tool:
            save(base.with_suffix(".opencode.json"), json.loads(child_environment["OPENCODE_CONFIG_CONTENT"]))
    if report_only and len(prompt.encode("utf-8")) > REPAIR_HANDOFF_BYTES:
        raise support.Paused(
            "PAUSED_REPORT_REPAIR_INPUT",
            f"Provider-decorated repair prompt exceeds {REPAIR_HANDOFF_BYTES} bytes; no request was launched."
            + REPAIR_INPUT_ROUTE,
        )
    if image_preparation is not None and prompt != image_preparation.prompt:
        raise support.Paused("PAUSED_VISUAL_EVIDENCE", "Final native image prompt changed after isolation sealing")
    prompt_file.write_text(prompt)

    record = {
        "role": role,
        "stage": stage,
        "iteration": iteration,
        "started_at": now(),
        "command": command,
        "prompt": str(prompt_file),
        "events": str(events),
        "output": str(output),
        "schema": str(schema),
        "criteria_revision": state.get("criteria_revision"),
        "runner_calls": 1,
        "runner_retries": 0,
        "headroom_enabled": state["settings"].get("headroom", {}).get("enabled", False),
        "stage_timeout_seconds": stage_timeout,
        "idle_timeout_seconds": idle_timeout,
        "tool_timeout_seconds": tool_timeout,
        "expected_session": session,
        "supports_sessions": supports_sessions,
        "withheld_env": agent_env.withheld(os.environ),
    }
    if visual_context:
        record["visual_runtime"] = visual_context
    if route_role != role:
        record["route_role"] = route_role
    record["engine"] = engine
    record["reasoning_effort"] = effort
    record["launch_route"] = {key: route.get(key) for key in ("engine", "provider", "model", "reasoning_effort")}
    record["output_mode"] = getattr(adapter, "OUTPUT", "opencode_events") if engine == "opencode" else "codex_events"
    if report_only:
        record.update(report_only=True, original_stage=original_stage)
    if joint_stage:
        record["planning"] = True
    if engine == "opencode" and not configured_tool:
        record["permission_config"] = str(base.with_suffix(".opencode.json"))
    record.update(provider_launch.stage_record(worker_context))
    goal_contract = state.get("goal_contract")
    if goal_contract:
        record.update(contract_revision=goal_contract["revision"], contract_hash=goal_contract["hash"])
    current_task = state.get("current_task")
    if current_task:
        record["task_id"] = current_task["id"]
    if dry_run:
        record.update({"dry_run": True, "finished_at": now(), "exit_code": 0})
        return {"status": "DRY_RUN"}, record

    started = time.monotonic()
    timed_out, activity = False, None
    interrupted = False
    cleanup_error = None
    worker_path = run_dir / "active-processes.json"
    native_prepared = None
    on_root = None
    stdin_path = image_preparation.prompt_path if image_preparation is not None else prompt_file
    with (
        processes.interruption_handler(),
        stdin_path.open("r") as stdin,
        event_log.open_events(events) as stdout,
        ExitStack() as provider_guard,
    ):
        try:
            readonly_events.prepare_opencode_snapshots(
                workspace, record=record, env=child_options["env"], excludes=snapshot_env
            )
            before = native_before or source_scope.snapshot(workspace, state, base_snapshot=support.snapshot)
            job_source.prepare_capture_context(
                record, before, output, child_options, native_context=native_capture, protected=native is not None
            )
            save(base.with_suffix(".before.json"), before)
            record["before_ref"] = str(base.with_suffix(".before.json"))
            network_retry.bind_attempt(state, record, before, workspace)
            record["context"] = state.pop("pending_context_metrics", {})
            job_source.capture(workspace, base, record, before)
            with interventions.admission(run_dir):
                native_prepared = (
                    native.prepare_writer_launch(
                        state,
                        before,
                        record,
                        worker_context,
                        command,
                        child_options["env"],
                        native_isolation,
                        report_only=report_only,
                    )
                    if native is not None
                    else None
                )
                provider_launch.verify_containment(
                    worker_context,
                    **(
                        {
                            "state": state,
                            "native_role_isolation": native_isolation,
                            "command": command,
                            "environment": child_options["env"],
                        }
                        if native is not None
                        else {}
                    ),
                )
                if native is not None and native_prepared is None:
                    native_prepared = native.context.factory.prepare_launch(
                        state,
                        before,
                        record,
                        boundary=worker_context["tool_containment"],
                        command=command,
                        environment=child_options["env"],
                        native_role_isolation=native_isolation,
                    )
                if original_stage == "sol" and visual_runtime.requested(state):
                    visual_runtime.verify_reference(state)
                if visual_context and visual_context.get("status") == "READY":
                    visual_runtime.verify_prelaunch(
                        visual_context,
                        state,
                        run_dir=run_dir,
                        current_snapshot=before,
                        command=command,
                        env=child_environment,
                        **({"scope_authority": native.context.factory.scope_authority} if native is not None else {}),
                    )
                resolver_recovery.admit_dispatch(
                    state, record, workspace, run_dir, retry_authorization=retry_authorization
                )
                if joint_stage and not report_only:
                    planning.charge(state, original_stage, record=record, workspace=workspace)
                    if fallback_route:
                        admitted = reviewer_fallback.admit(state, run_dir, workspace, original_stage)
                        if admitted != fallback_route:
                            raise support.Paused(
                                "PAUSED_REVIEWER_FALLBACK", "Reviewer fallback changed before provider admission"
                            )
                        record["reviewer_fallback_grant"] = next(
                            grant["id"]
                            for grant in state["planning"]["reviewer_route_fallbacks"]
                            if grant.get("consumed") and grant["binding"]["selected_route"] == admitted
                        )
                resolver_runtime.charge_diagnostic_dispatch(sys.modules[__name__], state, run_dir, workspace, record)
                progressive_state.admit_attempt(state, record, before)
                job_failure.admit(state, record, workspace)
                classification_recovery.admit(state, record, workspace)
                state["active_stage"] = record
                save(run_dir / "state.json", state)
                child_stdin = (
                    subprocess.DEVNULL
                    if engine == "opencode" and configured_tool and getattr(adapter, "PROMPT_MODE", "stdin") == "file"
                    else stdin
                )

                def ownership_checkpoint(metadata):
                    # One writer before exec; status and reconciliation consume
                    # these birth identities and the independent receipt.
                    record.update(pid=metadata["provider"]["pid"], owner=metadata["owner"], supervision=metadata)
                    save(run_dir / "state.json", state)

                launch_options = dict(
                    cwd=workspace,
                    stdin=child_stdin,
                    stdout=stdout,
                    stderr=subprocess.STDOUT,
                    text=True,
                    **checkout_lock.child_options(workspace, child_options),
                )
                if native is not None:
                    child, on_root = provider_guard.enter_context(
                        native.context.held_launch(
                            native_prepared,
                            command=command,
                            environment=child_options["env"],
                            options=launch_options,
                            receipt_path=base.with_suffix(".supervision.json"),
                            timeout=stage_timeout or None,
                            checkpoint=ownership_checkpoint,
                        )
                    )
                else:
                    child = provider_guard.enter_context(
                        supervision.launch(
                            command,
                            receipt_path=base.with_suffix(".supervision.json"),
                            timeout=stage_timeout or None,
                            checkpoint=ownership_checkpoint,
                            **launch_options,
                        )
                    )
                if original_stage == "terra" and not report_only and interaction_timing.mark(state, "builder", now()):
                    save(run_dir / "state.json", state)
        except support.Paused:
            if native is None:
                job_source.discard_unadmitted(state, record)  # admission lost: no request or provider was started
            raise
        except processes.ProcessError as error:
            if native is None and isinstance(error, supervision.LaunchRefused):  # nothing ran (#2296)
                job_source.discard_refused(state, record, run_dir / "state.json")
            raise support.Paused("PAUSED_PROCESS_CLEANUP", str(error)) from error
        except KeyboardInterrupt as error:  # the stage's one interrupt; the launch stopped what it started
            if native is None:
                job_source.discard_unadmitted(state, record)
            if state.get("active_stage") is not record:
                raise  # before admission finished, nothing of this stage was saved
            why = f"Provider launch interrupted by {str(error) or 'a signal'}; inspect saved artifacts "
            raise support.Paused("PAUSED_INTERRUPTED", why + "before reconciliation") from error
        except BaseException:
            if native is None:
                job_source.discard_unadmitted(state, record)
            raise
        try:  # from here the stage's one interrupt is a pause with its provider collected (#454)
            print(
                f"{autocode_status.role_name(stage, state)}: started; model={model or 'default'}; log={events}",
                flush=True,
            )
            activity = ActivityMonitor(
                events,
                idle_seconds=idle_limit,
                tool_seconds=tool_timeout,
                reporter=verbose.reporter(autocode_status.role_name(stage, state), model),
                idle_origin=idle_origin,
                idle_hint=JOB_IDLE_LIMIT if stage in jobs.STAGES else CHANGE_IDLE_LIMIT,
                progress=(
                    investigation_progress.Budget(
                        limits.get("investigation_exploration_budget", investigation_progress.DEFAULT_BUDGET)
                    )
                    if stage == "investigate_bug" and not report_only
                    else None
                ),
            )
            activity_label, last_activity_print = None, 0

            def activity_checkpoint(snapshot):
                nonlocal activity_label, last_activity_print
                record["activity"] = {
                    **snapshot,
                    "observed_at": now(),
                    "elapsed_seconds": round(time.monotonic() - started, 1),
                    "stage_limit_seconds": stage_timeout,
                }
                save(run_dir / "state.json", state)
                label = (snapshot.get("activity"), snapshot.get("detail"))
                current = time.monotonic()
                if label != activity_label or current - last_activity_print >= 60:
                    stop = snapshot.get("timeout_reason") or (
                        snapshot.get("detail") if snapshot.get("activity") == "stalled" else None
                    )
                    print(
                        f"{autocode_status.role_name(stage, state)}: {snapshot.get('activity', 'waiting_for_provider')}; model={model or 'default'}; "
                        f"elapsed={record['activity']['elapsed_seconds']:g}s; "
                        f"idle={snapshot.get('idle_seconds', 0):g}s/{idle_limit or 'off'}; "
                        f"tool={snapshot.get('tool_elapsed_seconds', 0) or 0:g}s/{tool_timeout or 'off'}; "
                        f"stage_limit={stage_timeout or 'off'}" + (f"; {stop}" if stop else ""),
                        flush=True,
                    )
                    activity_label, last_activity_print = label, current

            def checkpoint(owned):
                record["processes"] = owned
                save(worker_path, {"run_dir": str(run_dir), "pid": child.pid, "processes": owned})
                save(run_dir / "state.json", state)

            exit_code, timed_out = processes.wait_for_stage(
                child,
                stage_timeout,
                checkpoint,
                activity=activity,
                activity_checkpoint=activity_checkpoint,
                startup_grace=min(5, tool_timeout or 5),
                **({"on_root": on_root} if native is not None else {}),
            )
        except KeyboardInterrupt:
            interrupted = True
            exit_code = child.poll()
        except processes.ProcessError as error:
            cleanup_error = str(error)
            timed_out = processes.retain_stage_timeout(error, activity) or timed_out
            exit_code = child.poll()
            record["processes"] = getattr(error, "processes", record.get("processes", []))
            save(
                worker_path,
                {
                    "run_dir": str(run_dir),
                    "pid": child.pid,
                    "processes": record.get("processes", []),
                    "cleanup_error": cleanup_error,
                },
            )
        else:
            worker_path.unlink(missing_ok=True)
        finally:
            try:
                provider_guard.close()
            except supervision.VerifiedStop as error:
                if error.cause == "stage_deadline":
                    timed_out = True
                else:
                    interrupted = True
            except processes.ProcessError as error:
                cleanup_error = "; ".join(filter(None, (cleanup_error, str(error))))
            except KeyboardInterrupt:  # the stage's one interrupt, after its provider was collected
                interrupted = True
            independent = supervision.receipt(record["supervision"])
            if independent and independent.get("cause") == "stage_deadline":
                timed_out = True
            elif independent and independent.get("cause") in (
                "owner_lost",
                "lifeline_failure",
                "invalid_owner_message",
            ):
                interrupted = True
        if interrupted:
            record["interrupted"] = True
            worker_path.unlink(missing_ok=True)  # wait_for_stage cleaned up before propagating the interrupt
            exit_code = (
                child.poll() if exit_code is None else exit_code
            )  # one the wait never reached: its keeper stopped it
    record.update(
        finished_at=now(),
        exit_code=exit_code,
        duration_seconds=time.monotonic() - started,
        metrics=support.event_metrics(events),
        timed_out=timed_out,
    )
    if timed_out:
        timeout = getattr(activity, "timeout", None) or {
            "kind": "stage",
            "reason": f"Stage exceeded its {stage_timeout}-second hard runtime limit",
        }
        record.update(timeout_kind=timeout["kind"], timeout_reason=timeout["reason"])
    account_stage(state, record)
    if not cleanup_error and exit_code is not None:
        job_source.stopped(workspace, base, record)
    save(run_dir / "state.json", state)
    if cleanup_error:
        raise support.Paused("PAUSED_PROCESS_CLEANUP", cleanup_error)
    if interrupted:
        raise support.Paused(
            "PAUSED_INTERRUPTED", "Provider stage interrupted; inspect saved artifacts before reconciliation"
        )
    if timed_out:
        raise support.Paused(
            "PAUSED_PROVIDER_TIMEOUT", f"{role}: {record['timeout_reason']}; partial work and logs retained at {events}"
        )
    if exit_code != 0:
        refused = stage_stops.refusal_reason(state, record) or stage_stops.context_window_reason(state, record)
        hint = (
            provider_env.authentication_hint(adapter.raw_events(events), record["withheld_env"])
            if engine == "opencode" and configured_tool
            else ""
        )
        raise support.Paused(
            support.failure_status(events, record=record),
            provider_auth.exit_message(role, exit_code, events, refused=refused or "", hint=hint or ""),
        )
    if supports_sessions:
        thread = format_correction.event_thread_id(events)
        if not thread or (session and thread != session):
            raise support.Paused("PAUSED_UNCERTAIN_STAGE", "Provider returned a missing or unexpected session ID")
        if not session and not report_only:
            state["sessions"][route_role] = thread
            record["thread_id"] = thread
        if not any(e.get("type") == "turn.completed" for e in support.events(events)):
            stage_stops.refused_at_clean_exit(state, record, role, events)
            stage_stops.stopped_at_context_window(state, record, role, events)
            raise support.Paused(
                output_cap.STATUS
                if support.failure_status(events, record=record) == output_cap.STATUS
                else "PAUSED_UNCERTAIN_STAGE",
                output_cap.explain(support.terminal_failure_reason(events), record.get("output_token_cap"))
                or "Process exited without turn.completed",
            )
    elif not output.is_file():
        stage_stops.refused_at_clean_exit(state, record, role, events)
        stage_stops.stopped_at_context_window(state, record, role, events)
        raise support.Paused("PAUSED_UNCERTAIN_STAGE", "Process exited without a report file")
    after = source_scope.snapshot(workspace, state, base_snapshot=support.snapshot)
    save(base.with_suffix(".after.json"), after)
    record["after_ref"] = str(base.with_suffix(".after.json"))
    record["source_revision"] = after["revision"]
    record["changed_files"] = support.changed_paths(before, after)
    diff_path = base.with_suffix(".diff")
    source_diff.write(workspace, diff_path, after)
    record["diff_ref"] = str(diff_path)
    summary_path = base.with_suffix(".tools.json")
    support.summarize_events(events, summary_path)
    record["tool_evidence"] = str(summary_path)
    if not allow_write and before["revision"] != after["revision"]:
        raise support.Paused(
            "PAUSED_STALE_VALIDATION", "Repository changed during read-only review; preserve result and revalidate"
        )
    try:
        value = load_stage_report(
            record,
            workspace,
            (state.get("pending_report_repair") or {}).get("original") if report_only else None,
            state=state,
        )
    except support.Paused:
        raise
    except (ValueError, RuntimeError) as error:
        if native is not None:
            native.reject_launch(state, record, error, native_prepared, after)
        reject_completed_stage(state, run_dir, record, error)
    if native is not None:
        native.context.factory.finish_launch(native_prepared, record, after)
    return value, record


HISTORY_FIELDS = (
    "commands_run",
    "results",
    "changed_files",
    "remaining_risks",
    "untested_behavior",
    "addressed_requirements",
    "deferred_backlog",
)


def restore_builder_history(original, value):
    """Put the original Builder execution history back into a repaired report.

    A repair fixes shape and citations; it must not rewrite a recorded execution. The
    prompt says so, and a repair that ignores it used to be thrown away whole — losing
    a correct fix over bookkeeping (2026-10-05 live cases). Copy the history fields
    back from the original instead of trusting the repair to have left them alone.
    Schema-valid history is restored; missing or ill-typed history stays repairable.
    """
    if original.get("stage") != "terra" or not isinstance(value, dict):
        return False
    previous = repair_report_source(original)["content"]
    if not isinstance(previous, dict):
        previous = {}
    changed = False
    for field in HISTORY_FIELDS:
        old = previous.get(field)
        if isinstance(old, list) and all(isinstance(item, str) for item in old) and value.get(field) != old:
            value[field] = list(old)
            changed = True
    old_request = previous.get("user_request")
    if (
        isinstance(old_request, dict)
        and old_request.get("kind") not in (None, "none")
        and value.get("user_request") != old_request
    ):
        value["user_request"] = copy.deepcopy(old_request)
        changed = True
    return changed


def assert_repair_preserves_builder_history(original, value):
    """Repair a report's shape/citations, never rewrite a recorded execution.

    Schema-valid history fields in the original report are immutable. Missing or
    ill-typed fields may still be repaired, but newly supplied command names must
    come from the original execution events, not a new repair-stage execution.
    restore_builder_history() puts those fields back first, so this is the guard
    that they did: it fails only when history is unrestorable and still rewritten.
    """
    if original.get("stage") != "terra":
        return
    previous = repair_report_source(original)["content"]
    if not isinstance(previous, dict):
        previous = {}
    for field in HISTORY_FIELDS:
        old = previous.get(field)
        if isinstance(old, list) and all(isinstance(item, str) for item in old) and value.get(field) != old:
            raise ValueError(f"Report repair changed recorded Builder history: {field}")
    old_request = previous.get("user_request")
    if (
        isinstance(old_request, dict)
        and old_request.get("kind") not in (None, "none")
        and value.get("user_request") != old_request
    ):
        raise ValueError("Report repair changed the original Builder user decision request")
    old_commands = previous.get("commands_run")
    if not isinstance(old_commands, list) or not all(isinstance(c, str) for c in old_commands):
        commands = {
            e["item"].get("command")
            for e in support.events(original["events"])
            if e.get("type") == "item.completed" and e.get("item", {}).get("type") == "command_execution"
        }
        if any(command not in commands for command in value.get("commands_run", [])):
            raise ValueError("Report repair invented a command absent from original execution events")


def accept_repaired_report(state: RunState, run_dir, workspace, value, repair_record):
    return repaired_result.accept(sys.modules[__name__], state, run_dir, workspace, value, repair_record)


def execute_report_repair(state: RunState, run_dir, workspace, *, native=None):
    """Launch the bounded report-only repair; native planning keeps its own writer and launch owner."""
    runtime = native_invocation.runner_view(sys.modules[__name__], native) if native else sys.modules[__name__]
    pending = state["pending_report_repair"]
    original = pending["original"]
    if state.get("next_stage") != original.get("stage"):
        raise support.Paused(
            "PAUSED_STALE_REPORT_ROUTE", "Saved report repair belongs to a different stage; reconcile before retrying"
        )
    if pending["attempts"] >= repair_limit(state):
        raise support.Paused("PAUSED_REPORT_REPAIR_LIMIT", "Bounded report-only repair attempts exhausted")
    if pending["attempts"] and not pending.get("latest_rejected"):
        # Old checkpoints kept the latest error but only the first report pointer.
        # Reassociate from the owning stage's ordered history, never by filename.
        stages = state.get("stages", [])
        indices = [i for i, row in enumerate(stages) if row.get("events") == original.get("events")]
        if len(indices) != 1:
            raise support.Paused(
                "PAUSED_STALE_VALIDATION", "Cannot identify the original stage for report-repair recovery"
            )
        # A repair timeout the runner archived holds no report (#377); pair the error with the earlier one.
        later = [
            row
            for row in stages[indices[0] + 1 :]
            if row.get("report_only") and not recovered_timeout_attempt(row, stage_completed(state, row))
        ]
        if later:
            latest = later[-1]
            if (
                not latest.get("rejected")
                or latest.get("iteration") != original.get("iteration")
                or latest.get("original_stage", latest["stage"].removesuffix("_report_repair")) != original["stage"]
                or latest.get("source_revision") != original.get("source_revision")
                or latest.get("contract_hash") != original.get("contract_hash")
                or latest.get("rejection_reason") != pending.get("error")
                or not (
                    stage_completed(state, latest)
                    or valid_truncated_report_attempt(latest, stage_completed(state, latest))
                )
            ):
                raise support.Paused(
                    "PAUSED_STALE_VALIDATION", "Latest repair error cannot be paired with its rejected report"
                )
            pending["latest_rejected"] = copy.deepcopy(latest)
            for key in ("output", "response_text", "events", "schema"):
                if latest.get(key) and Path(latest[key]).is_file():
                    pending["pins"].setdefault(latest[key], support.file_hash(latest[key]))
        elif pending.get("error") != original.get("rejection_reason"):
            raise support.Paused("PAUSED_STALE_VALIDATION", "Repair error does not match the saved original report")
    if stale_report_repair(state, workspace):
        raise support.Paused(
            "PAUSED_STALE_VALIDATION",
            f"The source changed after the rejected {original['stage']} report, so its repair cannot run. Resume with "
            f"autocode resume to archive the repair (evidence retained) and start a fresh {original['stage']} attempt.",
        )
    if (
        source_scope.snapshot(workspace, state, base_snapshot=support.snapshot)["revision"]
        != original["source_revision"]
        or (state.get("goal_contract") or {}).get("hash") != pending["contract_hash"]
        or any(not Path(p).is_file() or support.file_hash(p) != h for p, h in pending["pins"].items())
    ):
        raise support.Paused("PAUSED_STALE_VALIDATION", "Saved report-repair inputs changed; do not retry")
    resolver_runtime.boundary(runtime, state, run_dir, workspace)
    if native is None and format_correction.execute(runtime, state, run_dir, workspace):
        return
    original_source, rejected_source = report_repair_context.sources(pending, repair_report_source)
    for source in (original_source, rejected_source):
        pending["pins"].setdefault(source["path"], source["sha256"])
    context_instruction, context_handoff = report_repair_context.prompt_parts(
        state, pending, original_source, rejected_source, receipt_repair_context.prompt_parts(pending)
    )
    prompt = (
        prompts.get("fragments/autocode/execute-report-repair-15.md")
        + repair_report_instruction(pending)
        + prompts.get("fragments/autocode/execute-report-repair-14.md")
        + report_repair_context.baseline_instruction(original["stage"])
        + planning.repair_rules(original["stage"], support.read(Path(original["schema"])))
        + prompts.get("fragments/autocode/execute-report-repair-13.md")
        + stuck_repair_context.evidence_instruction(original["stage"])
        + prompts.get("fragments/autocode/execute-report-repair-12.md")
        + DISPOSITION_REPAIR_RULE
        + prompts.get("fragments/autocode/execute-report-repair-11.md")
        + (report_repair_context.instruction(original["stage"]) if original["stage"] == "astra_finalize" else "")
        + (
            goals.DECISION_PROVENANCE + goals.CONTRACT_REFERENCES
            if original["stage"] == "astra_discovery" or planning.is_planning(state, original["stage"])
            else ""
        )
        + jobs.repair_rules(original["stage"])
        + context_instruction
        + "CURRENT HANDOFF DATA\n"
        + json.dumps(
            {
                "report_repair": True,
                "execution_engine": planning.engine_for(
                    state["settings"], original.get("route_role", original["role"])
                ),
                "error": pending.get(
                    "error",
                    original.get("rejection_reason", "Legacy report validation failed without a recorded error"),
                ),
                **context_handoff["header"],
                "rejected_report": rejected_source,
                "original_report": context_handoff["original_report"],
                "original": {
                    key: original[key]
                    for key in (
                        "role",
                        "stage",
                        "output",
                        "events",
                        "schema",
                        "source_revision",
                        "contract_hash",
                        "contract_revision",
                        "task_id",
                        "truncated_output",
                    )
                    if key in original
                },
                "archived_paths": {
                    **original.get("archived_paths", {}),
                    **pending.get("latest_rejected", {}).get("archived_paths", {}),
                },
                "open_findings": findings_ledger.handoff(state),
                "acceptance_criteria": support.criteria_definition(state.get("acceptance_criteria", [])),
                "source_texts": goals.source_texts(state) if original["stage"] == "requirements_gather" else None,
                "requirement_coverage_checklist": [
                    sentence for source in goals.scan_texts(state) for sentence in goals.cue_sentences(source)
                ]
                if original["stage"] == "requirements_gather"
                else None,
                **report_repair_context.requirements_sources(state, original["stage"]),
                **context_handoff["clarification"],
                "investigation_context": stuck_repair_context.context(
                    state, original["stage"], run_dir / "state.json", workspace, (original_source, rejected_source)
                ),
                "protected_contract": context_handoff["protected_contract"],
                "requirement_trace_rows": planning.trace_rows(state, original["stage"]) or None,
                **planning.repair_citation_context(state, original["stage"], workspace),
                "report_identity": {
                    "contract_hash": (state.get("goal_contract") or {}).get("hash"),
                    "contract_revision": (state.get("goal_contract") or {}).get("revision"),
                    "task_id": (state.get("current_task") or {}).get("id", ""),
                },
                "original_executed_checks": [
                    {
                        "command": e["item"]["command"],
                        "exit_code": e["item"]["exit_code"],
                        "evidence_ref": "event:" + e["item"]["id"],
                    }
                    for e in support.events(original["events"])
                    if e.get("type") == "item.completed"
                    and e.get("item", {}).get("type") == "command_execution"
                    and isinstance(e["item"].get("command"), str)
                    and isinstance(e["item"].get("id"), str)
                    and type(e["item"].get("exit_code")) is int
                ],
                "finding_identity_policy": prompts.get("fragments/autocode/execute-report-repair-06.md"),
                "state_file": str(run_dir / "state.json"),
                **jobs.repair_context(original["stage"], state),
            },
            # Compact: indentation was 16% of a program child's finalize repair, which then overflowed (#1337).
            separators=(",", ":"),
        )
    )
    if len(prompt.encode("utf-8")) > REPAIR_HANDOFF_BYTES:
        raise support.Paused(
            "PAUSED_REPORT_REPAIR_INPUT",
            f"Complete report-repair handoff exceeds {REPAIR_HANDOFF_BYTES} bytes; "
            "no unbounded repair was launched." + REPAIR_INPUT_ROUTE,
        )
    pending["attempts"] += 1
    state.update({"phase": "REPORT_REPAIR"})
    runtime.write_json(run_dir / "state.json", state)
    role = original["role"]
    route_role = planning.route_for(state, original["stage"], role)
    try:
        value, record = runtime.run_role(
            role=role,
            prompt=prompt,
            sandbox="read-only",
            workspace=workspace,
            run_dir=run_dir,
            state=state,
            schema=Path(original["schema"]),
            model=state["settings"]["roles"][route_role]["model"],
            allow_write=False,
            dry_run=False,
            report_only=True,
        )
    except support.Paused as error:
        if error.status in ("PAUSED_INTERVENTION_PENDING", "PAUSED_REPORT_REPAIR_INPUT") and not state.get(
            "active_stage"
        ):
            pending["attempts"] -= 1
            runtime.write_json(run_dir / "state.json", state)
        if native is None and automatically_recover_truncated_review(state, run_dir, workspace, error):
            raise ReportRepairQueued() from error
        if native is None and automatically_recover_report_repair_timeout(state, run_dir, workspace, error):
            raise ReportRepairQueued() from error
        raise
    account_stage(state, record)
    runtime.accept_repaired_report(state, run_dir, workspace, value, record)


def apply_result(state: RunState, stage, value, record, workspace, run_dir, *, native=None):
    runtime = (
        native_invocation.runner_view(sys.modules[__name__], native) if native is not None else sys.modules[__name__]
    )
    result = autopilot.apply_result(runtime, state, stage, value, record, workspace, run_dir)
    job_failure.completed(state, stage)
    return result


def save_record(state: RunState, record):
    state.setdefault("stages", []).append(record)
    state.setdefault("history", []).append(record)
    state["evidence_locations"] = [r["output"] for r in state["stages"][-3:]]
    state.pop("active_stage", None)
    recovery_accounting.stage_saved(state)
    recovery_context.stage_saved(state, record)


def _apply_result(state: RunState, stage, value, record, workspace, run_dir):
    """Compatibility entry for recovery and older callers; Autopilot owns transitions."""
    return autopilot._apply_result(sys.modules[__name__], state, stage, value, record, workspace, run_dir)


MAX_AUTOMATIC_RECOVERIES = recovery_accounting.MAX_AUTOMATIC_RECOVERIES


def timeout_recovery_guard(state: RunState):
    reason = recovery_limits.stop_reason(state, recovery_count(state), MAX_AUTOMATIC_RECOVERIES)
    if reason:
        raise support.Paused(*reason)


def grant_recovery_allowance(state: RunState, run_dir, amount, *, previous_settings=None):
    result = recovery_grants.grant(
        state,
        run_dir,
        amount,
        previous_settings=previous_settings,
        current_request=resolver_human.current,
        count=recovery_count(state),
        supersede=resolver_human.supersede_operational,
        persist=write_json,
        maximum=MAX_AUTOMATIC_RECOVERIES,
    )
    if run_dir is not None:
        route_advisory.report_changes(previous_settings, state.get("settings"))
    return result


def repeated_failure_resume_guard(state: RunState, workspace, *, authorization=None):
    """A restart or plain resume cannot erase an unchanged repeated failure.

    A recognized recovery path that reroutes the run changes the saved status
    before this guard runs. An operator who has inspected the failure can
    authorize one fresh attempt with --retry-failed-stage.
    """
    return recovery_limits.repeated_failure_resume_guard(
        state,
        authorization=authorization,
        repeated_failure=failures.repeated,
        current_revision=lambda: source_scope.snapshot(workspace, state, base_snapshot=support.snapshot)["revision"],
        paused=support.Paused,
    )


def reconcile_active(state: RunState, run_dir, workspace):
    regression.before_review(state, None, workspace, run_dir)
    record = state.get("active_stage")
    if not record:
        return
    held = None
    if record.get("supervision"):
        independent = supervision.receipt(record["supervision"])
        held = supervision_recovery.hold(record, independent, attempt=attempt_id(record))
    if held:
        if held["timed_out"]:
            record["timed_out"] = True
            if record.get("report_only") and automatically_recover_report_repair_timeout(
                state, run_dir, workspace, support.Paused(held["status"], held["reason"])
            ):
                return
        raise support.Paused(held["status"], held["reason"])
    if (
        record.get("report_only")
        and record.get("timed_out")
        and automatically_recover_report_repair_timeout(
            state,
            run_dir,
            workspace,
            support.Paused("PAUSED_PROVIDER_TIMEOUT", record.get("timeout_reason", "Saved repair timeout")),
        )
    ):
        return
    recorded_events = record.get("events")
    if (
        recorded_events
        and Path(recorded_events).is_file()
        and support.failure_status(recorded_events) == "PAUSED_RATE_LIMIT"
        and reconcile_rate_limited_stage(state, run_dir, workspace)
    ):
        raise support.Paused("PAUSED_RATE_LIMIT", state["stop_reason"])
    if record.get("rejected"):
        raise support.Paused(
            "PAUSED_INVALID_OUTPUT",
            "This completed attempt was already rejected. Explicitly retry planning with autocode resume; do not recover the rejected output.",
        )
    assert_stage_stopped(record)
    supports_sessions = stage_supports_sessions(state, record)
    thread = format_correction.event_thread_id(Path(record["events"])) if supports_sessions else None
    unexpected = supports_sessions and (
        ("expected_session" in record and not thread)
        or (record.get("expected_session") and thread != record["expected_session"])
    )
    unexpected_session = support.Paused("PAUSED_UNCERTAIN_STAGE", "Recovered response belongs to an unexpected session")
    if not stage_completed(state, record) or (supports_sessions and record.get("exit_code") not in (None, 0)):
        reason = stage_stops.refusal_reason(state, record)
        # As in run_role, a timeout names the stop before the response is read (and recovers on its own),
        # whatever session it came from. A refusal in a response from a session the run did not expect, or
        # naming none, is typed only for a provider that exited with an error; after a clean, unsaved or
        # signalled (interrupted) exit it is never typed or answered with a model (#464).
        if reason and record.get("timed_out"):
            timeout = record.get("timeout_reason") or "Stage timed out"
            raise support.Paused(
                "PAUSED_PROVIDER_TIMEOUT",
                f"{record['role']}: {timeout}; partial work and logs retained at {record['events']}",
            )
        exit_code = record.get("exit_code")
        if reason and unexpected and not (type(exit_code) is int and exit_code > 0):
            raise unexpected_session
        reason = (
            reason
            or stage_stops.context_window_reason(state, record)
            or output_cap.explain(support.terminal_failure_reason(record["events"]), record.get("output_token_cap"))
        )
        raise support.Paused(
            support.failure_status(record["events"], record=record),
            (f"{reason.rstrip('.')}. " if reason else "")
            + f"Uncertain stage must be inspected, never automatically replayed. After review, "
            f"use --abandon-stage {attempt_id(record)} to retain partial work and set aside this response.",
        )
    if unexpected:
        raise unexpected_session
    if thread and not record.get("report_only"):
        state["sessions"][record.get("route_role", record["role"])] = thread
        record["thread_id"] = thread
    record["metrics"] = support.event_metrics(record["events"])
    account_stage(state, record)
    if not record.get("before_ref"):
        # Never invent the original source snapshot for a legacy partial record.
        try:
            load_stage_report(record, workspace, state=state)
        except (ValueError, RuntimeError) as error:
            reject_completed_stage(state, run_dir, record, error)
        raise support.Paused("PAUSED_UNCERTAIN_STAGE", "Recovered stage lacks its original source snapshot")
    before = read_json(Path(record["before_ref"]))
    after = source_scope.snapshot(workspace, state, base_snapshot=support.snapshot)
    if (record["role"] != "terra" or record.get("report_only")) and before["revision"] != after["revision"]:
        raise support.Paused(
            "PAUSED_STALE_VALIDATION",
            f"Read-only stage revision changed across interruption: {record['stage']} ran on source "
            f"{before['revision'][:12]}, the workspace is now at {after['revision'][:12]}; its result is not applied. "
            f"After inspecting the change, use --abandon-stage {attempt_id(record)} to set the result aside "
            "(evidence and edits retained), then autocode resume for a fresh attempt on the current source.",
        )
    base = Path(record["output"]).with_suffix("")
    source_snapshot.preserve_after(base.with_suffix(".after.json"), after, write_json=write_json)
    record.update(
        after_ref=str(base.with_suffix(".after.json")),
        source_revision=after["revision"],
        changed_files=support.changed_paths(before, after),
        recovered_at=now(),
        metrics=support.event_metrics(record["events"]),
    )
    try:
        value = load_stage_report(
            record,
            workspace,
            (state.get("pending_report_repair") or {}).get("original") if record.get("report_only") else None,
            state=state,
        )
    except support.Paused:
        raise
    except (ValueError, RuntimeError) as error:
        reject_completed_stage(state, run_dir, record, error)
    if record.get("report_only"):
        accept_repaired_report(state, run_dir, workspace, value, record)
        return
    if run_actions.stale_result(state, value, after["revision"]):
        return run_actions.discard_stale_recovered(state, run_dir, record)
    try:
        commit_stage_result(state, record["stage"], value, record, workspace, run_dir)
    except (ValueError, KeyError, support.Paused) as error:
        reject_completed_stage(state, run_dir, record, error)
    write_json(run_dir / "state.json", state)


def capture_command(argv):
    try:
        from . import autocode_capture_command
    except ImportError:
        import autocode_capture_command
    return autocode_capture_command.cli(argv, formatter=support.compact_output)


def configure(args, state: RunState):
    return autocode_configure.configure(
        args, state, planning=planning, milestones=milestones, autopilot=autopilot, opencode=opencode
    )


def configure_joint(settings, args, *, fresh):
    return autocode_configure.configure_joint(settings, args, fresh=fresh, planning=planning, opencode=opencode)


def configure_codex_joint(settings, args):
    return autocode_configure.configure_codex_joint(settings, args, planning=planning)


def migrate_opencode_roles(state: RunState, run_dir, workspace):
    return autocode_configure.migrate_opencode_roles(
        state, run_dir, workspace, planning=planning, opencode=opencode, write_json=write_json, now=now
    )


def check_joint_transports(state: RunState, workspace):
    return joint_transport.check(
        state,
        workspace,
        engine_for=planning.engine_for,
        support=support,
        opencode=opencode,
        check_subscription=check_subscription,
    )


def accept_completion(state: RunState, workspace: Path, *, run_dir=None) -> None:
    """Operator closes a run whose gates all verify independently but whose
    completion report the model cannot produce in the required echo format."""
    if (state.get("settings") or {}).get("replay_recording", {}).get("checks") == "recorded":
        raise ValueError("Recorded-check simulation cannot authorize completion or delivery")
    if state.get("status") == "TASK_COMPLETE":
        raise ValueError("Run is already complete")
    if not goals.approved(state):
        raise ValueError("Completion acceptance requires an approved goal")
    launch_inputs.guard(state, workspace, run_dir)
    current = source_scope.snapshot(workspace, state, base_snapshot=support.snapshot)
    contract = cast(dict, state["goal_contract"])
    # The probe must carry the current task identity: execution_guard rejects a
    # result whose task_id is absent while a task is assigned, so omitting it
    # made --accept-completion unreachable on every real run.
    probe = {
        "status": "TASK_COMPLETE",
        "contract_revision": contract["revision"],
        "contract_hash": contract["hash"],
        "task_id": (state.get("current_task") or {}).get("id", ""),
        "acceptance_criteria": [
            {**c, "status": "verified", "evidence": "Current Validator criterion evidence"}
            for c in state["acceptance_criteria"]
        ],
    }
    if not visual_runtime.completion_allowed(state, current_snapshot=current) or not completion_gate.completion_ready(
        state, probe, current
    ):
        stale = completion_gate.stale_validation(state, current)
        if stale:
            raise ValueError(
                f"Validation is stale: {stale}. Resume with --resume-paused to re-validate the current "
                "source; completion can be accepted only after that validation passes."
            )
        raise ValueError("Completion acceptance requires current passing independent evidence for every criterion")
    if goals.missing_human_reviews(state):
        raise ValueError("Completion acceptance requires every required human review to be recorded")
    failed = sum(1 for r in state.get("stages", []) if r.get("stage") == "astra_review" and r.get("rejected"))
    state.setdefault("user_events", []).append(
        {
            "kind": "completion_accept",
            "actor": "user_cli",
            "at": now(),
            "basis": f"runner-verified gates; {failed} completion-report attempts failed",
            "criteria_revision": state.get("criteria_revision"),
            "contract_revision": cast(dict, state["goal_contract"])["revision"],
            "validation_digest": support.digest(state.get("validation") or {}),
        }
    )
    state.update(
        {
            "status": "TASK_COMPLETE",
            "completed_at": now(),
            "final_decision": probe,
            "completion_actor": "user_cli",
            "next_stage": None,
            "phase": "COMPLETE",
        }
    )
    route_ladder.record_outcome(state, "passed")


def recheck_completion(state: RunState, workspace, *, run_dir=None):
    if state.get("status") != "TASK_COMPLETE":
        return
    changed_inputs = None
    try:
        launch_inputs.guard(state, workspace, run_dir)
    except ValueError as error:
        changed_inputs = str(error)
    if not changed_inputs and completion_gate.completion_ready(
        state, state.get("final_decision", {}), source_scope.snapshot(workspace, state, base_snapshot=support.snapshot)
    ):
        return
    state.setdefault("completion_archive", []).append(
        {"completed_at": state.pop("completed_at", None), "decision": state.pop("final_decision", None)}
    )
    state.pop("completion_actor", None)
    if state.get("validation"):
        state.setdefault("validation_archive", []).append(
            {"reason": "Completed artifact or evidence changed", "validation": state.pop("validation")}
        )
    state["human_reviews"] = {}
    state.pop("displayed_review", None)
    run_status.pause(
        state,
        "PAUSED_STALE_VALIDATION",
        (
            "Completion is no longer current. " + changed_inputs
            if changed_inputs
            else "Completion is no longer current. Use autocode resume for fresh independent validation."
        ),
        next_stage=workflow.review_stage(state),
    )


intervention_metadata = stop_policy.metadata


def consume_interventions(state: RunState, run_dir, workspace, *, persist=None, **options):
    """Commit receipt effects and identity together through the selected sink (options: lock_held, released)."""
    preflight_attempts.reconcile(sys.modules[__name__], state, run_dir)
    return stop_policy.consume(state, run_dir, workspace, write_json=persist or write_json, now=now, **options)


def commit_user_action(state: RunState, candidate, run_dir, *, require_current_inputs=False, persist=None, native=None):
    return parent_boundary.commit_user_action(
        state,
        candidate,
        run_dir,
        require_current_inputs=require_current_inputs,
        save=persist or write_json,
        admission=interventions.admission,
        guard_inputs=launch_inputs.guard,
        commit_pending=planning_artifacts.commit_pending,
        native=native,
        report_changes=route_advisory.report_changes,
    )


def commit_stage_result(state: RunState, stage, value, record, workspace, run_dir, *, persist=None, native=None):
    """Preserve a finished report, applying any earlier input before authorization."""
    result_application.commit(
        state,
        run_dir,
        stage,
        lambda candidate: apply_result(candidate, stage, value, record, workspace, run_dir, native=native),
        lambda candidate: commit_boundary_candidate(
            state,
            candidate,
            run_dir,
            workspace,
            persist=persist,
            native=native,
            planning_record=record if native is not None and stage == "plan_finalize" else None,
        ),
        persist=persist or write_json,
    )
    if native is not None and stage == "sol":
        candidate = native.review_progress(state, record, milestones.observe_validation)
        commit_boundary_candidate(state, candidate, run_dir, workspace, persist=persist, native=native)


def commit_boundary_candidate(
    state: RunState, candidate, run_dir, workspace, *, persist=None, native=None, planning_record=None
):
    return parent_boundary.commit_boundary_candidate(
        state,
        candidate,
        run_dir,
        workspace,
        save=persist or write_json,
        serialized=interventions.serialized,
        pending=interventions.pending,
        consume=consume_interventions,
        commit_pending=planning_artifacts.commit_pending,
        native=native,
        planning_record=planning_record,
    )


def chat_checkpoint(state: RunState, run_dir=None, *, persist=None, native=None) -> bool:
    """Collect discovery answers and goal approval in a single terminal conversation."""
    speaker = "AutoResolver"
    save = persist or write_json
    if native is not None and (state.get("user_request") or {}).get("kind") == "human_review":
        raise support.Paused("PAUSED_VISUAL_EVIDENCE", "Native image/human artifact review requires later scopes")
    normalize_human_boundary(state, run_dir)

    def action(callback, published=None):
        candidate = copy.deepcopy(state)
        if run_dir is not None and interventions.pending(run_dir):
            raise support.Paused(
                "PAUSED_INTERVENTION_PENDING", "Queued intervention takes precedence over this human response"
            )
        if published:
            try:
                resolver_human.require_response(candidate, published["request_id"], published["request_token"])
            except ValueError:
                if run_dir is not None and interventions.pending(run_dir):
                    raise support.Paused(
                        "PAUSED_INTERVENTION_PENDING", "Queued intervention takes precedence over this human response"
                    ) from None
                raise
        if not approval_provenance.decide(callback, state, candidate, sys.argv[1:]):  # who approved (#722)
            return False  # the run's require-approver refused this checkout's Git identity: nothing changed
        if published:
            finish_human_action(candidate, published)
        normalize_human_boundary(candidate, run_dir)
        commit_user_action(
            state,
            candidate,
            run_dir,
            require_current_inputs=bool(published and published["scope"] == "human_review"),
            persist=save,
            native=native,
        )
        return True

    def show_handoff():
        rendered = lifecycle.present(state, run_dir)
        if run_dir is not None:
            save(run_dir / "state.json", state)
        print(rendered)

    def show_question():
        if interaction_timing.mark(state, "question", now()) and run_dir is not None:
            save(run_dir / "state.json", state)

    if state.get("discovery_summary") and state.get("phase") == "DISCOVERING":
        print(f"\n{speaker}: " + state["discovery_summary"])
    while state["status"] == "WAITING_FOR_USER":
        published = resolver_human.current(state)
        if not published:
            print("AutoResolver must evaluate this request before collecting an answer.")
            return False
        if published["scope"] in ("operational_exhaustion", "blocker"):
            show_question()
            return action_resolver.chat(
                sys.modules[__name__], state, run_dir, published, persist=persist, native=native
            )
        if cast(dict, state.get("user_request", {})).get("kind") == "human_review":
            show_handoff()
            for criterion in goals.missing_human_reviews(state):
                try:
                    reply = (
                        input(f"Approve artifact criterion {criterion} after reviewing its evidence? [y/N]: ")
                        .strip()
                        .lower()
                    )
                except (EOFError, KeyboardInterrupt):
                    print("\nChat paused; remaining artifact reviews are pending.")
                    return False
                if reply not in ("y", "yes"):
                    print("Artifact review remains pending.")
                    return False
                current = source_scope.snapshot(Path(state["workspace"]), state, base_snapshot=support.snapshot)
                review = lambda copied: goals.approve_review(copied, criterion, state["displayed_review"], current)
                if not action(review, resolver_human.current(state)):
                    return False
            return state["status"] == "RUNNING"
        if not state.get("pending_questions"):
            show_handoff()
            return False
        if state.get("user_request"):
            print("Decision needed: " + json.dumps(state["user_request"], indent=2))
        for question in list(state.get("pending_questions", [])):
            published = resolver_human.current(state)
            if not published or question["id"] not in {q["id"] for q in published["questions"]}:
                continue
            show_question()
            print(f"\n{speaker}: {question['question']}")
            print(f"Why: {question['why']}")
            for option in question.get("options", []):
                print(f"  - {option}")
            default = question.get("proposed_default", "").strip()
            if default:
                print(f"Suggested default: {default}")
            while True:
                try:
                    reply = input("You (/default, /feedback TEXT, or /pause): ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nChat paused; your prior answers are saved.")
                    return False
                if reply == "/pause":
                    return False
                if reply.startswith("/feedback ") and reply[len("/feedback ") :].strip():
                    action(lambda candidate: goals.feedback(candidate, reply[len("/feedback ") :]))
                    return True
                if reply == "/default" and default:
                    action(
                        lambda candidate: goals.answer(candidate, question["id"], "accept default", delegated=True),
                        published,
                    )
                    break
                if reply:
                    if reply.startswith("/"):
                        print("Use /default, /feedback TEXT or /pause, or type your answer.")
                        continue
                    if published["scope"] == "permission":
                        action(lambda candidate: goals.resolve_permission(candidate, question["id"], reply), published)
                    elif refused := goals.answer_options.refusal(question, reply, feedback="/feedback TEXT"):
                        print(refused)
                        continue
                    else:
                        action(lambda candidate: goals.answer(candidate, question["id"], reply), published)
                    break
                print("Please enter an answer, or /default when a suggested default is available.")
    if state["status"] == "AWAITING_GOAL_APPROVAL":
        published = resolver_human.current(state)
        if not published or published["scope"] != "goal_approval":
            return False
        print("\nAutoResolver: proposed plan ready for your decision:\n")
        show_handoff()
        while True:
            try:
                reply = input("Approve this brief? [y/N], or type planning feedback: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nChat paused; the brief remains available for approval.")
                return False
            if reply.lower() in ("y", "yes", "/approve"):
                return action(
                    lambda candidate: (
                        native.approve(lifecycle.approve, candidate, state["displayed_goal"])
                        if native is not None
                        else lifecycle.approve(candidate, state["displayed_goal"])
                    ),
                    published,
                )
            if reply.lower() in ("", "n", "no", "/pause"):
                print("Brief not approved. Resume this run when you are ready.")
                return False
            if reply.startswith("/feedback "):
                reply = reply[len("/feedback ") :].strip()
            elif reply.startswith("/"):
                print("Use /approve, /feedback TEXT or /pause, or type your feedback.")
                continue
            if reply:
                action(lambda candidate: goals.feedback(candidate, reply))
                return True
    return state["status"] == "RUNNING"


def rotate_if_needed(state: RunState, role, run_dir, *, persist=None):
    limit = state["settings"].get("rotation_after_input_tokens")
    latest = next(
        (
            r
            for r in reversed(state.get("stages", []))
            if r.get("route_role", r.get("role", r.get("stage", "").split("_")[0])) == role
        ),
        None,
    )
    tokens = (latest or {}).get("metrics", {}).get("provider_tokens", {}).get("input_tokens")
    if limit and tokens and tokens >= limit and state.get("sessions", {}).get(role):
        old = state["sessions"].pop(role)
        state.setdefault("session_rotations", []).append(
            {
                "role": role,
                "old_session": old,
                "at": now(),
                "reason": "Previous stage cumulative reported input exceeded rotation threshold; not a context-window measurement",
            }
        )
        (persist or write_json)(run_dir / "state.json", state)


def print_completion(state: RunState, args, workspace, run_dir):
    """Deliver a completed task worktree, then print the outcome: with ``--json``,
    the same payload ``--status`` prints; otherwise the human completion summary.
    Delivery runs in both modes — it commits the branch, it is not just text."""
    delivery = worktrees.deliver(state, workspace)
    if getattr(args, "json", False):
        json_output.status(sys.modules[__name__], state, args, workspace, run_dir)
    else:
        print(jobs.render(state, goals.render_completion) + delivery)


def print_pause(state: RunState, args, workspace, run_dir, rendered):
    """Compatibility entry point for the CLI pause presentation."""
    return status_command.print_pause(sys.modules[__name__], state, args, workspace, run_dir, rendered)


def main(unit=None) -> int:
    return native_invocation.main(sys.modules[__name__], unit=unit)


def _main_body(unit=None, *, launched_at=None) -> int:
    return native_invocation.main_body(sys.modules[__name__], unit=unit, launched_at=launched_at)


def cli(unit=None):
    # Neither a closed terminal nor a late signal after a saved pause may change how the run ends (#454).
    sys.stdout = detached_output.DetachedOutput(sys.stdout)
    sys.stderr = detached_output.DetachedOutput(sys.stderr)
    try:
        with processes.interrupts_held(until_exit=True):
            return main() if unit is None else main(unit=unit)
    except (RuntimeError, ValueError, OSError) as error:
        print(f"autocode: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(cli())
