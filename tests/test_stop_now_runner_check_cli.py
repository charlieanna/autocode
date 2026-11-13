"""Public Stop now interrupts an owned local baseline without completing its proof."""

import json
import os
import select
import signal
import subprocess
import sys
import textwrap
import unittest

import autocode_client_actions as actions
import psutil
from autocode_taskrun import TaskRun
from goal_fixtures import GREETING_TESTS

from tests import test_subprocess as fixture


@unittest.skipUnless(sys.platform == "linux", "supervised fake providers require supported Linux")
class StopNowRunnerCheckCliTests(unittest.TestCase):
    setUp = fixture.SubprocessFlow.setUp

    def status(self, run):
        result = subprocess.run(
            [*self.entry, "--workspace", str(run.workspace), "--run-dir", str(run.run_dir), "--status"],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        return json.loads(result.stdout)

    def test_stop_now_ends_the_running_baseline_and_its_descendant_before_another_check_or_model(self):
        ready, gate = self.root / "baseline-ready", self.root / "baseline-gate"
        released, completed = self.root / "baseline-released", self.root / "baseline-completed"
        retried = self.root / "baseline-retried"
        for path in (ready, gate):
            os.mkfifo(path)
        ready_fd = os.open(ready, os.O_RDWR | os.O_NONBLOCK)
        gate_fd = os.open(gate, os.O_RDWR | os.O_NONBLOCK)
        self.addCleanup(os.close, ready_fd)
        self.addCleanup(os.close, gate_fd)
        child = "from pathlib import Path; Path(" + repr(str(gate)) + ").open('rb').read(1)"
        barrier = textwrap.indent(
            textwrap.dedent(f"""
                # Only the real original-code copy lacks the Builder's greet.py.
                if not Path(__file__).with_name("greet.py").exists() and Path({str(released)!r}).exists():
                    Path({str(retried)!r}).write_text("a fresh baseline ran after explicit resume")
                if not Path(__file__).with_name("greet.py").exists() and not Path({str(released)!r}).exists():
                    import json
                    import os
                    held = subprocess.Popen([sys.executable, "-c", {child!r}])
                    with Path({str(ready)!r}).open("w") as stream:
                        stream.write(json.dumps({{"command_pid": os.getpid(), "descendant_pid": held.pid}}) + "\\n")
                    held.wait()
                    Path({str(completed)!r}).write_text("baseline completed normally")
            """),
            "        ",
        )
        tests = GREETING_TESTS.replace("        return subprocess.run(", barrier + "        return subprocess.run(", 1)
        reviewer = self.root / "fixture-bin/goal_fixtures.py"
        source = reviewer.read_text()
        marker = "\n\nGOODBYE_SOURCE ="
        self.assertEqual(1, source.count(marker))
        # The fake's own test bytes and its exact fixture reasons stay together.
        reviewer.write_text(source.replace(marker, "\nGREETING_TESTS = " + repr(tests) + marker, 1))
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        run = TaskRun.start(
            self.project,
            "Build greeting",
            command=self.entry,
            options=("--engine", "codex", "--max-cost-usd", "0", "--max-seconds", "300"),
            env=self.env,
            cwd=self.root,
            timeout=180,
        )
        run.answer("Q1", "CLI")
        view = run.advance_until_input()
        self.assertEqual("approve_plan", view["needs"]["kind"], view)
        self.assertIn("greet.py", run.show_goal())
        run.approve_plan(view["needs"]["token"])
        with (self.root / "worker.stdout").open("w") as stdout, (self.root / "worker.stderr").open("w") as stderr:
            worker = subprocess.Popen(
                [*self.entry, "--workspace", str(run.workspace), "--run-dir", str(run.run_dir), "--no-chat"],
                cwd=self.root,
                env=self.env,
                stdout=stdout,
                stderr=stderr,
            )
            try:
                readable, _, _ = select.select([ready_fd], [], [], 120)
                self.assertTrue(readable, "the actual original-code command never entered its FIFO handshake")
                entered = json.loads(os.read(ready_fd, 4096))
                owned = [psutil.Process(entered[key]) for key in ("command_pid", "descendant_pid")]
                before = self.status(run)
                (self.root / "before-stop-public.json").write_text(json.dumps(before, indent=2))
                view = before["view"]
                self.assertEqual("regression_proof", view["runner_check"]["stage"])
                self.assertFalse(view["usage"]["accounting"]["active_attempts"])
                self.assertTrue(before["runner_check_workers"]["alive"])
                offer = view["pause"]["stop_now"]
                self.assertFalse(offer["requested"])
                argv = [word.replace("REQUEST_ID", "stop-baseline-1") for word in offer["next_command"]]
                submitted = subprocess.run(
                    [*self.entry, *argv], cwd=self.root, env=self.env, capture_output=True, text=True, timeout=30
                )
                self.assertEqual(0, submitted.returncode, submitted.stdout + submitted.stderr)
                receipt = json.loads(submitted.stdout)
                (self.root / "stop-receipt.json").write_text(submitted.stdout)
                self.assertTrue(receipt["accepted"])
                self.assertTrue(receipt["receipt"]["now"])
                try:
                    code = worker.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    self.fail("Accepted Stop now did not end the owned baseline before its ordinary completion")
                self.assertEqual(2, code, (self.root / "worker.stdout").read_text())
                after = self.status(run)
                (self.root / "after-stop-public.json").write_text(json.dumps(after, indent=2))
                paused = after["view"]
                self.assertEqual("PAUSED_INTERVENTION", paused["status"])
                self.assertFalse(paused["done"])
                self.assertIsNone(paused["runner_check"])
                self.assertFalse(after["runner_check_workers"])
                self.assertFalse(completed.exists(), "the held command must not finish on its own")
                self.assertTrue(
                    all(not process.is_running() or process.status() == psutil.STATUS_ZOMBIE for process in owned)
                )
                self.assertEqual(view["approved_contract"], paused["approved_contract"])
                self.assertEqual(view["usage"]["accounting"]["attempts"], paused["usage"]["accounting"]["attempts"])
                self.assertIsNone(paused["evidence"].get("regression_proof"))
                duplicate = subprocess.run(
                    [*self.entry, *argv], cwd=self.root, env=self.env, capture_output=True, text=True, timeout=30
                )
                self.assertEqual(0, duplicate.returncode, duplicate.stdout + duplicate.stderr)
                self.assertEqual("already_applied", json.loads(duplicate.stdout)["consumer"])
                self.assertEqual(after["view"]["usage"], self.status(run)["view"]["usage"])
                released.touch()
                named = actions.named(paused, run.workspace, run.run_dir)
                self.assertIn("--resume-paused", named.argv)
                resumed = subprocess.run(
                    [
                        *self.entry,
                        "--workspace",
                        str(run.workspace),
                        "--run-dir",
                        str(run.run_dir),
                        *named.argv,
                    ],
                    cwd=self.root,
                    env={**self.env, **named.env},
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
                (self.root / "resume.stdout").write_text(resumed.stdout)
                (self.root / "resume.stderr").write_text(resumed.stderr)
                self.assertIn(resumed.returncode, (0, 2), resumed.stdout + resumed.stderr)
                self.assertTrue(retried.exists(), "resume must execute a fresh baseline, never reuse interrupted proof")
                self.assertEqual(view["approved_contract"], self.status(run)["view"]["approved_contract"])
            finally:
                # Failure cleanup releases this test's FIFO, not another run's controller.
                released.touch()
                os.write(gate_fd, b"release")
                if worker.poll() is None:
                    try:
                        worker.wait(timeout=60)
                    except subprocess.TimeoutExpired:
                        worker.send_signal(signal.SIGINT)
                        worker.wait(timeout=30)
