"""The build loop of one autocode invocation: the checks before each stage, launching the stage,
and what happens after it, driven by the orchestrator under the checkout lock.

run() returns the exit code when the loop stops, or None when it runs to the end. Like
autocode_run_actions, it is given the runner module and calls the runner's own functions as runner.X
(run_role, write_json, consume_interventions, ...), so tests that patch the runner still reach it and
runner.opencode is the provider selected for this invocation. It never imports autocode.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_acceptance_tests as acceptance_tests
    from . import autocode_approval_provenance as approval_provenance
    from . import autocode_checkout_lock as checkout_lock
    from . import autocode_climb_cap as climb_cap
    from . import autocode_completion_result as completion_result
    from . import autocode_cost as cost
    from . import autocode_dispatch as dispatch
    from . import autocode_interventions as interventions
    from . import autocode_launch_inputs as launch_inputs
    from . import autocode_milestones as milestones
    from . import autocode_model_preflight as model_preflight
    from . import autocode_planning as planning
    from . import autocode_progressive_state as progressive
    from . import autocode_recovery_dispatch as recovery_dispatch
    from . import autocode_regression as regression
    from . import autocode_resolver_runtime as resolver_runtime
    from . import autocode_skip_requirements as skip_requirements
    from . import autocode_storage_budget as storage_budget
    from . import autocode_support as support
    from . import autocode_workflow as workflow
    from . import autocode_workflows as workflows
    from . import autopilot
    from .units import common
except ImportError:
    import autocode_acceptance_tests as acceptance_tests
    import autocode_approval_provenance as approval_provenance
    import autocode_checkout_lock as checkout_lock
    import autocode_climb_cap as climb_cap
    import autocode_completion_result as completion_result
    import autocode_cost as cost
    import autocode_dispatch as dispatch
    import autocode_interventions as interventions
    import autocode_launch_inputs as launch_inputs
    import autocode_milestones as milestones
    import autocode_model_preflight as model_preflight
    import autocode_planning as planning
    import autocode_progressive_state as progressive
    import autocode_recovery_dispatch as recovery_dispatch
    import autocode_regression as regression
    import autocode_resolver_runtime as resolver_runtime
    import autocode_skip_requirements as skip_requirements
    import autocode_storage_budget as storage_budget
    import autocode_support as support
    import autocode_workflow as workflow
    import autocode_workflows as workflows
    import autopilot
    from units import common


def iteration_limit_reached(iteration, ceiling):
    """None is explicitly unlimited; zero retains the existing zero-budget meaning."""
    if ceiling is None:
        return False
    if type(ceiling) is not int or ceiling < 0:
        raise ValueError("iteration_ceiling must be a nonnegative integer or null (unlimited)")
    return iteration > ceiling


def record_resume(runner, args, state: RunState, state_path) -> None:
    """Save who resumed the run out of the pause it was found in (run_setup.resumed_pause) (#722).

    The loop calls it just before it launches work (a stage, a parallel batch, a report repair),
    once every check that could hold the run at its pause has passed. A pause the loop re-raises
    before that, such as a kept ``pause-requested`` file, changed transport settings, a held
    Builder failure or a climb projected past a budget (climb_cap.guard, #611), kept the run where
    it was and records nothing. Once per invocation.
    """
    pause = getattr(args, "_resumed_pause", None)
    if not pause:
        return
    args._resumed_pause = None
    called = approval_provenance.invocation(
        sys.argv[1:],
        claimed_as=getattr(args, "claimed_as", None),
        word="resume" if getattr(args, "_resume_command", False) else None,
    )
    if approval_provenance.acknowledge(state, pause, called, at=runner.now()):
        runner.write_json(state_path, state)


def run(runner, args, state: RunState, state_path, run_dir, workspace, *, checkout_owned=False):
    """Drive the run's stages until it stops; return the exit code, or None if the loop ends normally."""
    storage_checked = False

    def check_storage(current):
        nonlocal storage_checked
        if storage_checked or storage_budget.conversation_only(current):
            return True
        budget = storage_budget.projection(workspace)
        reason = storage_budget.refusal(budget)
        if reason:
            print(f"Cannot start: {reason}", file=sys.stderr)
            return False
        note = storage_budget.warning(budget)
        if note:
            print(f"warning: {note}", file=sys.stderr)
        storage_checked = True
        return True

    def consume_boundary(current):
        try:
            if runner.consume_interventions(current, run_dir, workspace):
                print(f"{current['status']}: {current['stop_reason']}")
                raise runner.orchestrator.LoopExit(2)
        except interventions.InterventionError as error:
            raise support.Paused("PAUSED_INTERVENTION_ACK", str(error)) from error

    def before_probe():
        consume_boundary(state)
        if (run_dir / "pause-requested").exists():
            raise support.Paused("PAUSED_REQUESTED", "Pause requested; previous model access probe saved")
        if reason := storage_budget.dispatch_refusal(workspace, run_dir):
            raise support.Paused("PAUSED_TASK_PREFLIGHT", reason)

    try:
        model_preflight.check(
            runner,
            state,
            run_dir,
            workspace,
            checkpoint=lambda: runner.write_json(state_path, state),
            before_probe=before_probe,
            selection=(state.get("settings") or {}).get("route_selection"),
        )
    except runner.orchestrator.LoopExit as stopped:
        return stopped.code

    def before_code_stage(current):
        if not check_storage(current):
            raise runner.orchestrator.LoopExit(2)
        if current.get("next_stage") in model_preflight.DEFERRED_STAGES:  # a deferred role's access, first (#1744)
            model_preflight.check(
                runner,
                current,
                run_dir,
                workspace,
                checkpoint=lambda: runner.write_json(state_path, current),
                before_probe=before_probe,
                selection=(current.get("settings") or {}).get("route_selection"),
            )
        native = getattr(runner, "native_calls", None)
        if native is not None:
            native.guard(current)
        consume_boundary(current)
        regression.before_review(current, None, workspace, run_dir)
        try:
            launch_inputs.guard(current, workspace, run_dir)
        except ValueError as error:
            raise support.Paused("PAUSED_STALE_VALIDATION", str(error)) from error
        if args.unit and autopilot.pending_unit(current) != args.unit:
            autopilot.publish_handoffs(current, run_dir)
            runner.write_json(state_path, current)
            print(f"{args.unit}: handoff ready; next unit={autopilot.pending_unit(current)}", flush=True)
            raise runner.orchestrator.LoopExit(0)
        if milestones.apply_queued_activation(current, run_dir):
            print(
                "Milestone checkpoints enabled at a safe boundary; continuing with independent validation.", flush=True
            )
        if autopilot.lifecycle.refresh_diagnosis(current, workspace):
            runner.write_json(state_path, current)
            if current["status"] != "RUNNING":
                raise support.Paused(current["status"], current["stop_reason"])
        workflow.guard(current)
        # spec-first: the Tester writes the tests before the Builder, for a parallel batch's whole wave (#1200)
        acceptance_tests.route(current, wave=lambda: dispatch.select(current))
        if current.get("next_stage") in ("terra", "sol", "orchestrator", "completion"):
            dispatch.enforce_cross_model_verification(current)
        repairing_before_upgrade = (
            args.resume_paused and current.get("pending_report_repair") and milestones.owns_pause(run_dir)
        )
        if (run_dir / "pause-requested").exists() and not repairing_before_upgrade:
            raise support.Paused("PAUSED_REQUESTED", "Pause requested; previous stage saved")
        limits = current["settings"]["limits"]
        runner.timeout_recovery_guard(current)
        if runner.iteration_limit_reached(
            current["iteration"], limits["iteration_ceiling"]
        ) and not runner.recover_default_budget(current, run_dir, workspace, "iteration_ceiling"):
            raise support.Paused("PAUSED_ITERATION_LIMIT", "Saved iteration ceiling reached")
        if (
            limits["max_seconds"]
            and current.get("active_seconds", 0) >= limits["max_seconds"]
            and not runner.recover_default_budget(current, run_dir, workspace, "max_seconds")
        ):
            raise support.Paused("PAUSED_TIME_LIMIT", "Saved active-time limit reached at stage boundary")
        # The money cap (#721) is the person's: never extended automatically, checked before any launch.
        if reached := cost.exceeded(current):
            raise support.Paused(
                "PAUSED_COST_LIMIT",
                f"Spend so far {cost.usd(reached[0])} reached the {cost.usd(reached[1])} cost cap at stage boundary",
            )
        classifying = (
            current.get("next_stage") == "investigate_stuck"
            and (current.get("stuck_investigation") or {}).get("mode") == "builder_failure"
        )
        if (
            not repairing_before_upgrade
            and not classifying
            and (not milestones.enabled(current) or current.get("next_stage") in ("terra", "orchestrator"))
            and limits["no_progress_batches"]
            and current.get("no_progress_batches", 0) >= limits["no_progress_batches"]
        ):
            raise support.Paused("PAUSED_NO_PROGRESS", "Repeated unchanged implementation batches require review")
        # Do not silently change auth/provider when local config changes.
        engine = current["settings"].get("engine")
        using_opencode = engine == "opencode"
        if engine not in (None, "codex", "opencode"):
            raise support.Paused(
                "PAUSED_TRANSPORT_CHANGED",
                f"Saved engine {engine!r} is not bundled in "
                "this checkout; resume it from a checkout that has it, or start a new run "
                "with --provider and a user-level provider config",
            )
        if current["settings"].get("replay_recording"):
            try:
                from . import autocode_replay_transport as replay_transport
            except ImportError:
                import autocode_replay_transport as replay_transport
            replay_transport.selected(current["settings"])
        else:
            if planning.enabled(current):
                runner.check_joint_transports(current, workspace)
            if using_opencode:
                current_settings = runner.opencode.local_settings(workspace)
                drifted = runner.opencode.transport_drift(current_settings, current["settings"]["transport_identity"])
            else:
                current_settings = support.local_settings()
                drifted = support.transport_drift(
                    current_settings, current["settings"]["transport_identity"], current["settings"]["roles"]
                )
            if drifted:
                raise support.Paused(
                    "PAUSED_TRANSPORT_CHANGED", "Local model/auth/provider settings differ from checkpoint"
                )
            if using_opencode and current["settings"]["transport_identity"].get("identity_version", 1) < 2:
                current.setdefault("configuration_changes", []).append(
                    {
                        "at": runner.now(),
                        "reason": "Expanded OpenCode configuration identity; all previously recorded inputs match",
                    }
                )
                current["settings"]["transport_identity"] = current_settings
        if current.get("pending_report_repair"):
            record_resume(runner, args, current, state_path)
            try:
                runner.execute_report_repair(current, run_dir, workspace)
            except runner.ReportRepairQueued:
                return runner.orchestrator.SKIP
            # Repair completed and applied the result. Run the after
            # callback so chat_checkpoint and pipeline advancement fire.
            after_code_stage(current, current.get("next_stage", "report_repair"), None)
            return runner.orchestrator.SKIP

    def dispatch_code_stage(current, stage):
        native = getattr(runner, "native_calls", None)
        if native is not None:
            native.guard(current, stage)
        progressive.guard_dispatch(current, stage)
        autopilot.builder_failure.dispatch_guard(current, stage, workspace)
        # Admission parity with autopilot.dispatch_unit: a paused Builder
        # retry lane blocks the serial writer launch here as well.
        if stage == "terra":
            autopilot.builder_policy.guard(current)
        try:
            if native is not None:
                native.dispatch_guard(current, stage, milestones.dispatch_guard)
            else:
                milestones.dispatch_guard(current, stage)
        except support.Paused as error:
            if getattr(runner, "native_calls", None) is not None:
                raise  # No automatic authority, allowance or report repair in Scope 3.
            if error.status != "PAUSED_MILESTONE_TIME_LIMIT" or not runner.recover_default_budget(
                current, run_dir, workspace, "milestone_max_seconds"
            ):
                raise
            milestones.dispatch_guard(current, stage)
        workflow.dispatch_guard(current, stage, workspace)
        autopilot.admit_validation(runner, current, stage, workspace, run_dir)
        if planning.is_planning(current, stage) and not runner.recover_default_budget(
            current, run_dir, workspace, "planning_review_call_limit"
        ):
            resolver_runtime.operational_boundary(runner, current, run_dir, workspace)
        completion_result.before_review(current, stage, workspace, run_dir)
        try:
            launch_inputs.guard(current, workspace, run_dir)
        except ValueError as error:
            raise support.Paused("PAUSED_STALE_VALIDATION", str(error)) from error
        if stage == "orchestrator":
            record_resume(runner, args, current, state_path)  # every check that could hold the pause passed
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
        if current.get("status") != "RUNNING":
            # Saving adjudicated a queued resolver request (published or deferred it): launch nothing.
            return runner.orchestrator.SKIP
        # A role's climb on its effort ladder is checked before it launches (#611): priced against the money cap
        # (PAUSED_COST_LIMIT), then timed against an active-time budget you set (PAUSED_TIME_LIMIT) and, for the
        # Builder, against the milestone budget you set (PAUSED_MILESTONE_BUDGET).
        climb_cap.guard(current, stage, route_role)
        # Only now has every check that could hold the pause passed, the climb's included, as for the Builder's
        # stronger attempt (builder_policy.guard above): a resume the projection keeps paused records nothing.
        record_resume(runner, args, current, state_path)
        schema_value = request.schema
        schema_path = run_dir / "schemas" / f"v3-{stage}.json"
        runner.write_json(schema_path, support.model_output_schema(schema_value))
        try:
            value, record = runner.run_role(
                role=role,
                prompt=prompt,
                sandbox=common.launch_sandbox(stage, request.allow_write),
                workspace=workspace,
                run_dir=run_dir,
                state=current,
                schema=schema_path,
                model=current["settings"]["roles"][route_role]["model"],
                allow_write=request.allow_write,
                dry_run=False,
                retry_authorization=getattr(args, "_failure_retry_authorization", None),
            )
            record["unit"] = autopilot.unit_for(stage)
            runner.account_stage(current, record)
            try:
                runner.commit_stage_result(current, stage, value, record, workspace, run_dir)
            except (ValueError, KeyError, support.Paused) as error:
                runner.reject_completed_stage(current, run_dir, record, error)
        except runner.ReportRepairQueued:
            return runner.orchestrator.SKIP
        except support.Paused as error:
            if getattr(runner, "native_calls", None) is not None:
                raise  # Retained native attempts grant no automatic recovery.
            fired = recovery_dispatch.recover(runner, current, run_dir, workspace, error)
            if fired == "capacity":
                recovery = current["recovery_context"]
                inspector = runner.autocode_status.role_name(recovery["next_stage"], current)
                print(
                    f"{stage}: provider capacity recovery {recovery['retry_number']}/"
                    f"{runner.MAX_AUTOMATIC_CAPACITY_RECOVERIES}; partial work archived for {inspector} inspection",
                    flush=True,
                )
                return runner.orchestrator.SKIP
            if fired in ("timeout", "external_directory_denial"):
                if (current.get("recovery_context") or {}).get("timeout_kind") == "stage":
                    runner.recover_default_budget(current, run_dir, workspace, "stage_timeout_seconds")
                print(f"{stage}: non-terminal attempt archived; continuing from recovery checkpoint", flush=True)
                return runner.orchestrator.SKIP
            if fired:
                return runner.orchestrator.SKIP
            raise
        return record

    def after_code_stage(current, stage, _record):
        print(
            f"{stage}: saved; next={current['next_stage']}; status={current['status']}"
            + workflows.describe(current, stage),
            flush=True,
        )
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
        if skip_requirements.settle(runner, current, run_dir, native=getattr(runner, "native_calls", None)):
            runner.write_json(state_path, current)  # an unattended worker's defaults or approval (#1731)
        if args.chat and current["status"] in ("WAITING_FOR_USER", "AWAITING_GOAL_APPROVAL"):
            if not runner.chat_checkpoint(current, run_dir):
                runner.write_json(state_path, current)
                raise runner.orchestrator.LoopExit(2)
            runner.write_json(state_path, current)
        if args.pause_after_stage and current["status"] == "RUNNING":
            raise support.Paused("PAUSED_REQUESTED", "--pause-after-stage checkpoint reached")

    # A conversation creates no workspace copies. Defer their storage estimate
    # until planning starts, including a transition within this same invocation.
    if not check_storage(state):
        return 2

    try:  # One run's agents at a time in a checkout (autocode_checkout_lock).
        from contextlib import nullcontext

        with nullcontext() if checkout_owned else checkout_lock.exclusive(workspace, run_dir):
            if (
                "base_commit" in state
                and not state["base_commit"]
                and not any(not row.get("runner_owned") for row in state.get("stages") or [])
            ):
                # Found the checkout busy at launch (autocode_run_setup.launch_base): it starts from here.
                regression.start(state, workspace, run_dir)
                if "launch_sources" in state and state["launch_sources"].get("base_commit") is None:
                    # The launch record vouched for no base. Retake it for this base, under the same lock and
                    # before any provider, as an idle launch takes both together; otherwise supply() distrusts
                    # it and the proof cannot tell an inert base from behavior to preserve.
                    launch_inputs.record(state, workspace, run_dir)
                runner.write_json(state_path, state)
            runner.orchestrator.drive(
                state,
                dispatch_code_stage,
                before=before_code_stage,
                persist=lambda current: (
                    autopilot.publish_handoffs(current, run_dir),
                    runner.write_json(state_path, current),
                ),
                after=after_code_stage,
                investigate=not args.unit,
                # Native authority never admits the Investigator: sending a stage pause to it would
                # only spend its identity and bury the original reason under a refusal. A Builder
                # failure request it refuses is still restored to its own pause.
                queue=getattr(runner, "native_calls", None) is None,
            )
    except runner.orchestrator.LoopExit as stopped:
        return stopped.code
    return None
