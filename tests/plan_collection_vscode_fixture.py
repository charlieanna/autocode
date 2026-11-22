"""Stage a sealed offline approval view with real, distinct pytest observations."""

import json
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import autocode_registry as registry  # noqa: E402
import autocode_requirements_observations as observations  # noqa: E402
import autocode_source_snapshot as source  # noqa: E402
import autocode_util as util  # noqa: E402

TITLE = "Offline collect-only plan evidence"


def prepare(root):
    workspace = root / "project"
    workspace.mkdir()
    (workspace / "test_baseline.py").write_text(
        "def test_targeted():\n    assert 2 + 2 == 4\n\n\ndef test_other():\n    assert False\n"
    )
    git = ["git", "-C", str(workspace), "-c", "user.name=Fixture", "-c", "user.email=f@example.test"]
    for args in (["init", "-q"], ["add", "."], ["commit", "--no-gpg-sign", "-qm", "seed"]):
        subprocess.run([*git, *args], check=True, capture_output=True, timeout=10)
    run = workspace / ".autocode/runs/collection-plan"
    attempt = run / "iterations/001"
    attempt.mkdir(parents=True)
    # Reconstruct the original failure mode: full collection succeeds, a scoped
    # execution passes, but an ordinary full run executes one failing case.
    commands = []
    evidence = [{"type": "step_start", "sessionID": "planner", "part": {"id": "start", "type": "step-start"}}]
    for index, (args, expected) in enumerate(
        ((["--collect-only", "-q"], 0), (["-v", "test_baseline.py::test_targeted"], 0), (["-q"], 1))
    ):
        command = [sys.executable, "-m", "pytest", *args]
        result = subprocess.run(command, cwd=workspace, capture_output=True, text=True, timeout=30)
        if result.returncode != expected:
            raise AssertionError(f"Unexpected fixture pytest result: {result.stdout[-400:]} {result.stderr[-400:]}")
        commands.append(shlex.join(command))
        evidence.append(
            {
                "type": "tool_use",
                "sessionID": "planner",
                "part": {
                    "id": f"fixture-{index}",
                    "tool": "bash",
                    "state": {
                        "status": "completed",
                        "input": {"command": commands[-1]},
                        "output": result.stdout,
                        "metadata": {"exit": result.returncode, "truncated": False},
                    },
                },
            }
        )
    before = source.snapshot(workspace)
    report = {"summary": "Preserve the CLI", "required_behaviors": ["Keep the existing tests"]}
    record = {
        "stage": "draft_plan",
        "role": "planner",
        "exit_code": 0,
        "finished_at": util.now(),
        "source_revision": before["revision"],
        "changed_files": [],
        "before_ref": str(attempt / "before.json"),
        "after_ref": str(attempt / "after.json"),
        "events": str(attempt / "events.jsonl"),
        "output": str(attempt / "report.json"),
    }
    for field in ("before_ref", "after_ref"):
        Path(record[field]).write_text(json.dumps(before))
    Path(record["output"]).write_text(json.dumps(report))
    evidence.append(
        {
            "type": "step_finish",
            "sessionID": "planner",
            "part": {"id": "finish", "type": "step-finish", "reason": "stop"},
        }
    )
    Path(record["events"]).write_text("".join(json.dumps(row) + "\n" for row in evidence))
    contract = {
        "task_id": "collection-plan",
        "revision": 1,
        "body": {
            "intended_outcome": "Preserve the baseline",
            "acceptance_criteria": [
                {"id": "AC1", "criterion": "Keep the existing checks", "current_behavior": "The full suite passes."}
            ],
        },
    }
    contract["hash"] = util.digest(contract)
    state = {
        "version": 3,
        "task_id": "collection-plan",
        "task": TITLE,
        "workspace": str(workspace),
        "run_dir": str(run),
        "status": "AWAITING_GOAL_APPROVAL",
        "phase": "AWAITING_GOAL_APPROVAL",
        "next_stage": "builder",
        "iteration": 1,
        "sessions": {},
        "settings": {},
        "stages": [record],
        "goal_contract": contract,
        "displayed_goal": f"r1:{contract['hash']}",
        "planning": {"reports": {"draft_plan": {"report": report, "output": record["output"]}}},
    }
    observations.prepare(record)
    observations.seal_plan(state, record)
    if record[observations.FIELD]["status"] != "sealed":
        raise AssertionError("The fixture did not retain its complete collection event")
    (run / "state.json").write_text(json.dumps(state))
    store = root / "registry"
    store.mkdir()
    workspace_id, run_id = registry._identity("workspace", workspace), registry._identity("run", run)
    (store / "registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "workspaces": {workspace_id: {"id": workspace_id, "workspace": str(workspace)}},
                "runs": {
                    run_id: {
                        "id": run_id,
                        "workspace_id": workspace_id,
                        "workspace": str(workspace),
                        "run_dir": str(run),
                        "task_id": "collection-plan",
                    },
                },
            }
        )
    )
    return {
        "workspace": str(workspace),
        "run_dir": str(run),
        "collection": commands[0],
        "selection": commands[1],
        "title": TITLE,
    }


if __name__ == "__main__":
    print(json.dumps(prepare(Path(sys.argv[1]).resolve(strict=True))))
