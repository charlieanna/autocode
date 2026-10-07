"""The build loop of one autocode invocation: the checks before each stage, launching the stage,
and what happens after it, driven by the orchestrator under the checkout lock.

run() returns the exit code when the loop stops, or None when it runs to the end. Like
autocode_run_actions, it is given the runner module and calls the runner's own functions as runner.X
(run_role, write_json, consume_interventions, ...), so tests that patch the runner still reach it and
runner.opencode is the provider selected for this invocation. It never imports autocode.
"""
from __future__ import annotations


try:
    from . import autocode_job_failure as job_failure
    from . import autopilot
    from . import autocode_checkout_lock as checkout_lock
    from . import autocode_dispatch as dispatch
    from . import autocode_interventions as interventions
    from . import autocode_milestones as milestones
    from . import autocode_planning as planning
    from . import autocode_progressive_state as progressive
    from . import autocode_regression as regression, autocode_provider_recovery as provider_recovery
    from . import autocode_launch_inputs as launch_inputs
    from . import autocode_resolver_runtime as resolver_runtime
    from . import autocode_support as support
    from .units import common
    from . import autocode_workflow as workflow
    from . import autocode_workflows as workflows
except ImportError:
    import autocode_job_failure as job_failure
    import autopilot
    import autocode_checkout_lock as checkout_lock
    import autocode_dispatch as dispatch
    import autocode_interventions as interventions
    import autocode_milestones as milestones
    import autocode_planning as planning
    import autocode_progressive_state as progressive
    import autocode_regression as regression, autocode_provider_recovery as provider_recovery
    import autocode_launch_inputs as launch_inputs
    import autocode_resolver_runtime as resolver_runtime
    import autocode_support as support
    from units import common
    import autocode_workflow as workflow
    import autocode_workflows as workflows


