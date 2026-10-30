"""CLI status rendering, with controller services supplied by the caller."""

from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_answer_output as answer_output
    from . import autocode_chat_activity as chat_activity
    from . import autocode_chat_log as chat_log
    from . import autocode_chat_progress as chat_progress
    from . import autocode_cost as cost
    from . import autocode_escalation_view as escalation_view
    from . import autocode_launch_inputs as launch_inputs
    from . import autocode_review_result as review_result
    from . import autocode_source_scope as source_scope
    from . import autocode_status_summary as status_summary
except ImportError:
    import autocode_answer_output as answer_output
    import autocode_chat_activity as chat_activity
    import autocode_chat_log as chat_log
    import autocode_chat_progress as chat_progress
    import autocode_cost as cost
    import autocode_escalation_view as escalation_view
    import autocode_launch_inputs as launch_inputs
    import autocode_review_result as review_result
    import autocode_source_scope as source_scope
    import autocode_status_summary as status_summary

import json
import sys
from pathlib import Path

try:
    from . import autocode_attention_status as attention_status
    from . import autocode_controller_owner as controller_owner
    from . import autocode_conversation as conversation_protocol
    from . import autocode_dev_server as dev_server
    from . import autocode_interventions as interventions
    from . import autocode_job_report_recovery as job_report_recovery
    from . import autocode_liveness as liveness_policy
    from . import autocode_pause_authority as pause_authority
    from . import autocode_pause_offer as pause_offer
    from . import autocode_progress_view as progress_view
    from . import autocode_stop_explanations as stop_explanations
    from . import autocode_util as util
    from . import autocode_verification_inspection as verification
    from . import autocode_verification_preparation as preparation
    from . import autocode_worker_binding as worker_binding
except ImportError:
    import autocode_attention_status as attention_status
    import autocode_controller_owner as controller_owner
    import autocode_conversation as conversation_protocol
    import autocode_dev_server as dev_server
    import autocode_interventions as interventions
    import autocode_job_report_recovery as job_report_recovery
    import autocode_liveness as liveness_policy
    import autocode_pause_authority as pause_authority
    import autocode_pause_offer as pause_offer
    import autocode_progress_view as progress_view
    import autocode_stop_explanations as stop_explanations
    import autocode_util as util
    import autocode_verification_inspection as verification
    import autocode_verification_preparation as preparation
    import autocode_worker_binding as worker_binding


# How often one --status re-reads a checkpoint that changed while its controller receipt was inspected.
SAMPLE_RETRIES = 4


def sample(runner, state: "RunState", run_dir, workspace):
    """The saved checkpoint and its controller receipt, read together.

    ``--status`` holds no lock. A controller that finishes a stage and exits between the state read
    and the receipt read would otherwise be shown as ended at a checkpoint that no longer exists:
    ``needs`` offers a continue while ``checkpoint_token`` names the earlier state, and the worker
    refuses that exact offer as stale. When the saved state changed during the inspection, read it
    again and inspect again, so every field of one view describes the same checkpoint.
    """
    path = Path(run_dir) / "state.json"
    token, owner = util.digest(state), None
    for _ in range(SAMPLE_RETRIES):
        if not path.is_file():
            return state, token, None
        owner = controller_owner.inspect(state, run_dir, workspace)
        try:
            saved = runner.read_json(path)
        except (OSError, ValueError):
            return state, token, owner  # a checkpoint being replaced; keep the one that was read
        if not isinstance(saved, dict) or util.digest(saved) == token:
            return state, token, owner
        state, token = cast("RunState", saved), util.digest(saved)
    return state, token, controller_owner.inspect(state, run_dir, workspace)


