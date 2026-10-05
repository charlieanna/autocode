"""Isolated public-CLI fixtures for adversarial tests of AutoCode itself.

The scripted provider is the fault boundary. Runtime modules are never imported,
and saved private state is never edited. Evidence stays under .scenario-runs.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

import psutil

from . import catalog
from .driver import Driver, default_autocode, fake_setup
from .processes import run_cli
from .project import materialize

REPO = Path(__file__).resolve().parents[2]


class AdversarialCase(unittest.TestCase):
    """Tests deliberately remain red when a product invariant is violated."""

    scenario_id = "greenfield-greeting-cli"

    def setUp(self):
        output = Path(os.environ.get("AUTOCODE_ADVERSARIAL_OUT", REPO / ".scenario-runs/adversarial"))
        output.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix=self._testMethodName + "-", dir=output)).resolve()
        self.scenario = catalog.load(self.scenario_id)
        self.project = materialize(self.scenario.seed, self.root / "project")
        self.flags, env = fake_setup(self.scenario, self.root, self.scenario.reference)
        self.flags += ["--max-iterations", "2", "--max-seconds", "300", "--max-stage-seconds", "30"]
        self.driver = Driver(self.project, self.root, self.flags, env,
                             autocode=default_autocode(), max_steps=80, timeout_seconds=150)
        self.env = self.driver.env
        self.config_path = self.root / "fake-config.json"
        config = json.loads(self.config_path.read_text())
        config["adversarial"] = {"root": str(self.root), "case": "control"}
        self.config_path.write_text(json.dumps(config))
        provider = Path(__file__).with_name("adversarial_provider.py")
        # The entry point is copied, while its implementation stays in the harness.
        (self.root / "bin/codex").write_text(
            "#!" + sys.executable + "\nimport runpy\nrunpy.run_path(" + repr(str(provider)) + ", run_name='__main__')\n")
        (self.root / "bin/codex").chmod(0o755)
        self.owned = []
        self.children = []

    def tearDown(self):
        errors = []
        owned = list(self.owned)
        for event in self.trace():
            if event.get("pid") and event.get("birth_identity") is not None:
                try:
                    process = psutil.Process(event["pid"])
                    if process._ident[1] == event["birth_identity"] and process not in owned:
                        owned.append(process)
                except psutil.NoSuchProcess:
                    pass
        for process in list(owned):
            try:
                if process.is_running():
                    owned.extend(p for p in process.children(recursive=True) if p not in owned)
            except psutil.NoSuchProcess:
                pass
            except psutil.Error as error:
                errors.append(str(error))
        for process in reversed(owned):
            try:
                if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                    process.kill()  # psutil checks the retained birth identity.
            except psutil.NoSuchProcess:
                pass
            except psutil.Error as error:
                errors.append(str(error))
        _, alive = psutil.wait_procs(owned, timeout=3)
        errors.extend(f"owned PID {p.pid} remains alive" for p in alive
                      if p.is_running() and p.status() != psutil.STATUS_ZOMBIE)
        for child in self.children:
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                errors.append(f"owned CLI PID {child.pid} was not reaped")
        (self.root / "cleanup.json").write_text(json.dumps({"errors": errors}, indent=2))
        self.assertEqual([], errors, "Owned test processes must be cleaned up")

    def set_fault(self, module: str, case: str, **options):
        config = json.loads(self.config_path.read_text())
        config["adversarial"] = {"root": str(self.root), "module": module,
                                  "case": case, **options}
        self.config_path.write_text(json.dumps(config))

    def trace(self, event=None, stage=None):
        path = self.root / "provider-trace.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
        return [row for row in rows if (event is None or row.get("event") == event)
                and (stage is None or row.get("stage") == stage)]

    def discover_run(self):
        paths = sorted((self.project / ".autocode/runs").glob("*/state.json"))
        self.assertEqual(1, len(paths), "Exactly one run must have been created")
        self.driver.run_dir = paths[0].parent
        return self.driver.run_dir

    def command(self, *extra, task=None):
        return [*default_autocode(), *([task] if task else []), "--workspace", str(self.project),
                *(["--run-dir", str(self.driver.run_dir)] if self.driver.run_dir else ["--in-place"]),
                "--no-chat", *self.flags, *map(str, extra)]

    def invoke(self, *extra, task=None, timeout=60):
        command = self.command(*extra, task=task)
        result = run_cli(command, env=self.env, cwd=self.root, timeout=timeout)
        with (self.root / "invocations.jsonl").open("a") as out:
            out.write(json.dumps({"command": command, "exit_code": result.returncode,
                                  "stdout": result.stdout, "stderr": result.stderr}) + "\n")
        return result

    def spawn(self, *extra, task=None, own_session=False):
        """Start the CLI in the background; ``own_session`` makes it a session and group leader,
        so a test can signal its whole group the way a terminal or session manager does."""
        number = len(self.owned)
        stream = (self.root / f"async-{number}.log").open("w")
        try:
            child = subprocess.Popen(self.command(*extra, task=task), cwd=self.root, env=self.env,
                                     stdout=stream, stderr=subprocess.STDOUT, text=True,
                                     start_new_session=own_session)
        finally:
            stream.close()
        self.owned.append(psutil.Process(child.pid))
        self.children.append(child)
        return child

    def status(self):
        return self.driver.view()

    def start_to_approval(self):
        self.driver.call("start", task=self.scenario.brief)
        self.discover_run()
        for _ in range(12):
            view = self.status()
            need = view.get("needs") or {}
            if need.get("kind") == "approve_plan":
                return view
            self.assertIn(need.get("kind"), ("answer", "continue"), view)
            if need["kind"] == "continue":
                self.driver.call("continue")
            else:
                self.driver.serve(need)
        self.fail("Planning did not reach an approval request")

    def approve(self, view=None):
        view = view or self.status()
        self.assertEqual("approve_plan", view["needs"]["kind"], view)
        self.driver.serve(view["needs"])
        return self.status()

    def finish(self):
        return self.driver.until_stopped()

    def await_gone(self, process, *, timeout=15, message="an owned process to stop"):
        self.await_condition(lambda: not alive(process), timeout=timeout, message=message)

    def stage_keeper(self):
        """The active attempt's stage keeper, named by public ``--status`` and identity-checked."""
        status = json.loads(self.invoke("--status").stdout)
        row = ((status.get("active_stage") or {}).get("supervision") or {}).get("keeper")
        self.assertTrue(row, "The active attempt must name its stage keeper")
        keeper = psutil.Process(row["pid"])
        self.assertEqual(row["birth_identity"], birth_identity(keeper), "Stage keeper identity changed")
        return keeper

    def kill_supervision(self, controller):
        """Kill the stage keeper, then the controller, leaving the provider a live orphan.

        Controller death alone stops the provider at once (#454). Losing the keeper too is
        the remaining way to get a live orphan, so the second line of defence (the inherited
        checkout lock, process receipts and report adoption) stays covered.
        """
        keeper = self.stage_keeper()
        keeper.kill()  # psutil checks the retained birth identity
        self.await_gone(keeper, message="the stage keeper to die")
        controller.kill()

    def await_condition(self, predicate, *, timeout=15, message="test handshake"):
        # Waits for a causal process/file handshake, never a fixed-duration fault.
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            import threading
            threading.Event().wait(0.02)
        self.fail(f"Timed out awaiting {message}; evidence: {self.root}")


def alive(process) -> bool:
    """Running and not a zombie. A reparented process stays a zombie until init reaps it,
    which some hosts' init does late, and psutil's is_running() counts zombies as running."""
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def birth_identity(process) -> float:
    """AutoCode's recorded process birth identity (macOS: kernel epoch; Linux: create_time)."""
    return process._ident[1] if psutil.MACOS else process.create_time()