def run(runner, args, state, state_path, run_dir, workspace):
    """Drive the run's stages until it stops; return the exit code, or None if the loop ends normally."""
    def before_code_stage(current):
        try:
            if runner.consume_interventions(current, run_dir, workspace):
                print(f"{current['status']}: {current['stop_reason']}")
                raise runner.orchestrator.LoopExit(2)
        except interventions.InterventionError as error:
            raise support.Paused("PAUSED_INTERVENTION_ACK", str(error)) from error
        regression.before_review(current, None, workspace, run_dir)
        try:
            launch_inputs.guard(current, workspace, run_dir)
        except ValueError as error:
            raise support.Paused('PAUSED_STALE_VALIDATION', str(error)) from error
        if args.unit and autopilot.pending_unit(current) != args.unit:
            autopilot.publish_handoffs(current, run_dir)
            runner.write_json(state_path, current)
            print(f"{args.unit}: handoff ready; next unit={autopilot.pending_unit(current)}", flush=True)
            raise runner.orchestrator.LoopExit(0)
        if milestones.apply_queued_activation(current, run_dir):
            print("Milestone checkpoints enabled at a safe boundary; continuing with independent validation.", flush=True)
        workflow.guard(current)
        if current.get("next_stage") in ("terra", "sol", "orchestrator", "completion"):
            dispatch.enforce_cross_model_verification(current)
        repairing_before_upgrade = (args.resume_paused and current.get('pending_report_repair')
                                    and milestones.owns_pause(run_dir))
        if (run_dir / "pause-requested").exists() and not repairing_before_upgrade:
            raise support.Paused("PAUSED_REQUESTED", "Pause requested; previous stage saved")
        limits = current["settings"]["limits"]
        runner.timeout_recovery_guard(current)
        if runner.iteration_limit_reached(current["iteration"], limits["iteration_ceiling"]):
            if not runner.recover_default_budget(current, run_dir, workspace, 'iteration_ceiling'):
                raise support.Paused("PAUSED_ITERATION_LIMIT", "Saved iteration ceiling reached")
        if limits["max_seconds"] and current.get("active_seconds",0) >= limits["max_seconds"]:
            if not runner.recover_default_budget(current, run_dir, workspace, 'max_seconds'):
                raise support.Paused("PAUSED_TIME_LIMIT", "Saved active-time limit reached at stage boundary")
        classifying = (current.get('next_stage') == 'investigate_stuck'
                       and (current.get('stuck_investigation') or {}).get('mode') == 'builder_failure')
        if (not repairing_before_upgrade and not classifying and (not milestones.enabled(current) or current.get('next_stage') in ('terra', 'orchestrator')) and limits["no_progress_batches"]
                and current.get("no_progress_batches",0) >= limits["no_progress_batches"]):
            raise support.Paused("PAUSED_NO_PROGRESS", "Repeated unchanged implementation batches require review")
        # Do not silently change auth/provider when local config changes.
        engine = current["settings"].get("engine")
        using_opencode = engine == "opencode"
        if engine not in (None, "codex", "opencode"):
            raise support.Paused("PAUSED_TRANSPORT_CHANGED", f"Saved engine {engine!r} is not bundled in "
                             "this checkout; resume it from a checkout that has it, or start a new run "
                             "with --provider and a user-level provider config")
        if planning.enabled(current):
            runner.check_joint_transports(current, workspace)
        if using_opencode:
            current_settings = runner.opencode.local_settings(workspace)
            drifted = runner.opencode.transport_drift(current_settings, current["settings"]["transport_identity"])
        else:
            current_settings = support.local_settings()
            drifted = support.transport_drift(current_settings, current["settings"]["transport_identity"], current["settings"]["roles"])
        if drifted:
            raise support.Paused("PAUSED_TRANSPORT_CHANGED", "Local model/auth/provider settings differ from checkpoint")
        if using_opencode and current["settings"]["transport_identity"].get("identity_version", 1) < 2:
            current.setdefault("configuration_changes", []).append({"at": runner.now(),
                "reason": "Expanded OpenCode configuration identity; all previously recorded inputs match"})
            current["settings"]["transport_identity"] = current_settings
        if current.get('pending_report_repair'):
            try:
                runner.execute_report_repair(current, run_dir, workspace)
            except runner.ReportRepairQueued:
                return runner.orchestrator.SKIP
            # Repair completed and applied the result. Run the after
            # callback so chat_checkpoint and pipeline advancement fire.
            after_code_stage(current, current.get('next_stage', 'report_repair'), None)
            return runner.orchestrator.SKIP

    def dispatch_code_stage(current, stage):
        progressive.guard_dispatch(current, stage)
        if current.get('_failure_routing_enabled', True):
            autopilot.builder_failure.dispatch_guard(current, stage, workspace)
        # Admission parity with autopilot.dispatch_unit: a paused Builder
        # retry lane blocks the serial writer launch here as well.
        if stage == "terra":
            autopilot.builder_policy.guard(current)
        try:
            milestones.dispatch_guard(current, stage)
        except support.Paused as error:
            if (error.status != 'PAUSED_MILESTONE_TIME_LIMIT'
                    or not runner.recover_default_budget(current, run_dir, workspace, 'milestone_max_seconds')):
                raise
            milestones.dispatch_guard(current, stage)
        workflow.dispatch_guard(current,stage,workspace)
        autopilot.admit_validation(runner, current, stage, workspace, run_dir)
        if planning.is_planning(current, stage):
            if not runner.recover_default_budget(current, run_dir, workspace, 'planning_review_call_limit'):
                resolver_runtime.operational_boundary(runner, current, run_dir, workspace)
        regression.before_review(current, stage, workspace, run_dir)
        try:
            launch_inputs.guard(current, workspace, run_dir)
        except ValueError as error:
            raise support.Paused('PAUSED_STALE_VALIDATION', str(error)) from error
        if stage == "orchestrator":
            return autopilot.unit_module(stage).dispatch(current, workspace, run_dir)
        request = autopilot.prepare_request(current, stage, state_path, runner.SCHEMA_DIR)
        role, route_role = request.role, request.route_role
        runner.rotate_if_needed(current, route_role, run_dir)
        prompt, metrics = request.prompt, request.metrics
        current["pending_context_metrics"] = metrics
        # Soft budget: keep exact requirements; don't silently truncate them.
        if metrics["estimated_prompt_tokens"] > metrics["soft_budget_tokens"]:
            print("Context soft budget exceeded; preserving complete requirements", flush=True)
        runner.write_json(state_path, current)
        schema_value = request.schema
        schema_path = run_dir / "schemas" / f"v3-{stage}.json"
        runner.write_json(schema_path, support.model_output_schema(schema_value))
        try:
            value, record = runner.run_role(role=role, prompt=prompt, sandbox=common.launch_sandbox(stage, request.allow_write),
                workspace=workspace, run_dir=run_dir, state=current,
                schema=schema_path,
                model=current["settings"]["roles"][route_role]["model"], allow_write=request.allow_write, dry_run=False,
                retry_authorization=getattr(args, '_failure_retry_authorization', None))
            record["unit"] = autopilot.unit_for(stage)
            runner.account_stage(current, record)
            try:
                runner.commit_stage_result(current, stage, value, record, workspace, run_dir)
            except (ValueError, KeyError, support.Paused) as error:
                runner.reject_completed_stage(current, run_dir, record, error)
        except runner.ReportRepairQueued:
            return runner.orchestrator.SKIP
        except support.Paused as error:
            if job_failure.recover(runner, current, run_dir, workspace, error):
                return runner.orchestrator.SKIP
            if provider_recovery.recover_startup(runner, current, run_dir, workspace, error):
                return runner.orchestrator.SKIP
            if runner.automatically_recover_truncated_review(current, run_dir, workspace, error):
                return runner.orchestrator.SKIP
            capacity_recovered = runner.automatically_recover_capacity_stage(current, run_dir, workspace, error)
            if capacity_recovered:
                recovery = current["recovery_context"]
                inspector = runner.autocode_status.role_name(recovery["next_stage"], current)
                print(f"{stage}: provider capacity recovery {recovery['retry_number']}/"
                      f"{runner.MAX_AUTOMATIC_CAPACITY_RECOVERIES}; partial work archived for {inspector} inspection", flush=True)
                return runner.orchestrator.SKIP
            if (runner.automatically_recover_timed_out_stage(current, run_dir, workspace, error)
                    or runner.automatically_recover_external_directory_denial(current, run_dir, workspace, error)):
                if (current.get('recovery_context') or {}).get('timeout_kind') == 'stage':
                    runner.recover_default_budget(current, run_dir, workspace, 'stage_timeout_seconds')
                print(f"{stage}: non-terminal attempt archived; continuing from recovery checkpoint", flush=True)
                return runner.orchestrator.SKIP
            raise
        return record

    def after_code_stage(current, stage, _record):
        print(f"{stage}: saved; next={current['next_stage']}; status={current['status']}"
              + workflows.describe(current, stage), flush=True)
        autopilot.publish_handoffs(current, run_dir)
        if milestones.enabled(current):
            print(milestones.status_line(current), flush=True)
        try:
            if runner.consume_interventions(current, run_dir, workspace):
                print(f"{current['status']}: {current['stop_reason']}")
                raise runner.orchestrator.LoopExit(2)
        except interventions.InterventionError as error:
            raise support.Paused("PAUSED_INTERVENTION_ACK", str(error)) from error
        resolver_runtime.boundary(runner, current, run_dir, workspace)
        if args.chat and current["status"] in ("WAITING_FOR_USER", "AWAITING_GOAL_APPROVAL"):
            if not runner.chat_checkpoint(current, run_dir):
                runner.write_json(state_path, current)
                raise runner.orchestrator.LoopExit(2)
            runner.write_json(state_path, current)
        if args.pause_after_stage and current["status"] == "RUNNING":
            raise support.Paused("PAUSED_REQUESTED", "--pause-after-stage checkpoint reached")
    try:  # One run's agents at a time in a checkout (autocode_checkout_lock).
        with checkout_lock.exclusive(workspace, run_dir, busy=runner.orchestrator.LoopExit(2)):
            if "base_commit" in state and not state["base_commit"] and not any(
                    not row.get("runner_owned") for row in state.get("stages") or []):
                # Found the checkout busy at launch (autocode_run_setup.launch_base): it starts from here.
                state["base_commit"] = regression.launch_base(workspace, run_dir)
                runner.write_json(state_path, state)
            runner.orchestrator.drive(state, dispatch_code_stage, before=before_code_stage,
                               persist=lambda current: (autopilot.publish_handoffs(current, run_dir), runner.write_json(state_path, current)),
                               after=after_code_stage, investigate=not args.unit)
    except runner.orchestrator.LoopExit as stopped:
        return stopped.code
    return None
