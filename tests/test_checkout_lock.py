"""Two runs in one checkout: only one run's agents work in it at a time."""
import json
import subprocess
import tempfile
import unittest

import autocode_checkout_lock as checkout_lock

from . import test_subprocess


class ExclusiveTests(unittest.TestCase):
    def test_a_second_holder_is_refused_and_told_who_holds_it(self):
        with tempfile.TemporaryDirectory() as workspace:
            with checkout_lock.exclusive(workspace, "/runs/first"):
                self.assertEqual("/runs/first", checkout_lock.holder(workspace)["run_dir"])
                with self.assertRaises(checkout_lock.CheckoutBusy) as busy:
                    with checkout_lock.exclusive(workspace, "/runs/second"):
                        self.fail("entered a held checkout")
                self.assertEqual("PAUSED_WORKSPACE_BUSY", busy.exception.status)
                self.assertIn("/runs/first", str(busy.exception))
            self.assertEqual({}, checkout_lock.holder(workspace))
            with checkout_lock.exclusive(workspace, "/runs/second"):
                pass


class InPlaceRunTests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ("--engine", "codex")

    def test_a_run_waits_while_another_run_works_in_the_checkout(self):
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        other = self.project / ".autocode" / "runs" / "other-run"
        with checkout_lock.exclusive(self.project, other):
            result = self.launch(["Build greeting", "--chat"], 2, answers="CLI\nyes\n")
            self.assertIn(f"Another AutoCode run is working in this checkout ({other}); nothing was changed",
                          result.stderr)
            self.assertIn("without --in-place", result.stderr)
            run, state = self.saved()
            self.assertEqual("RUNNING", state["status"], "a busy checkout changes nothing in the waiting run")
            self.assertIsNone(state["base_commit"], "the other run's unfinished files are not where this run starts")
            (self.project / "notes.txt").write_text("the other run's work\n")
            self.assertEqual([], [row for row in state["stages"] if not row.get("runner_owned")],
                             "no agent may run while another run holds the checkout")
            self.assertFalse((self.project / "greet.py").exists())
            status = json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)
            self.assertEqual("RUNNING", status["status"])
        # The same kind of command, once the other run has stopped, simply continues.
        self.launch(["--run-dir", str(run), "--chat"], 0, answers="CLI\nyes\n")
        _, state = self.saved()
        self.assertEqual("TASK_COMPLETE", state["status"])
        self.assertEqual(["notes.txt"], subprocess.run(
            ["git", "ls-tree", "--name-only", state["base_commit"]], cwd=self.project, check=True,
            capture_output=True, text=True).stdout.split(), "the run starts from the checkout as it first holds it")
        self.assertEqual({}, checkout_lock.holder(self.project))


if __name__ == "__main__":
    unittest.main()
