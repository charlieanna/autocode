"""Stop now ends a running stage through the real CLI and fake OpenCode (#3263).

A stop request used to wait for the running step to finish, so a looping stage kept spending up to its
hour-long hard limit. A pause submitted with ``--now`` ends the stage at once, keeps its partial work, and
leaves the run paused with a plain reason; resuming runs again."""

import json
import subprocess
import time
import unittest

import autocode_stop as stop

from . import test_opencode


class StopNowCliTests(unittest.TestCase):
    def test_stop_now_ends_the_running_builder_and_the_run_pauses_stopped_by_you(self):
        flow = test_opencode.OpenCodeFlow()
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        barrier = flow.root / "hold-builder"  # the fake Builder waits while this file exists
        flow.env["AUTOCODE_CONSUMER_BARRIER"] = str(barrier)
        flow.launch(["Build a greeting tool", "--no-chat"], 2)
        run, _ = flow.saved()
        identity = ["--workspace", str(flow.project), "--run-dir", str(run)]
        flow.launch(["--run-dir", str(run), "--answer", "Q1=CLI", "--no-chat"], 0)
        flow.launch(["--run-dir", str(run), "--no-chat"], 2)
        flow.launch(["--run-dir", str(run), "--approve-goal", flow.saved()[1]["displayed_goal"], "--no-chat"], 0)
        barrier.touch()
        worker = subprocess.Popen(
            [*flow.entry, *identity, "--no-chat"],
            cwd=flow.root,
            env=flow.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 120
            while (
                not barrier.with_suffix(".entered").exists() and worker.poll() is None and time.monotonic() < deadline
            ):
                time.sleep(0.02)
            self.assertTrue(barrier.with_suffix(".entered").exists(), "the fake Builder never started")
            status = subprocess.run(
                [*flow.entry, *identity, "--status"], cwd=flow.root, env=flow.env, capture_output=True, text=True
            )
            offer = json.loads(status.stdout)["view"]["pause"]["stop_now"]
            argv = [word.replace("REQUEST_ID", "stop-1") for word in offer["next_command"]]
            submitted = subprocess.run(
                [*flow.entry, *argv], cwd=flow.root, env=flow.env, capture_output=True, text=True, timeout=60
            )
            self.assertEqual(0, submitted.returncode, submitted.stdout + submitted.stderr)
            # The barrier stays: the stage cannot finish on its own, so only the person's stop ends it.
            stdout, stderr = worker.communicate(timeout=90)
            self.assertEqual(2, worker.returncode, stdout + stderr)
            self.assertTrue(barrier.exists())
        finally:
            barrier.unlink(missing_ok=True)
            if worker.poll() is None:
                worker.terminate()
                worker.communicate(timeout=20)

        state = flow.saved()[1]
        self.assertEqual("PAUSED_INTERVENTION", state["status"])
        self.assertEqual(stop.STOPPED_NOW_REASON, state["stop_reason"])
        builder = [row for row in state["stages"] if row["stage"] == "builder"]
        self.assertEqual(1, len(builder))
        self.assertEqual(("person", "Stopped by you"), (builder[0]["timeout_kind"], builder[0]["timeout_reason"]))
        self.assertTrue(builder[0].get("abandoned"), "the stopped attempt is archived, its partial work kept")
        # "Resume runs it again" (#3334): the stopped Builder is next, with its partial work handed on,
        # not the Completion Reviewer.
        self.assertEqual("builder", state["next_stage"])
        self.assertIn("partial_work", state["recovery_context"])

        before = len(state["stages"])
        resumed_run = subprocess.run(
            [*flow.entry, *identity, "--resume-paused", "--no-chat"],
            cwd=flow.root,
            env=flow.env,
            capture_output=True,
            text=True,
            timeout=240,
        )
        self.assertIn(resumed_run.returncode, (0, 2), resumed_run.stdout + resumed_run.stderr)
        resumed = flow.saved()[1]
        self.assertGreater(len(resumed["stages"]), before, "resuming runs again")
        ran = [row["stage"] for row in resumed["stages"][before:] if row["stage"] != "model_preflight"]
        self.assertEqual("builder", ran[0], f"resuming runs the stopped Builder again, not {ran}")
        self.assertNotEqual(stop.STOPPED_NOW_REASON, resumed.get("stop_reason"))


if __name__ == "__main__":
    unittest.main()