def render(runner, state: "RunState", args, workspace, run_dir, *, design_input_preview=None):
    state, token, owner = sample(runner, state, run_dir, workspace)
    observation = {
        "version": 1,
        "workspace": str(workspace),
        "run_dir": str(run_dir),
        "checkpoint_token": token,
        "sampled_at": util.now(),
    }
    execution = state.get("execution") or {}
    execution_inspection = None
    if execution.get("mode") == "durable_local" and execution.get("launch_id"):
        execution_inspection = worker_binding.observation(execution)
    state = controller_owner.project(state, owner)
    if getattr(args, "explain", False):
        text = stop_explanations.explain(
            state.get("status") or "",
            stop_reason=state.get("stop_reason") or "",
            needs=runner.run_view.needs(state),
            held_cause=pause_authority.answered_cause(state),
            ready=(owner or {}).get("kind") == "ready",
        )
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
    stale = bool(
        active
        and state.get("status") == "RUNNING"
        and worker_state
        and worker_state.get("checked")
        and not worker_state.get("alive")
        and not active_finished
    )
    check = state.get("active_runner_check")
    check_workers: Any = runner.processes.recorded_worker_state(check) if check else None
    check_liveness = runner.supervision.observe(check["supervision"]) if check and check.get("supervision") else None
    stale_check = bool(
        check
        and not active
        and state.get("status") == "RUNNING"
        and check_workers.get("checked")
        and not check_workers.get("alive")
    )
    strict_visual = runner.visual_runtime.requested(state)
    current: Any = (
        source_scope.snapshot(workspace, state, base_snapshot=runner.support.snapshot)
        if state["status"] == "TASK_COMPLETE" or strict_visual or state.get("settings", {}).get("design_manifest")
        else None
    )
    # Public --status stays lock-free. Scoped visual acceptance needs the runner's
    # CODE scope authority (exclusive locks); without it, projection reports UNKNOWN
    # and completion_current stays unknown rather than a false NOT_READY denial (#1255).
    visual_acceptance = (
        runner.visual_runtime.projection(state, current_snapshot=current)
        if (state.get("settings", {}).get("design_manifest") or strict_visual) and current
        else None
    )
    completion_current = (
        runner.completion_gate.completion_ready(state, state.get("final_decision", {}), current)
        if state["status"] == "TASK_COMPLETE"
        else None
    )
    if completion_current:
        try:
            launch_inputs.guard(state, workspace, run_dir)
        except ValueError:
            completion_current = False
    if completion_current is True and (state.get("settings", {}).get("design_manifest") or strict_visual):
        visual_gate = runner.visual_runtime.completion_check(state, current_snapshot=current)
        # None means scoped acceptance could not be judged without runner CODE.
        completion_current = visual_gate["passed"]
    inspected = None
    if getattr(args, "inspect_evidence", False):
        contract = state.get("goal_contract") or {}
        criteria = (contract.get("body") or {}).get("acceptance_criteria") or []
        missing = runner.goals.missing_human_reviews(state) if contract else []
        accepted = [row["id"] for row in criteria if row.get("human_review") and row["id"] not in missing]
        inspected = verification.inspect(
            state,
            workspace,
            snapshot=lambda root: source_scope.snapshot(root, state, base_snapshot=runner.support.snapshot),
            read_state=lambda: runner.read_json(run_dir / "state.json"),
            accepted_human_ids=accepted,
            initial_snapshot=current,
        )
        try:
            launch_inputs.guard(state, workspace, run_dir)
        except ValueError as error:
            inspected = verification.project(
                state,
                current_revision=inspected.get("inspected_source_revision"),
                evidence_matches=False,
                accepted_human_ids=accepted,
            )
            inspected["reasons"].append(str(error))
        if completion_current is not None and (
            inspected["freshness"] != "current" or inspected.get("inspected_source_revision") != current["revision"]
        ):
            completion_current = False
    conversation_journal = None
    if state.get("conversation_handoff"):
        try:
            conversation_journal = conversation_protocol.read_journal(run_dir)
        except (OSError, ValueError) as error:
            conversation_journal = {"session_error": str(error)}
    try:
        pending = interventions.inspect(workspace, run_dir)["requests"]
    except interventions.InterventionError:
        pending = None  # An unreadable inbox cannot accept a request either.
    public_view = runner.run_view.view(
        state,
        completion_current=completion_current,
        controller=owner,
        conversation_journal=conversation_journal,
        visual_acceptance=visual_acceptance,
        liveness=liveness,
        runner_check_liveness=check_liveness,
        verification_obligation=preparation.frontier(run_dir / "check-replay" / "obligations", check),
        stale_report_repair=runner.stale_report_repair(state, workspace) is not None,
        design_input_preview=design_input_preview,
        run_dir=run_dir,
        workspace=workspace,
        dev_server=dev_server.inspect(run_dir, state.get("settings")),
        observation=observation,
        execution_inspection=execution_inspection,
        stop_pending=any(isinstance(row, dict) and row.get("kind") == "stop" for row in pending or []),
    )
    if public_view.get("workflow") == "review" and state.get("status") == "TASK_COMPLETE":
        result = public_view["evidence"].get("workflow_result")
        if result:
            public_view["review_result"] = review_result.inspect(
                result, workspace, current.get("revision") if current else None
            )
    public_view["attention"] = attention_status.observed(public_view.get("attention"))
    public_view["job_report_recovery"] = job_report_recovery.offer(runner, state, run_dir, workspace)
    if pending is not None:
        public_view["pause"] = pause_offer.project(
            state, public_view, pending=pending, run_dir=run_dir, workspace=workspace
        )
    if inspected is not None:
        public_view["verification"] = inspected
    try:
        from . import autocode_evidence_export as evidence_export
    except ImportError:
        import autocode_evidence_export as evidence_export
    if isinstance(state.get("evidence_export"), dict):
        public_view["evidence_report"] = evidence_export.read(
            run_dir,
            state["evidence_export"],
            expected_binding=(
                None
                if cast(dict, state["evidence_export"]).get("error")
                else evidence_export.binding(state, (public_view.get("usage") or {}).get("accounting"))
            ),
            current=completion_current is True and (inspected is None or inspected["freshness"] == "current"),
            include=getattr(args, "inspect_evidence", False),
        )
    supervision_state = public_view.get("liveness", {})
    lost_supervisor = supervision_state.get("kind") in ("unsupervised", "interrupted")
    if active and not active.get("finished_at"):
        lost_supervisor = lost_supervisor or any(
            supervision_state.get(key, {}).get("checked") and supervision_state[key].get("alive") is False
            for key in ("owner", "keeper")
        )
    stale = stale or bool(active and state.get("status") == "RUNNING" and lost_supervisor)
    check_supervision_state = (public_view.get("runner_check") or {}).get("liveness", {})
    lost_check_supervisor = check_supervision_state.get("kind") in ("unsupervised", "interrupted")
    if check and check.get("supervision") and check_supervision_state.get("kind") != "stopped":
        lost_check_supervisor = lost_check_supervisor or any(
            check_supervision_state.get(key, {}).get("checked") and check_supervision_state[key].get("alive") is False
            for key in ("owner", "keeper")
        )
    stale_check = stale_check or bool(check and state.get("status") == "RUNNING" and lost_check_supervisor)
    checkpoint = runner.milestones.summary(state)
    stage = (active or {}).get("stage") or state.get("next_stage")
    # A finished attempt still sits in active_stage until its record is saved. The
    # job after it is what a person should see next to that "finished" line (#1711).
    following_id = state.get("next_stage") if active_finished else None
    if following_id and following_id == (active or {}).get("stage"):
        following_id = None
    next_action = (
        f"AutoResolver must reconcile retained attempt {runner.attempt_id(active)} before any provider call"
        if stale
        else "Inspect the retained runner check and resume the run"
        if stale_check
        else None
    )
    if stale and "--reconcile-only" in (public_view.get("next_command") or ""):
        next_action = public_view["next_command"]
    if owner and owner["kind"] == "ended" and state.get("status") == controller_owner.STATUS:
        next_action = public_view["next_command"]
    # A stale runner check stopped before the next stage was launched: name the check, not that stage.
    stage_name = (
        f"Runner check ({str(cast(dict, check).get('stage') or 'check').replace('_', ' ')})"
        if stale_check
        else runner.autocode_status.role_name(stage, state)
        if stage
        else None
    )
    following_name = None if stale_check or not following_id else runner.autocode_status.role_name(following_id, state)
    public_view["progress"] = progress_view.progress(
        state,
        accepted=checkpoint["accepted_milestones"],
        needs=public_view["needs"],
        reviewed=reviewed_criteria(runner, state),
        stopped=next_action,
        finished=bool(active_finished),
        stage=stage_name,
        following=following_name,
    )
    # The run in one sentence and its exact next action (#2049); `--summary` prints only this.
    public_view["summary"] = status_summary.project(
        public_view, stale=stale or stale_check, completion_current=completion_current, next_action=next_action
    )
    public_view["chat"]["progress"] = chat_progress.project(
        state, public_view, finished=bool(active_finished), stopped=bool(next_action)
    )
    if activity := chat_activity.project(
        state, public_view, run_dir, finished=bool(active_finished), stopped=bool(next_action)
    ):
        public_view["chat"]["activity"] = activity

    def supervised(attempt: dict) -> bool:
        saved = attempt.get("supervision")
        observed = runner.supervision.observe(saved) if saved else None
        return liveness_policy.classify(saved, observed)["kind"] == "supervised"

    if log := chat_log.project(
        state, public_view, run_dir, supervised=supervised, finished=bool(active_finished), stopped=bool(next_action)
    ):
        public_view["chat"]["log"] = log
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
        print(
            f"STALE CHECKPOINT: saved status is RUNNING but the retained "
            f"{cast(dict, active).get('stage')} attempt has no current supervised result. "
            "AutoResolver must reconcile the retained attempt before any further provider call.",
            file=sys.stderr,
        )
    if stale_check:
        print(
            "STALE CHECKPOINT: the retained runner check has no current supervised result. "
            "Inspect its command ownership receipt and output before resuming the run.",
            file=sys.stderr,
        )
    if getattr(args, "summary", False):
        print(status_summary.text(public_view["summary"]))
        if block := answer_output.questions(public_view):
            print(block)  # what the named --answer command answers, with options and default (#2325)
        return
    print(
        json.dumps(
            {
                "run_dir": str(run_dir),
                "workspace": str(workspace),
                "project_workspace": state.get("project_workspace", str(workspace)),
                "task_branch": state.get("task_branch"),
                "status": state["status"],
                "iteration": state["iteration"],
                "stale": stale or stale_check,
                "next_action": next_action,
                "runner_check_workers": check_workers,
                "active_stage_workers": worker_state,
                "active_stage_finished": active_finished,
                "engine": state.get("settings", {}).get(
                    "engine", "codex" if args.run_dir else args.engine or runner.DEFAULT_ENGINE
                ),
                "next_stage": state.get("next_stage", "legacy; inspect saved finals"),
                "sessions": state["sessions"],
                "phase": state.get("phase", "DISCOVERING" if not args.run_dir else "migration_required"),
                "contract_token": runner.goals.token(state["goal_contract"]) if state.get("goal_contract") else None,
                "current_task": state.get("current_task"),
                "last_decision": state.get("last_decision"),
                "settings": state.get("settings"),
                "active_stage": active,
                "reasoning_escalations": state.get("reasoning_escalations", []),
                "attempt_id": runner.attempt_id(active) if active else None,
                "completion_current": completion_current,
                "milestone_checkpoint": checkpoint,
                "orchestration_batch": state.get("orchestration_batch"),
                "unit_handoffs": state.get("unit_handoffs", {}),
                "milestone_activation_pending": (run_dir / "milestone-checkpoints-requested.json").exists(),
                "interventions": runner.intervention_metadata(workspace, run_dir, state),
                "view": {**public_view, "delivery": runner.dependency.export(state, current, completion_current)},
                **runner.resolver_human.projection(state),
            },
            indent=2,
        )
    )

    if hint := answer_output.summary(public_view):
        print(hint, file=sys.stderr)
    if "cost" in public_view:
        print(cost.line(public_view["cost"]), file=sys.stderr)
    for line in escalation_view.lines(public_view.get("escalations")):
        print(line, file=sys.stderr)


