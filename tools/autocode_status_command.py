"""CLI status rendering, with controller services supplied by the caller."""
import json
import sys

try:
    from . import autocode_verification_inspection as verification
    from . import autocode_progress_view as progress_view
except ImportError:
    import autocode_verification_inspection as verification
    import autocode_progress_view as progress_view


def render(runner, state, args, workspace, run_dir):
    active = state.get("active_stage")
    worker_state = runner.processes.recorded_worker_state(active) if active else None
    active_finished = runner.stage_completed(state, active) if active else None
    stale = bool(active and state.get("status") == "RUNNING"
                 and worker_state and worker_state.get("checked")
                 and not worker_state.get("alive") and not active_finished)
    check = state.get("active_runner_check")
    check_workers = runner.processes.recorded_worker_state(check) if check else None
    stale_check = bool(check and not active and state.get("status") == "RUNNING"
                       and check_workers.get("checked") and not check_workers.get("alive"))
    current = runner.support.snapshot(workspace) if state["status"] == "TASK_COMPLETE" else None
    completion_current = (runner.completion_gate.completion_ready(state, state.get("final_decision", {}), current)
                          if state["status"] == "TASK_COMPLETE" else None)
    public_view = runner.run_view.view(state)
    if getattr(args, 'inspect_evidence', False):
        contract = state.get('goal_contract') or {}
        criteria = (contract.get('body') or {}).get('acceptance_criteria') or []
        missing = runner.goals.missing_human_reviews(state) if contract else []
        accepted = [row['id'] for row in criteria if row.get('human_review') and row['id'] not in missing]
        inspected = verification.inspect(state, workspace, snapshot=runner.support.snapshot,
            read_state=lambda: runner.read_json(run_dir / 'state.json'), accepted_human_ids=accepted, initial_snapshot=current)
        public_view['verification'] = inspected
        if completion_current is not None and (inspected['freshness'] != 'current'
                or inspected.get('inspected_source_revision') != current['revision']):
            completion_current = False
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
        print(f"STALE CHECKPOINT: saved status is RUNNING but the recorded "
              f"{active.get('stage')} workers are gone and no terminal report was saved. "
              "AutoResolver must reconcile the retained attempt before any further provider call.", file=sys.stderr)
    if stale_check:
        print("STALE CHECKPOINT: the runner executing the regression check is gone. "
              "Inspect the retained test output before resuming the run.", file=sys.stderr)
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
