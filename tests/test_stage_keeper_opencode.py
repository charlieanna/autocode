"""#454 repro 5 on the OpenCode route: a killed controller's provider is stopped at once.

Uses the checked-in offline OpenCode fixture in its allowed TIMEOUT_ONCE form, whose
first Plan Reviewer challenge sleeps 60 s. Before the stage keeper, the orphaned
provider ran to that natural end, about four times the stage cap.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import threading
import time
import unittest

import psutil

from . import test_planning, test_subprocess
from .opencode_fixture_cli import TIMEOUT_ONCE


def alive(process):
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


class OpenCodeStageKeeperTests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    prepare = test_planning.JointFlow.prepare
    new_run_engine_args = ()

    def await_condition(self, predicate, message, timeout=30):
        deadline = time.monotonic() + timeout
        pause = threading.Event()
        while time.monotonic() < deadline:
            if predicate():
                return
            pause.wait(.02)
        self.fail("timed out awaiting " + message)

    def test_controller_sigkill_stops_held_opencode_provider(self):
        self.prepare()
        fake = self.root / "fixture-bin/opencode"
        fake.write_text(fake.read_text().replace("with tempfile.TemporaryDirectory() as temp:", TIMEOUT_ONCE))
        held = self.root / "timeout-once"
        self.env["AUTOCODE_FIXTURE_TIMEOUT_ONCE"] = str(held)
        self.launch(["Build a greeting tool", "--no-chat", "--max-stage-seconds", "30"], 2)
        run, _ = self.saved()
        self.launch(["--run-dir", str(run), "--answer", "Q1=CLI"], 0)
        controller = subprocess.Popen([*self.entry, "--workspace", str(self.project), "--run-dir", str(run), "--no-chat"],
                                      cwd=self.root, env=self.env, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, start_new_session=True)
        provider = None
        try:
            self.await_condition(lambda: held.exists() or controller.poll() is not None, "the held challenge")
            self.assertIsNone(controller.poll(), "the controller must still be supervising the held stage")
            active = json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)["active_stage"]
            self.assertEqual("opencode", active["engine"])
            provider = psutil.Process(active["pid"])
            self.assertEqual(controller.pid, provider.ppid(), "the provider stays the controller's direct child")
            controller.kill()
            controller.wait(timeout=15)
            self.await_condition(lambda: not alive(provider), "the stage keeper to stop the orphaned provider",
                                 timeout=10)
            report_path = Path(active["supervision"]["report"])
            self.await_condition(report_path.is_file, "the supervision report")
            report = json.loads(report_path.read_text())
            self.assertEqual("supervisor_lost", report["cause"], report)
            self.assertEqual("stopped", report["outcome"], report)
            self.assertEqual(provider.pid, report["target"]["pid"])
        finally:
            if controller.poll() is None:
                controller.kill()
            controller.wait(timeout=15)
            if provider is not None and alive(provider):
                provider.kill()  # psutil checks the retained birth identity


if __name__ == "__main__":
    unittest.main()
