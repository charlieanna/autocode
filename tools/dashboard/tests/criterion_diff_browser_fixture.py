"""A real Git run whose saved Builder steps belong to different requirements (#730).

Four requirements: C1's step is its own, C2 and C3's steps change the same
file (overlapping), and C4 has no saved step yet. The production dashboard
serves it and reads the run through the real `autocode --status`.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlencode

SOURCE = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(SOURCE), str(SOURCE / "tools/dashboard"), str(SOURCE / "tools")]
import autocode_code_checkpoints as checkpoints
import autocode_contract_identity as identity
import autocode_registry as registry
import autocode_util as util
from agent_console import Console, Handler, LoopbackHTTPServer
from goal_fixtures import body

STEPS = (
    ("task-1", "M1", "Greet by name", {"greet.py": "def greet(name):\n    return 'Hello, ' + name\n"}),
    ("task-2", "M2", "Read the name argument", {"cli.py": "import sys\nprint(sys.argv[1])\n"}),
    (
        "task-3",
        "M3",
        "Reject an empty name",
        {
            "cli.py": "import sys\nif not sys.argv[1:]:\n    sys.exit(2)\nprint(sys.argv[1])\n",
            "test_cli.py": "# fixture\n",
        },
    ),
)


def plan():
    value = body()
    value["milestones"] = [
        {**value["milestones"][0], "id": f"M{n}", "objective": f"Milestone {n}", "acceptance_criteria": [f"C{n}"]}
        for n in range(1, 5)
    ]
    value["acceptance_criteria"] = [
        {"id": "C1", "criterion": "Greets by name", "verification_method": "Run greet", "human_review": False},
        {
            "id": "C2",
            "criterion": "Reads the name argument",
            "verification_method": "Run the CLI",
            "human_review": False,
        },
        {
            "id": "C3",
            "criterion": "Rejects an empty name",
            "verification_method": "Run with no name",
            "human_review": False,
        },
        {"id": "C4", "criterion": "Documents usage", "verification_method": "Read the README", "human_review": False},
    ]
    return value


def build(root):
    workspace = root / "project"
    workspace.mkdir(parents=True)

    def git(*args):
        return subprocess.check_output(["git", "-C", str(workspace), *args], text=True).strip()

    git("init", "-q")
    (workspace / "greet.py").write_text("def greet(name):\n    return name\n")
    (workspace / "cli.py").write_text("print('hello')\n")
    git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=f@example.test", "commit", "-qm", "base")
    run = workspace / ".autocode/runs/fixture"
    run.mkdir(parents=True)
    (workspace / ".autocode/.gitignore").write_text("*\n")
    contract = {"task_id": "run-task", "revision": 1, "body": plan()}
    contract["hash"] = util.digest(contract)
    approval = {"kind": "goal_approval", "actor": "user_cli", "token": identity.token(contract), "at": util.now()}
    contract.update(approval_status="approved", approval_event=approval, origin="astra_discovery")
    state = {
        "version": 3,
        "task_id": "run-task",
        "task": "Build a greeting CLI",
        "workspace": str(workspace),
        "run_dir": str(run),
        "created_at": util.now(),
        "status": "PAUSED_REQUESTED",
        "iteration": 3,
        "next_stage": "sol",
        "sessions": {},
        "history": [],
        "stages": [],
        "active_seconds": 12,
        "settings": {
            "engine": "codex",
            "roles": {"terra": {"model": "gpt-6-sol", "reasoning_effort": "high", "pinned": True}},
            "limits": {"max_seconds": 0},
        },
        "user_events": [approval],
        "findings_ledger": [],
        "workflow": {"kind": "build"},
        "goal_contract": contract,
        "base_commit": git("rev-parse", "HEAD"),
    }
    (run / "iterations").mkdir()
    for number, (task, milestone, objective, files) in enumerate(STEPS, 1):
        for name, text in files.items():
            (workspace / name).write_text(text)
        # The run-level snapshot the Changes pane lists, as a Builder step saves it.
        diff = f"iterations/builder-{number:02}.diff"
        (run / diff).write_text(git("diff", "HEAD") + "\n")
        revision = util.snapshot(workspace)["revision"]
        state["current_task"] = {"id": task, "milestone_id": milestone, "objective": objective}
        state["implementation"] = {"task_id": task, "source_revision": revision, "contract_hash": contract["hash"]}
        state["stages"].append(
            {
                "stage": "terra",
                "task_id": task,
                "source_revision": revision,
                "exit_code": 0,
                "finished_at": util.now(),
                "changed_files": sorted(files),
                "diff_ref": diff,
            }
        )
        checkpoints.update(state, run)
        assert state["code_checkpoints"][-1].get("available"), state["code_checkpoints"][-1]
    util.atomic_json(run / "state.json", state)
    registry.register_run(workspace, run, state)
    return workspace, run, [row["id"] for row in state["code_checkpoints"]]


class OfflineConsole(Console):
    def model_catalogue(self, refresh=False):
        return {"models": [], "error": "Offline criterion-diff fixture; no model requests"}


def main():
    root = Path(os.environ["AUTOCODE_FIXTURE_ROOT"]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ["AUTOCODE_HOME"] = str(root / "registry")
    scratch = tempfile.TemporaryDirectory()
    workspace, run, steps = build(Path(scratch.name).resolve())
    console = OfflineConsole(
        [workspace],
        SOURCE / "tools/autocode.py",
        lambda: False,
        conversation_root=root / "dashboard/conversations",
        project_store_root=root / "dashboard",
    )
    server = LoopbackHTTPServer(("127.0.0.1", 0), Handler)
    server.console = console
    server.hosts = {"127.0.0.1:" + str(server.server_port)}
    base = "http://127.0.0.1:" + str(server.server_port)
    fixture = {
        "url": base + "/#" + urlencode({"task": str(workspace), "run": str(run)}),
        "workspace": str(workspace),
        "run": str(run),
        "steps": steps,
    }
    print("FIXTURE=" + json.dumps(fixture), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        console.pool.shutdown(wait=True)
        console.conversations.close(wait=True)
        scratch.cleanup()


if __name__ == "__main__":
    main()
