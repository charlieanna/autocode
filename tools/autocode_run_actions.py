"""What one autocode invocation does to a saved run before it builds.

It applies dependency deliveries, operational-recovery decisions, AutoResolver responses, an
abandoned stage, a resumed pause, state migrations, the user's answers, approvals and feedback, and
stops for runs waiting on the user. handle() returns an exit code when the invocation ends here, or
None to go on to the build loop.

handle() runs STEPS in order, and their order is the precedence of the actions. Each family of
actions has its own step module (autocode_action_edits, autocode_action_recovery,
autocode_action_authority, autocode_action_resolver, autocode_action_decisions): a new flag's
action goes in the step for its family, not in handle() (#1052).

It is given the runner module (autocode) and calls the runner's own functions as runner.X, the way
autopilot.apply_result does, so tests that patch the runner still reach this code and the provider
selected for this invocation (runner.opencode) is the one used. It never imports autocode.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

try:
    from . import autocode_action_authority as action_authority
    from . import autocode_action_decisions as action_decisions
    from . import autocode_action_edits as action_edits
    from . import autocode_action_recovery as action_recovery
    from . import autocode_action_resolver as action_resolver
    from . import autocode_conversation_ingress as conversation_ingress
    from . import autocode_design_intake as design_intake
    from . import autocode_dispatch as dispatch
    from . import autocode_goal_lifecycle as lifecycle
    from . import autocode_goals as goals
    from . import autocode_interventions as interventions
    from . import autocode_jobs as jobs
    from . import autocode_member_stop as member_stop
    from . import autocode_milestones as milestones
    from . import autocode_operational_information as operational_information
    from . import autocode_planning as planning
    from . import autocode_resolver_human as resolver_human
    from . import autocode_resolver_runtime as resolver_runtime
    from . import autocode_run_finder as run_finder
    from . import autocode_run_records as records
    from . import autocode_stop as stop
    from . import autocode_support as support
    from . import autocode_workflows as workflows
    from . import autocode_worktrees as worktrees
except ImportError:
    import autocode_action_authority as action_authority
    import autocode_action_decisions as action_decisions
    import autocode_action_edits as action_edits
    import autocode_action_recovery as action_recovery
    import autocode_action_resolver as action_resolver
    import autocode_conversation_ingress as conversation_ingress
    import autocode_design_intake as design_intake
    import autocode_dispatch as dispatch
    import autocode_goal_lifecycle as lifecycle
    import autocode_goals as goals
    import autocode_interventions as interventions
    import autocode_jobs as jobs
    import autocode_member_stop as member_stop
    import autocode_milestones as milestones
    import autocode_operational_information as operational_information
    import autocode_planning as planning
    import autocode_resolver_human as resolver_human
    import autocode_resolver_runtime as resolver_runtime
    import autocode_run_finder as run_finder
    import autocode_run_records as records
    import autocode_stop as stop
    import autocode_support as support
    import autocode_workflows as workflows
    import autocode_worktrees as worktrees


@dataclasses.dataclass
class Decided:
    """What this invocation's earlier steps established that a later step reads."""

    dispatch: bool = False  # go straight to the build loop: a classification retry, or --resume-paused after a decision
    gate: str | None = None  # job_failure.resume_gate: 'authorize' once an exact job retry is validated
    retained_report: object = None  # an inspected Investigator report on offer, never authority to adopt it
    decision_action: bool = False  # an answer, approval, feedback or other decision was given
    information_admitted: bool = False  # AutoResolver admitted corrective information for one continuation
    checked_authority: bool = False  # the held pause's own authority, checked before queued input applies
    issued: dict | None = None  # AutoResolver's current request when that authority was weighed
    released: bool = False  # that authority released the held pause (autocode_stop)