def reviewed_criteria(runner, state: "RunState"):
    """Human-review criteria whose review receipt is valid for the current validation."""
    return set(runner.run_view.accepted_reviews(state))


def _pause_command():
    try:
        from . import autocode_pause_command as pause_command
    except ImportError:
        import autocode_pause_command as pause_command
    return pause_command


def pause_summary(runner, state: "RunState", workspace, run_dir):
    """Read-only category and action header; never re-render the mutating plan."""
    return _pause_command().summary(runner.run_view.view(state, run_dir=run_dir, workspace=workspace))


def print_pause(runner, state: "RunState", args, workspace, run_dir, rendered):
    """Print a non-complete outcome: with ``--json``, the same payload ``--status``
    prints; otherwise the prose view. The caller renders the prose BEFORE writing
    state — present() has a deliberate state effect (it drops a stale
    displayed_goal), and that effect must land before the state is persisted."""
    if getattr(args, "json", False):
        runner.json_output.status(runner, state, args, workspace, run_dir)
    else:
        pause_command = _pause_command()
        view = runner.run_view.view(state, run_dir=run_dir, workspace=workspace)
        summary = pause_command.summary(view)
        if summary:
            print(summary)
        print(rendered)
        # The command again after a long presentation, so the last line is the action (#2301).
        if trailer := pause_command.trailer(view, rendered):
            print(trailer)


def rejection_message(runner, state: "RunState", workspace, run_dir, error):
    """Append the current read-only recovery command to a refused action."""
    summary = pause_summary(runner, state, workspace, run_dir)
    return str(error) + ("\n" + summary if summary else "")


def reject_input(runner, state: "RunState", workspace, run_dir, error):
    """Print a refused input with its authoritative recovery action and exit 2."""
    print("Input rejected: " + rejection_message(runner, state, workspace, run_dir, error), file=sys.stderr)
    return 2
