"""Internal isolated Builder entry point, supervised by the Orchestrator."""
from pathlib import Path
import sys
import uuid

try:
    from . import autocode as runner
except ImportError:
    import autocode as runner


def execute(state, directory, workspace, mode):
    runner.goals.execution_guard(state)
    runner.opencode = runner.autocode_providers.resolve(state["settings"].get("provider", "opencode"))
    if state.get("active_stage"):
        try:
            runner.reconcile_active(state, directory, workspace)
        except runner.ReportRepairQueued:
            pass
        except runner.support.Paused:
            if mode != "retry" or not state.get("active_stage"):
                raise
            runner.abandon_stage(state, directory, workspace, runner.attempt_id(state["active_stage"]))
            state["next_stage"] = "terra"
    if mode == "retry":
        runner.prepare_exhausted_execution_report_retry(state, directory)
    while state.get("pending_report_repair"):
        try:
            runner.execute_report_repair(state, directory, workspace)
        except runner.ReportRepairQueued:
            continue
    if not state.get("implementation"):
        if mode == "recover" or mode == "start" and (state.get("stages") or (directory / "result.json").exists()):
            raise runner.support.Paused("PAUSED_ORCHESTRATOR_WORKER", "No completed Builder result; explicitly retry this member after inspection")
        state.update(status="RUNNING", next_stage="terra")
        prompt, metrics = runner.support.context_packet(state, "terra", directory / "state.json")
        prompt = ("\nYou are one isolated Builder in a parallel milestone batch. Write only within "
                   "current_task.affected_paths. Other Builders own the other milestones. Do not "
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
                runner.apply_result(state, "terra", value, record, workspace, directory)
            except (ValueError, KeyError, runner.support.Paused) as error:
                runner.reject_completed_stage(state, directory, record, error)
        except runner.ReportRepairQueued:
            while state.get("pending_report_repair"):
                try:
                    runner.execute_report_repair(state, directory, workspace)
                except runner.ReportRepairQueued:
                    continue
    implementation = state.get("implementation", {})
    runner.goals.execution_guard(state, implementation)
    request = implementation.get("user_request", {})
    if request.get("kind") != "none":
        raise runner.support.Paused("PAUSED_ORCHESTRATOR_WORKER", request.get("decision_needed", "Builder needs a user decision"))
    if implementation.get("source_revision") != runner.support.snapshot(workspace)["revision"]:
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
                runner.write_json(directory / "result.json", {"status": state["status"], "reason": reason})
                return 2
    except runner.support.Paused as error:
        # Never overwrite a live owner's state when its lock is held.
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "start"))