def next_command(state, issued, run_dir, workspace):
    """One concrete operator command for the pause class holding this run.

    --grant-recovery is named only for PAUSED_TIMEOUT_RECOVERY: the CLI rejects
    it for every other pause class, so suggesting it anywhere else is a dead end
    (#301).
    """
    origin = {}
    if issued:
        entry = state.get("resolver", {}).get("human_escalations", {}).get(issued.get("request_id"), {})
        origin = entry.get("identity", {}).get("proposal", {}).get("origin", {}) or {}
    pause = origin.get("pause_status") or state.get("status")
    where = f"--workspace {workspace} --run-dir {run_dir}"
    flags = operational_information.resume_flags(state, pause)
    if flags:
        return f"Next command: autocode {flags} {where}"
    if issued:
        return (
            f"Next command: autocode --resolver-request {issued['request_id']} "
            f"--resolver-token {issued['request_token']} --resolver-response provide_information "
            f"--resolver-message 'WHAT CHANGED' {where}"
        )
    return f"Next command: autocode --resume-paused {where}"


def stale_result(state, result, revision):
    """True when a recovered result is bound to another contract, task or tree (#302)."""
    contract = state.get("goal_contract") or {}
    task = state.get("current_task") or {}
    return (
        (result.get("contract_revision") is not None and result.get("contract_revision") != contract.get("revision"))
        or (
            result.get("contract_hash") and contract.get("hash") and result.get("contract_hash") != contract.get("hash")
        )
        or (result.get("task_id") and task.get("id") and result.get("task_id") != task.get("id"))
        or (result.get("source_revision") and result.get("source_revision") != revision)
    )


def discard_stale_recovered(state, run_dir, record):
    """Set aside stale recovered values and schedule one fresh attempt (#302)."""
    stage = record.get("original_stage") or str(record.get("stage", "")).removesuffix("_report_repair")
    originals = records.archive_rejected_stage(
        state,
        run_dir,
        record,
        "Recovered result is bound to another contract, task or source revision; stale values discarded",
    )
    state.setdefault("sessions", {}).pop(record.get("route_role", record.get("role")), None)
    state.update(
        status="RUNNING", phase="PLANNING" if planning.is_planning(state, stage) else "EXECUTING", next_stage=stage
    )
    state.pop("stop_reason", None)
    records.write_json(run_dir / "state.json", state)  # before the unlink: a failed save keeps the originals (#1115)
    for artifact in originals:
        Path(artifact).unlink(missing_ok=True)


def honor_stops(runner, args, parser, state, state_path, run_dir, workspace, decided):
    """Stop for an applied or pending stop, and refuse a bound classification retry mixed with other actions."""
    # An applied durable stop is terminal: no recovery, user action, answer,
    # approval, feedback or resume may relaunch a stopped run or complete it.
    if stop.applied_stop(state) is not None:
        if stop.assert_stopped(state):
            runner.write_json(state_path, state)
        print(f"{state['status']}: {state['stop_reason']}")
        return 2
    if (
        isinstance(args.job_retry_token, str)
        and args.job_retry_token.startswith("bc:")
        and any(
            (
                *runner.cli_args.user_actions(args).values(),
                args.resolver_response,
                args.revise_figma_manifest,
                args.grant_recovery is not None,
                args.retry_report,
                args.diagnose_failed_stage,
                args.retry_builder,
                args.accept_source_edit,
            )
        )
    ):
        print(
            "Input rejected: Run the bound classification retry separately from other user or recovery actions.",
            file=sys.stderr,
        )
        return 2
    active = state.get("active_stage") or {}
    if (
        (
            getattr(args, "recover_job_report", None) is not None
            or (active.get("stage") == "investigate_bug" and active.get("output_mode") == "report_file")
        )
        and stop.pending_stop(run_dir) is not None
        and runner.consume_interventions(state, run_dir, workspace)
    ):
        print(f"{state['status']}: {state['stop_reason']}")
        return 2
    # A pause intervention that landed on a held run, or a request it stranded, leaves that pause in
    # force: the checks below apply its own authority, never the generic resume (#486).
    if (
        args.resume_paused and stop.resume_interrupted(state, runner.now())
    ) or resolver_human.release_stranded_operational(state):
        runner.write_json(state_path, state)


