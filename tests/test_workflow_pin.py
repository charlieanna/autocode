"""`--workflow KIND` names the job; recognition is reported with a way to override it."""
import json
import unittest

from . import test_subprocess
import autocode_checkout_lock as checkout_lock
import autocode_workflows as workflows


class WorkflowPinCli(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ("--engine", "codex")

    def stages(self, state):
        return [row.get("stage") for row in state["stages"]]

    def test_a_named_workflow_skips_the_recognizer_and_a_running_job_keeps_its_kind(self):
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        result = self.launch(["Build greeting", "--workflow", "build", "--chat"], 0, answers="CLI\nyes\n")
        run, state = self.saved()
        self.assertEqual("TASK_COMPLETE", state["status"])
        self.assertEqual(("build", "user"), (state["workflow"]["kind"], state["workflow"]["source"]))
        self.assertNotIn(workflows.STAGE, self.stages(state))
        self.assertNotIn("Workflow: build", result.stdout, "nothing was recognized, so nothing is reported")
        status = json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)
        self.assertEqual(("build", "user"), (status["view"]["workflow"], status["view"]["workflow_source"]))
        result = self.launch(["--run-dir", str(run), "--workflow", "bugfix"], 2)
        self.assertIn("already runs as 'build'", result.stderr)
        self.assertIn("Start a new run with --workflow bugfix", result.stderr)

    def test_recognition_is_reported_with_its_reason_and_the_override(self):
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        result = self.launch(["Build greeting", "--chat"], 0, answers="CLI\nyes\n")
        self.assertIn("Workflow: build. Offline fixture: every request is treated as a build.", result.stdout)
        self.assertIn("Not what you meant? Start again with --workflow build|bugfix|review|design|discuss.",
                      result.stdout)
        _, state = self.saved()
        self.assertEqual(("build", "model"), (state["workflow"]["kind"], state["workflow"]["source"]))
        self.assertIn(workflows.STAGE, self.stages(state))

    def test_a_run_that_has_not_reached_its_recognizer_can_still_be_named(self):
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        other = self.project / ".autocode" / "runs" / "other-run"
        with checkout_lock.exclusive(self.project, other):
            self.launch(["Build greeting", "--chat"], 2, answers="CLI\nyes\n")
        run, state = self.saved()
        self.assertEqual(workflows.STAGE, state["next_stage"])
        self.launch(["--run-dir", str(run), "--workflow", "build", "--chat"], 0, answers="CLI\nyes\n")
        _, state = self.saved()
        self.assertEqual(("build", "user", "TASK_COMPLETE"),
                         (state["workflow"]["kind"], state["workflow"]["source"], state["status"]))
        self.assertNotIn(workflows.STAGE, self.stages(state))


if __name__ == "__main__":
    unittest.main()
