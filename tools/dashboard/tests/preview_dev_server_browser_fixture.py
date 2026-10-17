"""A real Git run with a saved ``serve`` command, for the Preview pane's run server (#730).

The production dashboard serves it and reads it through the real ``autocode --status``; the browser test runs
the real ``autocode serve start`` on it. Each ``step`` line on stdin saves one more Builder step that changes the
app, as the runner does, so the server restarts on the new code; ``step broken`` saves a step that breaks the app.
A second, static app stands for an address the person pasted.
"""

import functools
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

SOURCE = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(SOURCE), str(SOURCE / "tools/dashboard"), str(SOURCE / "tools")]
import autocode_code_checkpoints as checkpoints
import autocode_contract_identity as identity
import autocode_dev_server_config as serve_config
import autocode_registry as registry
import autocode_util as util
from agent_console import Console, Handler, LoopbackHTTPServer
from goal_fixtures import body

# Serves the workspace unless a file named broken is there, as an app with a syntax error would fail.
APP = """import pathlib, runpy, sys
if pathlib.Path("broken").exists():
    print("SyntaxError: the app is broken", flush=True)
    sys.exit(3)
sys.argv = ["http.server", sys.argv[1], "--bind", "127.0.0.1"]
runpy.run_module("http.server", run_name="__main__")
"""


def page(title, colour):
    return (
        f"<!doctype html><title>{title}</title><body style='margin:0;font:600 28px system-ui;background:{colour};"
        f"display:grid;place-items:center;height:100vh'><h1>{title}</h1></body>"
    )


class Run:
    def __init__(self, root):
        self.workspace = root / "project"
        self.workspace.mkdir(parents=True)
        self.git("init", "-q")
        (self.workspace / "app.py").write_text(APP)
        (self.workspace / "index.html").write_text(page("Greeting app · base", "#f4f4f4"))
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=f@example.test", "commit", "-qm", "base")
        self.run = self.workspace / ".autocode/runs/fixture"
        self.run.mkdir(parents=True)
        (self.workspace / ".autocode/.gitignore").write_text("*\n")
        contract = {"task_id": "run-task", "revision": 1, "body": body()}
        contract["hash"] = util.digest(contract)
        approval = {"kind": "goal_approval", "actor": "user_cli", "token": identity.token(contract), "at": util.now()}
        contract.update(approval_status="approved", approval_event=approval, origin="astra_discovery")
        serve = serve_config.checked(
            {"command": shlex.quote(sys.executable) + " app.py {port}", "ready-path": "/index.html"}, "fixture"
        )
        self.state = {
            "version": 3,
            "task_id": "run-task",
            "task": "Build a greeting page",
            "workspace": str(self.workspace),
            "run_dir": str(self.run),
            "created_at": util.now(),
            "status": "PAUSED_REQUESTED",
            "iteration": 1,
            "next_stage": "terra",
            "sessions": {},
            "history": [],
            "stages": [],
            "active_seconds": 12,
            "settings": {
                "engine": "codex",
                "roles": {"terra": {"model": "gpt-6-sol", "reasoning_effort": "high", "pinned": True}},
                "limits": {"max_seconds": 0},
                serve_config.SETTING: serve,
            },
            "user_events": [approval],
            "findings_ledger": [],
            "workflow": {"kind": "build"},
            "goal_contract": contract,
            "base_commit": self.git("rev-parse", "HEAD"),
        }
        self.step()
        registry.register_run(self.workspace, self.run, self.state)

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.workspace), *args], text=True).strip()

    def step(self, broken=False):
        """One applied Builder step that changes the page, saved with its code checkpoint as the runner saves it."""
        number = len(self.state["stages"]) + 1
        (self.workspace / "index.html").write_text(page(f"Greeting app · version {number}", "#e7f3ec"))
        changed = ["index.html"]
        if broken:
            (self.workspace / "broken").write_text("")
            changed.append("broken")
        task = f"task-{number}"
        revision = util.snapshot(self.workspace)["revision"]
        self.state["iteration"] = number
        self.state["current_task"] = {"id": task, "milestone_id": "M1", "objective": f"Page version {number}"}
        self.state["implementation"] = {
            "task_id": task,
            "source_revision": revision,
            "contract_hash": self.state["goal_contract"]["hash"],
        }
        self.state["stages"].append(
            {
                "stage": "terra",
                "task_id": task,
                "source_revision": revision,
                "exit_code": 0,
                "finished_at": util.now(),
                "changed_files": changed,
            }
        )
        checkpoints.update(self.state, self.run)
        row = self.state["code_checkpoints"][-1]
        assert row["task_id"] == task and row.get("available"), row
        util.atomic_json(self.run / "state.json", self.state)
        return {
            "checkpoint": row["id"],
            "state_sha256": hashlib.sha256((self.run / "state.json").read_bytes()).hexdigest(),
        }


class OfflineConsole(Console):
    def model_catalogue(self, refresh=False):
        return {"models": [], "error": "Offline preview fixture; no model requests"}


def pasted_app(root):
    """The app at an address the person pasted: a different local server than the run's own."""
    folder = root / "pasted"
    folder.mkdir()
    (folder / "index.html").write_text(page("Saved address app", "#eef0fb"))
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(folder))
    handler.log_message = lambda *args: None
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/"


def steps(run):
    for line in sys.stdin:
        command = line.split()
        if command and command[0] == "step":
            print("STEPPED=" + json.dumps(run.step(broken="broken" in command[1:])), flush=True)


def main():
    root = Path(os.environ["AUTOCODE_FIXTURE_ROOT"]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ["AUTOCODE_HOME"] = str(root / "registry")
    scratch = tempfile.TemporaryDirectory()
    run = Run(Path(scratch.name).resolve())
    pasted, pasted_url = pasted_app(Path(scratch.name).resolve())
    console = OfflineConsole(
        [run.workspace],
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
        "url": base + "/#" + urlencode({"task": str(run.workspace), "run": str(run.run)}),
        "workspace": str(run.workspace),
        "run": str(run.run),
        "home": os.environ["AUTOCODE_HOME"],
        "pasted_url": pasted_url,
        "checkpoint": run.state["code_checkpoints"][-1]["id"],
    }
    threading.Thread(target=steps, args=(run,), daemon=True).start()
    print("FIXTURE=" + json.dumps(fixture), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        pasted.shutdown()
        console.pool.shutdown(wait=True)
        console.conversations.close(wait=True)
        scratch.cleanup()


if __name__ == "__main__":
    main()