def prepare(runner, args, parser, state, state_path, run_dir, workspace, decided):
    """Migrate the saved run, apply this invocation's settings and queued input, then --retry-builder."""
    released, issued = decided.released, decided.issued
    if state.get("version", 2) < 3:
        backup = run_dir / "state.pre-v3.json"
        if not backup.exists():
            runner.write_json(backup, state)
        lifecycle.migrate(state, fresh=not args.run_dir)
        runner.write_json(state_path, state)
    if args.workflow:  # after migration: a new run's recognizer is begun there
        try:
            workflows.pin(state, args.workflow)
        except ValueError as error:
            parser.error(str(error))
        runner.write_json(state_path, state)
    if not args.run_dir:
        design_intake.queue(state)
        runner.write_json(state_path, state)
    if args.milestone_checkpoints:
        milestones.activate(state)
        if args.max_milestone_seconds is not None:
            state["settings"]["milestone_checkpoints"]["max_seconds"] = args.max_milestone_seconds
        runner.write_json(state_path, state)
    if milestones.apply_queued_activation(state, run_dir):
        print(
            f"Run: {run_dir}\nMilestone checkpoints enabled at a safe boundary; continuing with independent validation.",
            flush=True,
        )
    try:
        if runner.consume_interventions(state, run_dir, workspace, released=released):
            print(f"{state['status']}: {state['stop_reason']}")
            if args.retry_builder:
                print(
                    "--retry-builder was not applied: queued input was applied first, so it was not checked.",
                    file=sys.stderr,
                )
            return 2
    except interventions.InterventionError as error:
        raise support.Paused("PAUSED_INTERVENTION_ACK", str(error)) from error
    print(f"Run: {run_dir}", flush=True)
    if args.migrate_only:
        print("Migrated to an unapproved draft; saved work retained; no agent launched")
        return 0
    try:
        if args.retry_builder and not dispatch.try_request_retry(state, run_dir, args.retry_builder, issued=issued):
            return 2
    except support.Paused as error:
        if not getattr(error, "quota_worker", None):
            raise
        # A refused member with no question open: its stop, collected again, asks the question again (#541).
        member_stop.ask_again(state, error, at=runner.now())
        resolver_runtime.record_operational_exhaustion(runner, state, run_dir, error)
        runner.write_json(state_path, state)
        print(lifecycle.render(state) + "\nNo provider launched.")
        return 2


