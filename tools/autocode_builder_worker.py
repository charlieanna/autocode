"""Internal isolated Builder entry point, supervised by the Orchestrator."""

try:
    from . import autocode_source_scope as source_scope
except ImportError:
    import autocode_source_scope as source_scope

from pathlib import Path
import sys
import uuid

try:
    from . import autocode as runner, autocode_stage_context as stage_context
    from . import autocode_worker_quota as worker_quota, autocode_quota_route as quota_route
    from . import autocode_test_examples as test_examples, autocode_test_cases as test_cases
    from . import autocode_assignment as assignment
except ImportError:
    import autocode_assignment as assignment
    import autocode_test_cases as test_cases
    import autocode as runner, autocode_stage_context as stage_context
    import autocode_worker_quota as worker_quota, autocode_quota_route as quota_route
    import autocode_test_examples as test_examples


def execute(state, directory, workspace, mode):
    def require_running():
        runner.stop_policy.refuse_admission(state)
        if state.get('status') != 'RUNNING':
            raise runner.support.Paused(state['status'], state.get('stop_reason') or
                                        'Worker requires explicit continuation before another stage')
        if state.get('pause_intent') and not state['pause_intent'].get('acknowledged_at'):
            raise runner.support.Paused('PAUSED_INTERVENTION', state.get('stop_reason') or
                                        'Queued pause requires explicit continuation')

    def repair_reports():
        require_running()
        while state.get('pending_report_repair'):
            try:
                runner.execute_report_repair(state, directory, workspace)
            except runner.ReportRepairQueued:
                pass
            require_running()

    runner.stop_policy.refuse_admission(state)
    runner.goals.execution_guard(state)
    runner.autopilot.builder_failure.dispatch_guard(state, 'terra', workspace)
    runner.autopilot.builder_policy.guard(state)
    runner.autopilot.regression.before_review(state, None, workspace, directory)
    runner.opencode = runner.autocode_providers.resolve(state["settings"].get("provider", "opencode"))
    if (mode == "recover" and not state.get("active_stage") and not state.get("stages")
            and not state.get("implementation") and not (directory / "iterations").exists()
            and not (directory / "result.json").exists()):
        # The parent persisted launch intent but no provider attempt ever began.
        # Called under the worker workspace lock, after parent process-ownership
        # checks. A pristine, bound worktree can start; partial work cannot replay.
        parent = runner.read_json(Path(state["parent_run"]) / "state.json")
        batch = parent.get("orchestration_batch") or {}
        row = next((r for r in batch.get("workers", []) if r.get("task") == state["current_task"]), None)
        current = source_scope.snapshot(workspace, state, base_snapshot=runner.support.snapshot)
        expected = {p: h for p, h in batch.get("baseline", {}).get("files", {}).items() if h != "deleted"}
        if (not row or batch.get("id") != state.get("parent_batch")
                or batch.get("contract_hash") != state["goal_contract"]["hash"]
                or Path(row["run_dir"]).resolve() != directory.resolve()
                or current["head"] != batch.get("base_commit") or current["files"] != expected):
            raise runner.support.Paused("PAUSED_ORCHESTRATOR_DRIFT", "Unlaunched Builder baseline changed; no automatic replay")
        mode = "start"
    if mode == "retry":
        parent = runner.read_json(Path(state["parent_run"]) / "state.json")
        if any(r.get("stage") == "orchestrator" and not r.get("finished_at")
               for r in parent.get("stages", [])):
            raise runner.support.Paused("PAUSED_ORCHESTRATOR_WORKER", "Parent orchestrator stage is unfinished")
        # Only the entry-time pause is resumed by the parent's explicit member retry.
        # A pause committed during reconciliation or repair must stop this invocation.
        if state.get('pause_intent') and not state['pause_intent'].get('acknowledged_at'):
            state['pause_intent']['acknowledged_at'] = runner.now()
            for receipt in state.get('applied_interventions', []):
                if receipt.get('kind') == 'pause' and not receipt.get('resumed_at'):
                    receipt['resumed_at'] = state['pause_intent']['acknowledged_at']
    if state.get("active_stage"):
        if mode == 'retry':
            state['status'] = 'RUNNING'  # The parent explicitly admitted this stopped member.
        try:
            runner.reconcile_active(state, directory, workspace)
        except runner.ReportRepairQueued:
            pass
        except runner.support.Paused as error:
            runner.result_application.raise_if_uncertain(error)
            active = state.get('active_stage') or {}
            if (mode != "retry" or not active
                    or active.get('stage') != 'terra' or active.get('report_only')):
                raise
            runner.abandon_stage(state, directory, workspace, runner.attempt_id(state["active_stage"]))
            state["next_stage"] = "terra"
            state['status'] = 'RUNNING'
        require_running()
    if mode == "retry":
        runner.prepare_exhausted_execution_report_retry(state, directory, workspace)
        state['status'] = 'RUNNING'
    repair_reports()
    if (state.get("next_stage") == "investigate_stuck"
            and (state.get("stuck_investigation") or {}).get("mode") == "builder_failure"):
        # A no-progress result queued this read-only classification at its durable
        # acceptance boundary. Use the controller's normal guards, accounting and
        # report repair; it alone decides whether another Builder is admitted.
        runner.autopilot.dispatch_unit(runner, state, "investigate_stuck", workspace, directory)
        repair_reports()
        if state.get("status") != "RUNNING" or state.get("next_stage") != "terra":
            status = state["status"] if state["status"] != "RUNNING" else "PAUSED_ORCHESTRATOR_WORKER"
            raise runner.support.Paused(status, state.get("stop_reason") or
                                        "Builder classification did not admit an execution retry")
    if not state.get("implementation"):
        if mode == "recover" or mode == "start" and (state.get("stages") or (directory / "result.json").exists()):
            raise runner.support.Paused("PAUSED_ORCHESTRATOR_WORKER", "No completed Builder result; explicitly retry this member after inspection")
        if state.get("next_stage") not in (None, "terra"):
            raise runner.support.Paused("PAUSED_ORCHESTRATOR_WORKER",
                                        "This Builder requires its queued controller stage before another execution")
        runner.autopilot.builder_failure.dispatch_guard(state, "terra", workspace)
        state.update(status="RUNNING", next_stage="terra")
        prompt, metrics = stage_context.context_packet(state, "terra", directory / "state.json")
        prompt = test_examples.add_to_prompt(prompt, workspace, state.get("current_task"))
        prompt = prompt.replace("\nCURRENT HANDOFF DATA\n", test_cases.builder_note(state) + assignment.BUILD_OUTPUT_NOTE
                                + "\nCURRENT HANDOFF DATA\n", 1)
        metrics = {**metrics, "estimated_prompt_tokens": (len(prompt.encode()) + 3) // 4}
        prompt = ("\nYou are one isolated Builder in a parallel milestone batch. Your worktree is "
                   f"{workspace}: every path you read or write is relative to it, and absolute paths "
                   "start with it. The directory above .autocode/builders/ is the shared workspace: "
                   "never write there, and never cd there. Write only within "
                   "current_task.affected_paths. Other Builders own the other milestones. Do not "
                   "treat a missing assigned output file as a missing prerequisite: create new "
                   "files and parent directories inside your assigned paths when the task requires them. "
                   "Use bare event: IDs or exact existing file paths in evidence_refs; put "
                   "explanations in summary/results, never append prose to a path. Do not "
                   "change branches, commit, merge, edit Git metadata, or write outside this worktree. "
                   "If the assignment needs shared changes or a permission decision, report a "
                   "structured user_request. Keep all evidence under this run directory.\n") + prompt
        state["pending_context_metrics"] = metrics
        schema = runner.goals.role_schema(runner.read_json(runner.SCHEMA_DIR / "v2/terra-report.schema.json"), "terra")
        schema_path = directory / "schema.json"
        runner.write_json(schema_path, schema)
        try:
            value, record = runner.run_role(role="terra", prompt=prompt, sandbox="workspace-write",
                workspace=workspace, run_dir=directory, state=state, schema=schema_path,
                model=state["settings"]["roles"]["terra"]["model"], allow_write=True, dry_run=False)
            try:
                runner.commit_stage_result(state, "terra", value, record, workspace, directory)
            except (ValueError, KeyError, runner.support.Paused) as error:
                runner.reject_completed_stage(state, directory, record, error)
        except runner.ReportRepairQueued:
            repair_reports()
    require_running()
    if not state.get('implementation') and state.get('no_progress_reports'):
        if (state.get('next_stage') == 'investigate_stuck'
                and (state.get('stuck_investigation') or {}).get('mode') == 'builder_failure'):
            runner.autopilot.dispatch_unit(runner, state, 'investigate_stuck', workspace, directory)
            repair_reports()
        if state.get('next_stage') != 'terra':
            raise runner.support.Paused('PAUSED_BUILDER_CLASSIFICATION',
                'Isolated Builder returned without source changes and requires its bound Resolver handoff '
                'before another writer attempt')
        return execute(state, directory, workspace, 'retry')
    implementation = state.get("implementation", {})
    runner.goals.execution_guard(state, implementation)
    request = implementation.get("user_request", {})
    if request.get("kind") != "none":
        raise runner.support.Paused("PAUSED_ORCHESTRATOR_WORKER", request.get("decision_needed", "Builder needs a user decision"))
    if implementation.get("source_revision") != source_scope.snapshot(workspace, state, base_snapshot=runner.support.snapshot)["revision"]:
        raise runner.support.Paused("PAUSED_ORCHESTRATOR_DRIFT", "Builder source changed after its result")
    state.update(status="BUILT", next_stage=None)
    runner.write_json(directory / "state.json", state)
    runner.write_json(directory / "result.json", {"status": "BUILT", "report": state["stages"][-1]["output"]})


def main(directory, mode="start"):
    directory = Path(directory).resolve()
    state = runner.read_json(directory / "state.json")
    workspace = Path(state["workspace"])
    try:
        with runner.support.workspace_lock(workspace):
            state = runner.read_json(directory / "state.json")
            try:
                previous = directory / "result.json"
                if previous.exists():
                    runner.write_json(directory / ("result-history-" + uuid.uuid4().hex[:12] + ".json"), runner.read_json(previous))
                execute(state, directory, workspace, mode)
                return 0
            except (Exception, KeyboardInterrupt) as error:
                reason = str(error) or state.get("pending_report_repair", {}).get("error") or type(error).__name__
                state.update(status=getattr(error, "status", "PAUSED_ORCHESTRATOR_WORKER"), stop_reason=reason)
                runner.write_json(directory / "state.json", state)
                result = {"status": state["status"], "reason": reason}
                if state["status"] in quota_route.STATUSES:  # its quota or its provider's content filter (#465)
                    worker = worker_quota.payload(state, directory, workspace)
                    if worker:
                        result["quota_worker"] = worker
                runner.write_json(directory / "result.json", result)
                return 2
    except runner.support.Paused as error:
        # Never overwrite a live owner's state when its lock is held.
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "start"))
