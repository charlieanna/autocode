"""Setting up the run one autocode invocation acts on.

resolve() turns the command line into a workspace, a run directory and its state: it resumes a saved
run or creates a new one (in its own worktree unless --in-place), and handles the invocations that
end before any lock (--request-milestone-checkpoints, --status, --dry-run). load_locked() runs under
the run lock: it rereads the saved state, applies this invocation's settings, registers the run,
and migrates older state.

Like autocode_run_actions and autocode_build_loop, both are given the runner module and call the
runner's own functions as runner.X, so tests that patch the runner still reach them, and
runner.opencode is the provider _main_body selected between the two. Neither imports autocode.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import uuid
from pathlib import Path

try:
    from . import autocode_figma as figma, autocode_design_manifest as design_manifest
    from . import autocode_task_preflight as task_preflight
    from . import autocode_goals as goals, autocode_protected_oracles as protected_oracles
    from . import autocode_interventions as interventions
    from . import autocode_milestones as milestones
    from . import model_catalogue
    from . import autocode_planning as planning
    from . import autocode_planning_artifacts as planning_artifacts
    from . import autocode_registry as registry
    from . import autocode_regression as regression, autocode_verify as verify
    from . import autocode_resolver_human as resolver_human
    from . import autocode_retired_token_budget as retired_token_budget
    from . import autocode_status_command as status_command
    from . import autocode_recovery_view as recovery_view
    from . import autocode_support as support
    from . import autocode_workspaces as task_workspaces
    from . import autocode_workflows as workflows
except ImportError:
    import autocode_figma as figma, autocode_design_manifest as design_manifest
    import autocode_task_preflight as task_preflight
    import autocode_goals as goals, autocode_protected_oracles as protected_oracles
    import autocode_interventions as interventions
    import autocode_milestones as milestones
    import model_catalogue
    import autocode_planning as planning
    import autocode_planning_artifacts as planning_artifacts
    import autocode_registry as registry
    import autocode_regression as regression, autocode_verify as verify
    import autocode_resolver_human as resolver_human
    import autocode_retired_token_budget as retired_token_budget
    import autocode_status_command as status_command
    import autocode_recovery_view as recovery_view
    import autocode_support as support
    import autocode_workspaces as task_workspaces
    import autocode_workflows as workflows


def resolve(runner, args, parser):
    """Return (workspace, run_dir, state_path, state), or an exit code when the invocation ends here."""
    if getattr(args, "task_preflight", None):
        try:
            args._task_preflight_input = task_preflight.load(args.task_preflight)
        except (OSError, ValueError) as error:
            parser.error(f"Invalid task preflight: {error}")
    if getattr(args, "figma_manifest", None):
        if args.run_dir:
            parser.error("--figma-manifest is a new-run input; saved references are immutable")
        args._design_manifest_input = design_manifest.load(args.figma_manifest)
    if args.ui_run and args.figma_file:
        parser.error("Choose --ui-run or --figma-file")
    if args.run_dir and (args.ui_run or args.figma_review):
        parser.error("Figma inputs and review policy are fixed for a saved run")
    if args.ui_run:
        handoff = figma.load_handoff(args.ui_run)
        args.figma_file = handoff["figma_file"]
        args.task = (args.task or handoff["task"]) + "\n\nAccepted UI brief:\n" + handoff["brief"]
    if args.figma_file:
        args.figma_file = figma.design_url(args.figma_file)
        if args.engine not in (None, "codex"):
            parser.error("Figma implementation uses --engine codex")
        args.engine = "codex"
    elif args.figma_review:
        parser.error("--figma-review requires --figma-file or --ui-run")
    workspace = args.workspace.resolve()
    if args.run_dir:
        if not (workspace / ".git").exists():
            parser.error(f"workspace is not a Git repository: {workspace}")
        run_dir = args.run_dir.resolve()
        state_path = run_dir / "state.json"
        state = runner.read_json(state_path)
        task = state["task"]
        workspace = task_workspaces.resume_workspace(workspace, state)
    else:
        if not args.task:
            parser.error("task is required unless --run-dir is supplied")
        task = args.task
        if not (workspace / ".git").exists():
            if args.dry_run or args.status:
                parser.error(f"workspace is not a Git repository: {workspace}")
            try:
                workspace = task_workspaces.bootstrap(workspace, task)
            except ValueError as error:
                parser.error(str(error))
            # A new task project is already private to this task. Avoid a
            # second hidden worktree so users can find the generated files.
            args.in_place = True
            print(f"Created task project: {workspace}", flush=True)
        if not args.in_place and not args.dry_run and not args.status:
            isolated = task_workspaces.create(workspace, task)
            workspace = Path(isolated["workspace"])
            print(f"Task worktree: {workspace}\nBranch: {isolated['branch']}\nStarting from committed HEAD; the original checkout is unchanged.", flush=True)
        run_dir = workspace / ".autocode" / "runs" / f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{runner.slug(task)}-{uuid.uuid4().hex[:8]}"
        state_path = run_dir / "state.json"
        state = {"version": 2, "target": "code", "task": task, "workspace": str(workspace), "created_at": runner.now(),
                 "iteration": 1, "status": "RUNNING", "sessions": {}, "history": [], "stages": [],
                 "no_progress_batches": 0,
                 "next_stage": "astra_plan", "acceptance_criteria": []}
        isolated = task_workspaces.metadata(workspace)
        if isolated:
            state.update(project_workspace=isolated["project_workspace"], task_branch=isolated["branch"])
        # The revision a bug fix is proven against (autocode_regression).
        state["base_commit"] = (isolated or {}).get("base_commit") or regression.head(workspace)
        if args.ui_run:
            state["ui_run"] = str(args.ui_run.resolve())
        if args.legacy_iteration_ceiling is None and args.max_iterations is not None:
            args.legacy_iteration_ceiling = args.max_iterations

    if state["workspace"] != str(workspace):
        parser.error("workspace differs from checkpoint; use the original --workspace")
    if not run_dir.is_relative_to(workspace / ".autocode" / "runs"):
        parser.error("run-dir must belong to this project's .autocode/runs")
    registry.configure_workspace_storage(workspace)
    if args.request_milestone_checkpoints:
        request = milestones.queue_activation(run_dir, args.max_milestone_seconds)
        print(json.dumps({'queued': True, 'run_dir': str(run_dir), 'request': request,
                          'effect': 'Pause at the next boundary; next launch applies checkpoints without starting a provider'}, indent=2))
        return 0
    if args.status or args.dry_run:
        status_command.render(runner, state, args, workspace, run_dir)
        return 0
    return workspace, run_dir, state_path, state


def load_locked(runner, args, parser, state, state_path, run_dir, workspace):
    """Under the run lock: reread, configure, register and migrate the run; return its state."""
    support.assert_no_legacy_process(run_dir, workspace)
    if args.run_dir:
        # A competing user command may have finished between the first read
        # and lock acquisition. Never overwrite its event with stale state.
        state = runner.read_json(state_path)
        if state["workspace"] != str(workspace):
            parser.error("workspace differs from the locked checkpoint")
        try:
            recovery_view.require_token(state, getattr(args, 'expected_recovery_token', None))
        except ValueError as error:
            parser.error(str(error))
        recovery = state.get("recovery_context") or {}
        archived = (state.get("stages") or [{}])[-1]
        if (args.resume_paused and not state.get("active_stage")
                and state.get("pending_report_repair")
                and archived.get("abandoned") and archived.get("report_only")
                and recovery.get("attempt_id") == runner.attempt_id(archived)):
            state.setdefault("report_repair_archive", []).append({
                "reason": "Reconciled previously abandoned report repair",
                "repair": state.pop("pending_report_repair")})
            runner.write_json(state_path, state)
    # Preserve only this locked invocation's pre-change settings for grant validation.
    args._recovery_grant_settings = copy.deepcopy(state.get("settings"))
    settings = runner.configure(args, state)
    settings = protected_oracles.reconcile(state, settings, args, workspace, run_dir,
        is_test_path=verify.is_test_path,
        discover_command=lambda: (verify.detect_framework(workspace,
                                  python=verify.python_for(state.get("project_workspace") or workspace)) or
                                  verify.Framework("unknown", None)).suite)
    if args.run_dir and args.autoresolver_managed_limits:
        origins = settings.setdefault('budget_origins', {})
        for kind in runner.BUDGET_ARGUMENTS:
            if origins.get(kind) == 'user_explicit':
                origins[kind] = 'resolver_delegated'
    # A new checkpoint must exist before it is registered, so a failed registry
    # update leaves the same run directory available for an explicit retry.
    if not args.run_dir:
        state["settings"] = settings = model_catalogue.choose(settings, runner.opencode, workspace, interactive=args.chat)
        if settings.get('planning_flow') == 'v2':
            if state.get('next_stage') == workflows.STAGE:
                # Recognition still runs first; v2 only moves where the build pipeline starts.
                state['workflow']['then'] = planning.entry_stage(state)
            else:
                state['next_stage'] = planning.entry_stage(state)
        runner.write_json(state_path, state)
    try:
        registry.register_run(workspace, run_dir, state)
    except registry.RegistryError as error:
        message = f"Registry registration failed for {run_dir}: {error}"
        state.update(status="PAUSED_REGISTRY", phase="PAUSED_OR_BLOCKED", stop_reason=message, paused_at=runner.now())
        runner.write_json(state_path, state)
        raise support.Paused("PAUSED_REGISTRY", message) from error
    if args.run_dir and args.autoresolver_managed_limits:
        published = resolver_human.current(state)
        if published and published['scope'] == 'operational_exhaustion':
            proposal = state['resolver']['human_escalations'][published['request_id']]['identity']['proposal']
            kind = proposal['origin'].get('budget', {}).get('kind')
            pause_status = proposal['origin'].get('pause_status')
            if kind and settings.get('budget_origins', {}).get(kind) == 'resolver_delegated':
                if resolver_human.supersede_operational(state,
                        'User delegated this finite harness limit to bounded AutoResolver recovery'):
                    state['_authorized_bound_change'] = {'pause_status': pause_status, 'at': runner.now()}
    if state.get("settings") and settings != state["settings"]:
        published = state.get(resolver_human.PUBLIC) or {}
        entry = state.get('resolver', {}).get('human_escalations', {}).get(published.get('request_id'), {})
        origin = entry.get('identity', {}).get('proposal', {}).get('origin', {})
        paused_for = origin.get('pause_status')
        retiring_token_pause = retired_token_budget.retired_pause(origin)
        if retiring_token_pause and resolver_human.supersede_operational(state, 'Cumulative token budgets were removed'):
            state['_authorized_bound_change'] = {'pause_status': paused_for, 'at': runner.now()}
        relevant = {'PAUSED_ITERATION_LIMIT': ('max_iterations', 'legacy_iteration_ceiling', 'unlimited_iterations'),
                    'PAUSED_TIME_LIMIT': ('max_seconds',),
                    'PAUSED_MILESTONE_TIME_LIMIT': ('max_milestone_seconds',)}
        if any(flag in args._explicit_budget_flags for flag in relevant.get(paused_for, ())):
            if resolver_human.supersede_operational(state, 'Operator explicitly changed the exhausted bound'):
                state['_authorized_bound_change'] = {'pause_status': paused_for, 'at': runner.now()}
        if args.autoresolver_managed_limits and entry.get('identity', {}).get('proposal', {}).get('origin', {}).get('budget', {}).get('kind'):
            kind = entry['identity']['proposal']['origin']['budget']['kind']
            if settings.get('budget_origins', {}).get(kind) == 'resolver_delegated':
                if resolver_human.supersede_operational(state, 'User delegated this finite harness limit to bounded AutoResolver recovery'):
                    state['_authorized_bound_change'] = {'pause_status': paused_for, 'at': runner.now()}
        previous_settings = state["settings"]
        enabling_joint = settings.get("joint_planning") and not previous_settings.get("joint_planning")
        if enabling_joint:
            backup = run_dir / f"state.pre-joint-planning-{uuid.uuid4().hex[:8]}.json"
            runner.write_json(backup, state)
            state.setdefault("planning_migrations", []).append({"at": runner.now(), "backup": str(backup),
                "goal_token": goals.token(state["goal_contract"]), "next_stage": state.get("next_stage"),
                "reason": "Explicitly enabled independent planning; existing work and sessions retained"})
        state.setdefault("configuration_changes", []).append({"at":runner.now(),"previous":state["settings"],"selected":settings,
            "reason":("Cumulative token budgets were removed" if retiring_token_pause else
                      "Run settings updated at a saved stage boundary")})
        state["settings"] = settings
        if enabling_joint and settings.get("engine") == "codex":
            state["goal_contract"].update(approval_status="draft", approval_event=None)
            goals.invalidate(state, "Independent requirements and plan review requested before further execution")
            state.update(status="RUNNING", phase="DISCOVERING", next_stage="requirements_gather",
                         pending_questions=[])
            state.pop("stop_reason", None)
            state.pop("paused_at", None)
        # A grant validates the original request before its writer publishes
        # the corrected settings. Normalizing here would replace that request
        # with a different resolver decision before the grant can be checked.
        if args.grant_recovery is None:
            runner.write_json(state_path, state)
    state["settings"] = settings
    if args.run_dir and settings.get('planning_flow') == 'v2' and planning_artifacts.reconcile_orphans(state, run_dir):
        runner.write_json(state_path, state)
    state["intervention_capability"] = {"supported": True, "version": interventions.INBOX_VERSION}
    if state.get("version",1) == 1:
        backup = run_dir / "state.pre-v2.json"
        if not backup.exists():
            runner.write_json(backup, state)
        state = support.migrate_v1(state, run_dir, workspace, settings, runner.SCHEMA_DIR)
        if not state["stages"] and state["iteration"] == 0:
            state["iteration"] = 1
        runner.write_json(state_path, state)
    return state