def admit(runner, args, parser, state, state_path, run_dir, workspace, decided):
    """Stop at a completed or waiting run; otherwise make the run ready for the build loop.

    It runs only when no user decision was given: action_decisions.apply stops for every one.
    """
    if state["status"] == "TASK_COMPLETE":
        runner.recheck_completion(state, workspace, run_dir=run_dir)
        if state["status"] != "TASK_COMPLETE":
            runner.write_json(state_path, state)
    if state["status"] == "TASK_COMPLETE":
        print(jobs.render(state, goals.render_completion) + worktrees.deliver(state, workspace))
        return 0
    if state["status"] in ("WAITING_FOR_USER", "AWAITING_GOAL_APPROVAL"):
        if args.chat:
            if not runner.chat_checkpoint(state, run_dir):
                runner.write_json(state_path, state)
                return 2
            runner.write_json(state_path, state)
        else:
            rendered = lifecycle.present(state, run_dir)
            runner.write_json(state_path, state)
            print(rendered)
            return 2
    if state["status"] == "PAUSED_PLANNING_BUDGET" and (
        runner.recover_default_budget(state, run_dir, workspace, "planning_review_call_limit")
        or resolver_runtime.operational_boundary(runner, state, run_dir, workspace)
    ):
        state.update(status="RUNNING", phase="PLANNING")
        state.pop("stop_reason", None)
        runner.write_json(state_path, state)
    if state["status"] != "RUNNING":
        if state["status"] == "PAUSED_COMPONENT_PLAN":
            print(f"{state['status']}: {state.get('stop_reason', '')}")
            print(run_finder.continue_hint(run_dir, state, args.unit))
            return 2
        # A pre-v0.5.4 terminal response with one missing event
        # citation can be repaired without implementation replay.  It
        # is deliberately gated on an explicit resume and exact pins.
        if args.resume_paused and runner.recover_legacy_report_repair(state, run_dir, workspace):
            pass
        elif not args.resume_paused:
            word = "autocode resume" if run_finder.resume_acknowledges(state["status"]) else "--resume-paused"
            print(f"{state['status']}: {state.get('stop_reason', f'explicit resume required: {word}')}")
            return 2
        else:
            stop.acknowledge_pause(state, runner.now())  # never stamps a stop receipt
            state.update(
                status="RUNNING",
                phase="PLANNING"
                if planning.is_planning(state, state["next_stage"])
                else "DISCOVERING"
                if state["next_stage"] == "astra_discovery"
                else "READY_TO_EXECUTE",
            )
            state.pop("stop_reason", None)
    if state.get("pending_questions"):
        raise support.Paused("PAUSED_UNANSWERED_QUESTION", "Pending questions cannot be bypassed by resume")
    if args.resume_paused and action_decisions.revalidate_on_resume(
        state, workspace, review_stage=runner.workflow.review_stage
    ):
        runner.write_json(state_path, state)
    if runner.migrate_opencode_roles(state, run_dir, workspace):
        print("Saved roles now use OpenCode; previous sessions archived and task progress retained.", flush=True)
    try:
        conversation_ingress.require_expected_goal(
            state, getattr(args, "expected_goal_token", None), token_for=goals.token, is_approved=goals.approved
        )
    except ValueError as error:
        print(f"Input rejected: {error}", file=sys.stderr)
        return 2
    if state["settings"].get("engine") == "opencode":
        routes = {
            r: config
            for r, config in state["settings"]["roles"].items()
            if planning.engine_for(state["settings"], r) == "opencode"
        }
        inventory = getattr(args, "_model_inventory", None)
        if inventory is None:
            runner.opencode.check_models(routes, workspace)
        else:
            inventory.check_models(routes)
    if conversation_ingress.record_build_start(
        state, getattr(args, "expected_goal_token", None), token_for=goals.token, is_approved=goals.approved
    ):
        runner.write_json(state_path, state)
    return None


# handle()'s steps, in the order they take precedence. Each returns an exit code to end the
# invocation, or None to go on; one that sets decided.dispatch sends the run straight to the build.
STEPS = (
    honor_stops,  # an applied or pending stop, and a bound retry mixed with other actions
    action_edits.apply,  # a standalone edit: design revision, dependency, report recovery, repair handoff
    action_recovery.gate_failures,  # a classification or job failure, and its exact retry
    action_authority.settle,  # the held pause's own authority, before queued input applies
    action_resolver.answer,  # a model answer or --resolver-response to an operational request
    action_authority.reconsider,  # AutoResolver reconsiders its operational request
    action_recovery.resume,  # --abandon-stage, or an explicit resume's recovery
    prepare,  # migration, settings, queued input, --retry-builder
    action_decisions.apply,  # answers, approvals, feedback and completion
    admit,  # a completed or waiting run stops; anything else is made ready to build
)


def handle(runner, args, parser, state, state_path, run_dir, workspace):
    """Apply this invocation's action to the saved run; return an exit code to stop, or None to build."""
    args._failure_retry_authorization = None  # Invocation-local; saved history is audit data, not credit.
    decided = Decided()
    for step in STEPS:
        code = step(runner, args, parser, state, state_path, run_dir, workspace, decided)
        if code is not None or decided.dispatch:
            return code
    return None
