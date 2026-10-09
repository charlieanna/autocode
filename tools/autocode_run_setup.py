"""Setting up the run one autocode invocation acts on.

resolve() turns the command line into a workspace, a run directory and its state: it resumes a saved
run or creates a new one (in its own worktree unless --in-place), and handles the invocations that
end before any lock (--request-milestone-checkpoints, --status, --explain, --dry-run). load_locked() runs under
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
    from . import autocode_checkout_lock as checkout_lock
    from . import autocode_containment_policy as containment_policy
    from . import autocode_design_manifest as design_manifest
    from . import autocode_figma as figma
    from . import autocode_goal_lifecycle as lifecycle
    from . import autocode_goals as goals
    from . import autocode_interventions as interventions
    from . import autocode_job_route as job_route
    from . import autocode_launch_inputs as launch_inputs
    from . import autocode_milestones as milestones
    from . import autocode_pause_authority as pause_authority
    from . import autocode_planning as planning
    from . import autocode_planning_artifacts as planning_artifacts
    from . import autocode_protected_oracles as protected_oracles
    from . import autocode_quota_route as quota_route
    from . import autocode_recovery_view as recovery_view
    from . import autocode_registry as registry
    from . import autocode_regression as regression
    from . import autocode_resolver_human as resolver_human
    from . import autocode_retired_token_budget as retired_token_budget
    from . import autocode_status_command as status_command
    from . import autocode_stop as stop
    from . import autocode_support as support
    from . import autocode_task_preflight as task_preflight
    from . import autocode_test_root as test_roots
    from . import autocode_verify as verify
    from . import autocode_worker_quota as worker_quota
    from . import autocode_workflows as workflows
    from . import autocode_workspaces as task_workspaces
    from . import model_catalogue
except ImportError:
    import autocode_checkout_lock as checkout_lock
    import autocode_containment_policy as containment_policy
    import autocode_design_manifest as design_manifest
    import autocode_figma as figma
    import autocode_goal_lifecycle as lifecycle
    import autocode_goals as goals
    import autocode_interventions as interventions
    import autocode_job_route as job_route
    import autocode_launch_inputs as launch_inputs
    import autocode_milestones as milestones
    import autocode_pause_authority as pause_authority
    import autocode_planning as planning
    import autocode_planning_artifacts as planning_artifacts
    import autocode_protected_oracles as protected_oracles
    import autocode_quota_route as quota_route
    import autocode_recovery_view as recovery_view
    import autocode_registry as registry
    import autocode_regression as regression
    import autocode_resolver_human as resolver_human
    import autocode_retired_token_budget as retired_token_budget
    import autocode_status_command as status_command
    import autocode_stop as stop
    import autocode_support as support
    import autocode_task_preflight as task_preflight
    import autocode_test_root as test_roots
    import autocode_verify as verify
    import autocode_worker_quota as worker_quota
    import autocode_workflows as workflows
    import autocode_workspaces as task_workspaces
    import model_catalogue

# Recovery actions that answer or act on the published operational request themselves; a settings change
# that comes with one leaves that request for the action to check.
OTHER_RECOVERY = ('retry_failed_stage', 'retry_report', 'retry_builder', 'abandon_stage', 'diagnose_failed_stage',
                  'resolver_response')  # and --grant-recovery
# Pauses a resume acknowledges when it reasserts a bound that admits the used amount, even one
# already saved: (pause status, settings limit, explicit flag, used counter, superseded reason).
REASSERTABLE_BOUNDS = (
    ('PAUSED_TIME_LIMIT', 'max_seconds', 'max_seconds', 'active_seconds',
     'Operator explicitly resumed with an available active-time limit'),
    ('PAUSED_NO_PROGRESS', 'no_progress_batches', 'no_progress_limit', 'no_progress_batches',
     'Operator explicitly resumed with a no-progress limit above the retained count'),
)


def resolve(runner, args, parser):
    """Return (workspace, run_dir, state_path, state), or an exit code when the invocation ends here."""
    if getattr(args, "task_preflight", None):
        try:
            args._task_preflight_input = task_preflight.load(args.task_preflight)
        except (OSError, ValueError) as error:
            parser.error(f"Invalid task preflight: {error}")
    if args.revise_figma_manifest and (not args.run_dir or not args.expected_design_hash or not args.design_change_reason
                                     or args.status or args.explain or args.dry_run):
        parser.error("--revise-figma-manifest requires a stopped --run-dir, --expected-design-hash and --design-change-reason")
    if getattr(args, "figma_manifest", None):
        if args.run_dir:
            parser.error("--figma-manifest is a new-run input; saved references are immutable")
        args._design_manifest_input = design_manifest.load(args.figma_manifest)
    if args.figma_additional_file and not args.figma_file:
        parser.error("--figma-additional-file requires --figma-file")
    if args.run_dir and args.figma_additional_file:
        parser.error("Native references are fixed for a saved run")
    for reference in args.figma_additional_file:
        figma.design_url(reference)
    if getattr(args, "test_root", None) is not None:
        if args.run_dir:
            parser.error("--test-root is fixed when a run starts")
        try:
            args.test_root = test_roots.normalize(args.test_root)
        except ValueError as error:
            parser.error(str(error))
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
        try:
            lifecycle.require_supported_checkpoint(state)
        except support.Paused as error:
            parser.error(error.args[1] if len(error.args) > 1 else str(error))
        task = state["task"]
        workspace = task_workspaces.resume_workspace(workspace, state)
    else:
        if not args.task:
            # Reached only when a new-run input (--in-place, --figma-file, ...) turned off finding a saved run.
            parser.error('a task is needed to start a run, for example: autocode "Build a greeting CLI". '
                         "To continue a saved run, run autocode without new-run options from its project "
                         "or task worktree, or name it with --run-dir")
        task = args.task
        created = fresh = False
        if not (workspace / ".git").exists():
            if args.dry_run or args.status or args.explain:
                parser.error(f"workspace is not a Git repository: {workspace}")
            try:
                workspace = task_workspaces.bootstrap(workspace, task)
            except ValueError as error:
                parser.error(str(error))
            # A new task project is already private to this task. Avoid a
            # second hidden worktree so users can find the generated files.
            args.in_place = created = True
            print(f"Created task project: {workspace}", flush=True)
        if not args.in_place and not args.dry_run and not args.status and not args.explain:
            isolated = task_workspaces.create(workspace, task)
            workspace, fresh = Path(isolated["workspace"]), True
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
        # The revision a bug fix is proven against (autocode_regression). A project or task worktree
        # created just now is its HEAD; in any other checkout, including an earlier task's worktree,
        # the files it holds uncommitted or untracked at launch are part of it.
        if created or fresh or args.dry_run or args.status or args.explain:
            state["base_commit"] = (isolated or {}).get("base_commit") or regression.head(workspace)
        else:
            state["base_commit"] = launch_base(workspace, run_dir, parser)
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
    if args.status or args.explain or args.dry_run:
        status_command.render(runner, state, args, workspace, run_dir)
        return 0
    return workspace, run_dir, state_path, state


def launch_base(workspace, run_dir, parser):
    """A new in-place run's base_commit (regression.launch_base), taken while this run holds the
    checkout. None while another run's agents hold it: its unfinished edits are not this run's
    start, so the build loop records the base once this run holds the checkout."""
    task_workspaces.keep_out_of_git(workspace)
    try:
        with checkout_lock.exclusive(workspace, run_dir):
            return regression.launch_base(workspace, run_dir)
    except checkout_lock.CheckoutBusy:
        return None
    except RuntimeError as error:
        parser.error(f"cannot record the checkout's uncommitted and untracked files as the run's start: {error}")


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
    recovering_report = getattr(args, 'recover_job_report', None) is not None
    settings = runner.configure(args, copy.deepcopy(state) if recovering_report else state)
    if recovering_report:
        if settings != state.get('settings'):
            parser.error('Provider configuration or limits changed; report recovery requires unchanged saved settings')
        return state  # Standalone adoption must not save configuration, migrations or other actions first.
    # Before any stage: refuse a run whose built-in OpenCode stages cannot be kernel-contained, or save
    # the explicit --allow-uncontained-tools opt-out (#413).
    containment_policy.configure(state, settings, allow=bool(getattr(args, "allow_uncontained_tools", False)),
                                 configured_tool=getattr(runner.opencode, "CONFIGURED", False),
                                 workspace=workspace, now=runner.now)
    if not args.run_dir and not state.get("project_workspace"):
        launch_inputs.record(state, workspace, run_dir)
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
    # A response can consume the request before a bound change is applied, and a plain resume
    # after saving the change asks again under the unchanged settings. Reasserting that saved
    # bound is also explicit authority, but only with headroom (0 removes the bound).
    for pause, limit_key, flag, used_key, reason in REASSERTABLE_BOUNDS:
        limit = settings.get('limits', {}).get(limit_key)
        if not (args.resume_paused and flag in args._explicit_budget_flags and limit is not None
                and (limit == 0 or state.get(used_key, 0) < limit)):
            continue
        published = resolver_human.current(state)
        entry = state.get('resolver', {}).get('human_escalations', {}).get(
            published['request_id'], {}) if published else {}
        origin = entry.get('identity', {}).get('proposal', {}).get('origin', {})
        live_pause = (published and published['scope'] == 'operational_exhaustion'
                      and origin.get('pause_status') == pause)
        consumed_pause = (not state.get(resolver_human.PUBLIC) and not state.get(resolver_human.PRIVATE)
                          and state.get('status') == pause)
        if (live_pause and resolver_human.supersede_operational(state, reason)) or consumed_pause:
            state['_authorized_bound_change'] = {'pause_status': pause, 'at': runner.now()}
    if state.get("settings") and settings != state["settings"]:
        published = state.get(resolver_human.PUBLIC) or {}
        entry = state.get('resolver', {}).get('human_escalations', {}).get(published.get('request_id'), {})
        origin = entry.get('identity', {}).get('proposal', {}).get('origin', {})
        # A --<role>-model change under a quota-stopped, still uncertain attempt is refused (#184),
        # naming --answer only when the pending request asks that model question.
        refusal = quota_route.resume_refusal(state, state["settings"], settings, failure_status=support.failure_status,
                                             abandoning=args.abandon_stage, origin=origin,
                                             questions=published.get('questions') if entry.get('status') == 'pending' else None)
        # A stopped job's model answer changes only that model; nothing else is saved with it (#463).
        refusal = refusal or job_route.settings_refusal(args, state, settings)
        # Check a parallel retry before saving the settings beside it. The actual retry repeats
        # these checks. A collected refusal (Paused, #541) still asks its question there; only a
        # stop carrying its member payload proceeds there. Other stops reject before settings
        # are saved. A parent-only Builder route change cannot reach its child.
        if not refusal and args.retry_builder and state.get('next_stage') != 'terra':
            try:
                member = runner.dispatch.member_retry_refusal(state, args.retry_builder)
            except support.Paused as error:
                member = error
            rejected = isinstance(member, ValueError) or (
                isinstance(member, support.Paused) and not getattr(member, 'quota_worker', None))
            reason = (str(member) if rejected else worker_quota.retry_route_refusal(
                state, args.retry_builder, state["settings"], settings,
                asked=worker_quota.asked_member(state, resolver_human.current(state))))
            if reason:
                refusal = (reason.rstrip('.') + ". Nothing was saved, including this invocation's "
                           "settings; they are saved by the same command without --retry-builder.")
        if refusal:
            parser.error(refusal)
        paused_for = origin.get('pause_status')
        retiring_token_pause = retired_token_budget.retired_pause(origin)
        if retiring_token_pause and resolver_human.supersede_operational(state, 'Cumulative token budgets were removed'):
            state['_authorized_bound_change'] = {'pause_status': paused_for, 'at': runner.now()}
        # Match the paused budget by kind as well as status: an operational-exhaustion
        # request after recovery burn-out may name a different origin.pause_status than
        # the bound the operator is raising (#301). The same table decides whether the
        # flag acknowledges the pause (run_actions.explicit_recovery_requested).
        if pause_authority.changes_held_bound(args._explicit_budget_flags, origin):
            if resolver_human.supersede_operational(state, 'Operator explicitly changed the exhausted bound'):
                state['_authorized_bound_change'] = {'pause_status': paused_for, 'at': runner.now()}
        if args.autoresolver_managed_limits and entry.get('identity', {}).get('proposal', {}).get('origin', {}).get('budget', {}).get('kind'):
            kind = entry['identity']['proposal']['origin']['budget']['kind']
            if settings.get('budget_origins', {}).get(kind) == 'resolver_delegated':
                if resolver_human.supersede_operational(state, 'User delegated this finite harness limit to bounded AutoResolver recovery'):
                    state['_authorized_bound_change'] = {'pause_status': paused_for, 'at': runner.now()}
        retained = None
        # Preserve actions that validate the operational request themselves. Other
        # settings writes retire its stale binding without authorizing continuation.
        if (published.get('scope') == 'operational_exhaustion' and paused_for
                and args.grant_recovery is None
                and not any(getattr(args, name, None) for name in OTHER_RECOVERY)):
            withdrawn = resolver_human.supersede_operational(
                state, 'Settings changed without changing the exhausted bound')
            # Every pause, not only the active-time limit (#394): the request is asked again under
            # the new settings, never left for the writer boundary to rebuild as a legacy blocker.
            if (withdrawn and not state.get('_authorized_bound_change')
                    and not pause_authority.changes_held_bound(args._explicit_budget_flags, origin)):
                cause = (entry.get('identity', {}).get('proposal', {}).get('request') or {}).get('discovered')
                retained = support.Paused(paused_for, cause or 'Pause retained; other settings do not acknowledge it')
        previous_settings = state["settings"]
        enabling_joint = settings.get("joint_planning") and not previous_settings.get("joint_planning")
        if enabling_joint:
            contract = state.get("goal_contract")  # Recognition can pause before the first draft.
            backup = run_dir / f"state.pre-joint-planning-{uuid.uuid4().hex[:8]}.json"
            runner.write_json(backup, state)
            state.setdefault("planning_migrations", []).append({"at": runner.now(), "backup": str(backup),
                "goal_token": goals.token(contract) if contract else None, "next_stage": state.get("next_stage"),
                "reason": "Explicitly enabled independent planning; existing work and sessions retained"})
        state.setdefault("configuration_changes", []).append({"at":runner.now(),"previous":state["settings"],"selected":settings,
            "reason":("Cumulative token budgets were removed" if retiring_token_pause else
                      "Run settings updated at a saved stage boundary")})
        # A --<role>-model change while that role is stopped on quota is a recorded route assignment (#184).
        quota_route.record_resume_change(state, previous_settings, settings, failure_status=support.failure_status,
                                         at=runner.now(), cross_check=runner.dispatch.enforce_cross_model_verification,
                                         configured_tool=getattr(runner.opencode, 'CONFIGURED', False))
        state["settings"] = settings
        if enabling_joint and settings.get("engine") == "codex":
            if contract:
                contract.update(approval_status="draft", approval_event=None)
            goals.invalidate(state, "Independent requirements and plan review requested before further execution")
            state.update(next_stage="requirements_gather", pending_questions=[])
            # Enabling it is a settings write: planning restarts once an operational pause holding the
            # run is released by its own authority, never past it (#486 review).
            held = stop.interrupted_pause(state)
            if not (held and pause_authority.operational(held['status'])):
                state.update(status="RUNNING", phase="DISCOVERING")
                state.pop("stop_reason", None)
                state.pop("paused_at", None)
        # A grant validates the original request before its writer publishes
        # the corrected settings. Normalizing here would replace that request
        # with a different resolver decision before the grant can be checked.
        if retained:
            runner.resolver_runtime.record_operational_exhaustion(runner, state, run_dir, retained)
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
