"""CLI status rendering, with controller services supplied by the caller."""

try:
    from . import autocode_launch_inputs as launch_inputs
    from . import autocode_source_scope as source_scope
except ImportError:
    import autocode_launch_inputs as launch_inputs
    import autocode_source_scope as source_scope

import json
import sys

try:
    from . import autocode_job_report_recovery as job_report_recovery
    from . import autocode_progress_view as progress_view
    from . import autocode_verification_inspection as verification
    from . import autocode_verification_preparation as preparation
except ImportError:
    import autocode_job_report_recovery as job_report_recovery
    import autocode_progress_view as progress_view
    import autocode_verification_inspection as verification
    import autocode_verification_preparation as preparation


def render(runner, state, args, workspace, run_dir):
    if getattr(args, "explain", False):
        from autocode_stop_explanations import explain
        text = explain(state.get("status") or "", stop_reason=state.get("stop_reason") or "")
        print(text["what_happened"])
        print()
        print(text["what_it_means"])
        print()
        print(text["what_the_command_does"])
        return
    active = state.get("active_stage")
    worker_state = runner.processes.recorded_worker_state(active) if active else None
    liveness = runner.supervision.observe(active["supervision"]) if active and active.get("supervision") else None
    active_finished = runner.stage_completed(state, active) if active else None
    stale = bool(active and state.get("status") == "RUNNING"
                 and worker_state and worker_state.get("checked")
                 and not worker_state.get("alive") and not active_finished)
    check = state.get("active_runner_check")
    check_workers = runner.processes.recorded_worker_state(check) if check else None
    check_liveness = runner.supervision.observe(check["supervision"]) if check and check.get("supervision") else None
    stale_check = bool(check and not active and state.get("status") == "RUNNING"
                       and check_workers.get("checked") and not check_workers.get("alive"))
    strict_visual = runner.visual_runtime.requested(state)
    current = source_scope.snapshot(workspace, state, base_snapshot=runner.support.snapshot) if state["status"] == "TASK_COMPLETE" or strict_visual or state.get('settings', {}).get('design_manifest') else None
    visual_acceptance = (runner.visual_runtime.projection(state, current_snapshot=current)
                         if (state.get('settings', {}).get('design_manifest') or strict_visual) and current else None)
    completion_current = (runner.completion_gate.completion_ready(state, state.get("final_decision", {}), current)
                          if state["status"] == "TASK_COMPLETE" else None)
    if completion_current:
        try:
            launch_inputs.guard(state, workspace, run_dir)
        except ValueError:
            completion_current = False
    if completion_current is True and (state.get('settings', {}).get('design_manifest') or strict_visual):
        completion_current = runner.visual_runtime.completion_check(state, current_snapshot=current)['passed']
    inspected = None
    if getattr(args, 'inspect_evidence', False):
        contract = state.get('goal_contract') or {}
        criteria = (contract.get('body') or {}).get('acceptance_criteria') or []
        missing = runner.goals.missing_human_reviews(state) if contract else []
        accepted = [row['id'] for row in criteria if row.get('human_review') and row['id'] not in missing]
        inspected = verification.inspect(state, workspace, snapshot=lambda root: source_scope.snapshot(root, state, base_snapshot=runner.support.snapshot),
            read_state=lambda: runner.read_json(run_dir / 'state.json'), accepted_human_ids=accepted, initial_snapshot=current)
        try:
            launch_inputs.guard(state, workspace, run_dir)
        except ValueError as error:
            inspected = verification.project(state,
                current_revision=inspected.get('inspected_source_revision'), evidence_matches=False,
                accepted_human_ids=accepted)
            inspected['reasons'].append(str(error))
        if completion_current is not None and (inspected['freshness'] != 'current'
                or inspected.get('inspected_source_revision') != current['revision']):
            completion_current = False
    public_view = runner.run_view.view(state, completion_current=completion_current,
                                       visual_acceptance=visual_acceptance, liveness=liveness,
                                       runner_check_liveness=check_liveness,
                                       verification_obligation=preparation.frontier(run_dir / 'check-replay' / 'obligations', check),
                                       stale_report_repair=runner.stale_report_repair(state, workspace) is not None)
    public_view['job_report_recovery'] = job_report_recovery.offer(runner, state, run_dir, workspace)
    if inspected is not None:
        public_view['verification'] = inspected
    supervision_state = public_view.get('liveness', {})
    lost_supervisor = supervision_state.get('kind') in ('unsupervised', 'interrupted')
    if active and not active.get('finished_at'):
        lost_supervisor = lost_supervisor or any(
            supervision_state.get(key, {}).get('checked') and supervision_state[key].get('alive') is False
            for key in ('owner', 'keeper'))
    stale = stale or bool(active and state.get('status') == 'RUNNING' and lost_supervisor)
    check_supervision_state = (public_view.get('runner_check') or {}).get('liveness', {})
    lost_check_supervisor = check_supervision_state.get('kind') in ('unsupervised', 'interrupted')
    if check and check.get('supervision') and check_supervision_state.get('kind') != 'stopped':
        lost_check_supervisor = lost_check_supervisor or any(
            check_supervision_state.get(key, {}).get('checked') and check_supervision_state[key].get('alive') is False
            for key in ('owner', 'keeper'))
    stale_check = stale_check or bool(check and state.get('status') == 'RUNNING' and lost_check_supervisor)
    checkpoint = runner.milestones.summary(state)
    stage = (active or {}).get("stage") or state.get("next_stage")
    next_action = (f"AutoResolver must reconcile retained attempt {runner.attempt_id(active)} before any provider call"
                   if stale else "Inspect the retained runner check and resume the run" if stale_check else None)
    # A stale runner check stopped before the next stage was launched: name the check, not that stage.
    stage_name = (f"Runner check ({str(check.get('stage') or 'check').replace('_', ' ')})" if stale_check
                  else runner.autocode_status.role_name(stage, state) if stage else None)
    public_view["progress"] = progress_view.progress(
        state, accepted=checkpoint["accepted_milestones"], needs=public_view["needs"],
        reviewed=reviewed_criteria(runner, state), stopped=next_action, finished=bool(active_finished),
        stage=stage_name)
    if public_view.get("progressive") and completion_current is not None:
        public_view["progressive"]["current_whole_product_proof"] = {
            "verified": completion_current is True,
            "status": "current" if completion_current is True else "stale_or_incomplete",
            "source_revision": current["revision"],
            "reason": "Checked against the current checkout by the full completion gate.",
        }
        if completion_current is True:
            public_view["progressive"]["outstanding_product_criteria"] = []
    if stale:
        print(f"STALE CHECKPOINT: saved status is RUNNING but the retained "
              f"{active.get('stage')} attempt has no current supervised result. "
              "AutoResolver must reconcile the retained attempt before any further provider call.", file=sys.stderr)
    if stale_check:
        print("STALE CHECKPOINT: the retained runner check has no current supervised result. "
              "Inspect its command ownership receipt and output before resuming the run.", file=sys.stderr)
    print(json.dumps({"run_dir":str(run_dir), "workspace":str(workspace), "project_workspace":state.get("project_workspace", str(workspace)), "task_branch":state.get("task_branch"), "status":state["status"], "iteration":state["iteration"],
                      "stale":stale or stale_check,
                      "next_action": next_action,
                      "runner_check_workers":check_workers,
                      "active_stage_workers":worker_state,
                      "active_stage_finished":active_finished,
                      "engine":state.get("settings", {}).get("engine", "codex" if args.run_dir else args.engine or runner.DEFAULT_ENGINE),
                      "next_stage":state.get("next_stage", "legacy; inspect saved finals"), "sessions":state["sessions"],
                      "phase":state.get("phase", "DISCOVERING" if not args.run_dir else "migration_required"),
                      "contract_token":runner.goals.token(state["goal_contract"]) if state.get("goal_contract") else None,
                      "current_task":state.get("current_task"), "last_decision":state.get("last_decision"),
                       "settings":state.get("settings"), "active_stage":active,
                       "reasoning_escalations":state.get("reasoning_escalations", []),
                       "attempt_id":runner.attempt_id(active) if active else None,
                       "completion_current":completion_current,
                       "milestone_checkpoint": checkpoint,
                       "orchestration_batch": state.get("orchestration_batch"),
                       "unit_handoffs": state.get("unit_handoffs", {}),
                       "milestone_activation_pending": (run_dir / 'milestone-checkpoints-requested.json').exists(),
                       "interventions": runner.intervention_metadata(workspace, run_dir, state),
                        "view": {**public_view, "delivery": runner.dependency.export(state, current, completion_current)},
                       **runner.resolver_human.projection(state)}, indent=2))


def reviewed_criteria(runner, state):
    """Human-review criteria whose review receipt is valid for the current validation."""
    contract = state.get("goal_contract") or {}
    try:
        human = {row["id"] for row in contract["body"]["acceptance_criteria"] if row.get("human_review")}
        return human - set(runner.goals.missing_human_reviews(state))
    except (KeyError, TypeError, AttributeError, ValueError):
        return set()  # Legacy or partial plans carry no review binding to show.
