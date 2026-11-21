"""Prepare offline, source-bound stopped jobs for the real extension host test."""

import copy
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import autocode_job_failure as job_failure  # noqa: E402
import autocode_job_source as job_source  # noqa: E402
import autocode_registry as registry  # noqa: E402
import autocode_support as support  # noqa: E402

from tests.test_failure_retry import FailureRetryTests  # noqa: E402
from tests.test_needs_execution_matrix import NeedsExecutionMatrixTests  # noqa: E402


def relocate(value, oldrun, run, workspace, project):
    if isinstance(value, dict):
        return {
            relocate(k, oldrun, run, workspace, project): relocate(v, oldrun, run, workspace, project)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [relocate(v, oldrun, run, workspace, project) for v in value]
    if isinstance(value, str):
        return value.replace(str(oldrun), str(run)).replace(str(workspace), str(project))
    return value


def prepare(root, kind):
    if kind == "job":
        NeedsExecutionMatrixTests.setUpClass()
        case = NeedsExecutionMatrixTests("test_exact_job_retry_checks_source_and_current_token")
        case.setUp()
        workspace, oldrun = case.workspace, case.directory
        (workspace / "docs").mkdir(exist_ok=True)
        (workspace / "docs/greet.md").write_text("# Greeting\n\nThe greeting reads one name from argv.\n")
        case.state.update(
            status="PAUSED_JOB_FAILURE",
            stop_reason="Offline failed job status observation",
            next_stage="check_design",
            workflow={"kind": "build", "then": "draft_plan", "design_document": "docs/greet.md"},
        )
        case.state["job_failure"] = {
            "reason": "The Design Reviewer stopped",
            "stage": "check_design",
            "attempt_id": "001/check_design-01",
            "job_retry_token": "jr:fixture",
            "archive": str(oldrun / "archive"),
            "source_identity": support.digest(job_source.identity(workspace)),
            "configuration": job_failure.configuration(case.state),
            "write_diagnosis": {},
            "unrestored": [],
        }
    elif kind == "repeat":
        case = FailureRetryTests("runTest")
        case.setUp()
        case.seed()
        workspace, oldrun = case.root, case.run
        case.state["stop_reason"] = "Offline repeated failure status observation"
    else:
        raise ValueError("Unknown host fixture")
    try:
        project = root / "project"
        shutil.copytree(workspace, project, symlinks=True)
        run = project / ".autocode/runs/deadline-host"
        run.mkdir(parents=True)
        for source in oldrun.iterdir():
            destination = run / source.name
            if source.is_file():
                shutil.copy2(source, destination)
            elif source.is_dir():
                shutil.copytree(source, destination, symlinks=True)
        saved = relocate(copy.deepcopy(case.state), oldrun, run, workspace, project)
        saved.update(
            run_dir=str(run),
            workspace=str(project),
            task=f"Offline {kind} status deadline verification",
            task_id="deadline-host",
        )
        if kind == "job":
            saved["job_failure"]["source_identity"] = support.digest(job_source.identity(project))
        (run / "state.json").write_text(json.dumps(saved))
        workspace_id = registry._identity("workspace", project)
        run_id = registry._identity("run", run)
        document = {
            "version": 1,
            "workspaces": {workspace_id: {"id": workspace_id, "workspace": str(project)}},
            "runs": {
                run_id: {
                    "id": run_id,
                    "workspace_id": workspace_id,
                    "workspace": str(project),
                    "run_dir": str(run),
                    "task_id": "deadline-host",
                }
            },
        }
        store = root / "registry"
        store.mkdir()
        (store / "registry.json").write_text(json.dumps(document))
        return {"workspace": str(project), "run_dir": str(run), "registry": str(store), "kind": kind}
    finally:
        case.doCleanups()
        if kind == "job":
            NeedsExecutionMatrixTests.tearDownClass()


if __name__ == "__main__":
    print(json.dumps(prepare(Path(sys.argv[1]).resolve(), sys.argv[2])))
