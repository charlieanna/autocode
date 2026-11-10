"""`--workflow KIND` names the job; a run without a Requirements stage is planned as a build and says so."""

import json
import unittest

import autocode_checkout_lock as checkout_lock
import autocode_workflows as workflows

from . import test_subprocess


class WorkflowPinCli(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ("--engine", "codex")

    def stages(self, state):
        return [row.get("stage") for row in state["stages"]]

    def test_a_named_workflow_is_kept_and_a_running_job_keeps_its_kind(self):
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        result = self.launch(["Build greeting", "--workflow", "build", "--chat"], 0, answers="CLI\nyes\n")
        run, state = self.saved()
        self.assertEqual("TASK_COMPLETE", state["status"])
        self.assertEqual(("build", "user"), (state["workflow"]["kind"], state["workflow"]["source"]))
        self.assertNotIn("recognize_workflow", self.stages(state))
        status = json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)
        self.assertEqual(("build", "user"), (status["view"]["workflow"], status["view"]["workflow_source"]))
        result = self.launch(["--run-dir", str(run), "--workflow", "bugfix"], 2)
        self.assertIn("already runs as 'build'", result.stderr)
        self.assertIn("Start a new run with --workflow bugfix", result.stderr)

    def test_a_run_without_requirements_is_planned_as_a_build_and_says_so(self):
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        self.launch(["Build greeting", "--chat"], 0, answers="CLI\nyes\n")
        run, state = self.saved()
        self.assertEqual(("build", "default"), (state["workflow"]["kind"], state["workflow"]["source"]))
        self.assertNotIn("recognize_workflow", self.stages(state))
        status = json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)
        self.assertEqual(("build", "default"), (status["view"]["workflow"], status["view"]["workflow_source"]))
        self.assertIn("every task without a Requirements stage is planned as a build", workflows.approval_note(state))

    def test_a_run_that_has_not_reached_its_first_stage_can_still_be_named(self):
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        other = self.project / ".autocode" / "runs" / "other-run"
        with checkout_lock.exclusive(self.project, other):
            self.launch(["Build greeting", "--chat"], 2, answers="CLI\nyes\n")
        run, state = self.saved()
        self.assertEqual([], self.stages(state))
        self.launch(["--run-dir", str(run), "--workflow", "build", "--chat"], 0, answers="CLI\nyes\n")
        _, state = self.saved()
        self.assertEqual(
            ("build", "user", "TASK_COMPLETE"),
            (state["workflow"]["kind"], state["workflow"]["source"], state["status"]),
        )
        self.assertNotIn("recognize_workflow", self.stages(state))


if __name__ == "__main__":
    unittest.main()
