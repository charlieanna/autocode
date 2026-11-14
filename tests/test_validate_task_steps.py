"""A validate task's new steps are run when their author writes them (autocode_validate_task_steps).

A real run (Claude models, 2026-11-14) added gzip support with a committed binary fixture. The code was
right. The Completion Reviewer handed the Tester a validate task and wrote one of its steps itself:
``assert 'gzip' not in s`` over a test file whose approved test name contains "gzip". The Tester's own check
passed, the runner's replay of the planned step exited 1, the report was refused ("A Validator report cannot
change it"), and the Investigator could only ask the person to have the Completion Reviewer replace the step.

The step is now run in the runner's clean copy when the Completion Reviewer or the Resolver writes it, and a
failure refuses that author's report, which the runner sends back to that stage as a report repair. The
runner cases apply a reviewer's decision through the runner's result application in a Git project, running
the real commands, with no provider.
"""

import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode as runner
import autocode_support as support
import autocode_validate_task_steps as steps
from goal_fixtures import approve_fixture, seed_greeting_workspace

SUITE = "python3 -m unittest -v"
# The real run's step, over this fixture's test file: the word it forbids is in an approved test's name.
WRONG = "python3 -c \"s=open('test_greeting.py').read();assert 'greets' not in s;print('ok')\""
RIGHT = "python3 -c \"s=open('test_greeting.py').read();assert 'open(' not in s;print('ok')\""
MISSING = 'python3 -c "import a_module_this_project_never_had"'


def task(kind, *plan):
    return {"kind": kind, "validation_plan": list(plan)}


class NewStepTests(unittest.TestCase):
    """Which steps nothing has run yet: only those are run when the task is written."""

    def state(self, *passed, revision="r1"):
        return {
            "goal_contract": {
                "body": {
                    "acceptance_criteria": [{"id": "C1", "verification_method": f"Run `{SUITE}` and read its output"}],
                    "initial_task": {"validation_plan": ["python3 -m unittest test_greeting -v"]},
                }
            },
            "validation": {
                "source_revision": revision,
                "check_replay": {"checks": [{"command": command, "exit_code": 0} for command in passed]},
            },
        }

    def test_only_a_validate_tasks_steps_are_new(self):
        self.assertEqual([], steps.new_steps(self.state(), task("implement", WRONG), "r1"))
        self.assertEqual([], steps.new_steps(self.state(), None, "r1"))
        self.assertEqual([(WRONG, WRONG)], steps.new_steps(self.state(), task("validate", WRONG), "r1"))

    def test_a_step_in_prose_is_not_a_command(self):
        plan = task("validate", "Read test_greeting.py and confirm it opens no file", f"Run `{RIGHT}`")
        self.assertEqual([(f"Run `{RIGHT}`", RIGHT)], steps.new_steps(self.state(), plan, "r1"))

    def test_the_approved_plans_commands_are_not_new(self):
        plan = task("validate", SUITE, "python3 -m unittest test_greeting -v", RIGHT, RIGHT)
        self.assertEqual([(RIGHT, RIGHT)], steps.new_steps(self.state(), plan, "r1"))

    def test_a_command_the_last_validation_passed_on_this_source_is_not_new(self):
        plan = task("validate", RIGHT, WRONG)
        self.assertEqual([(WRONG, WRONG)], steps.new_steps(self.state(RIGHT), plan, "r1"))
        # On other source the earlier pass says nothing.
        self.assertEqual([(RIGHT, RIGHT), (WRONG, WRONG)], steps.new_steps(self.state(RIGHT), plan, "r2"))
        failed = self.state()
        failed["validation"]["check_replay"]["checks"] = [{"command": RIGHT, "exit_code": 1}]
        self.assertEqual([(RIGHT, RIGHT), (WRONG, WRONG)], steps.new_steps(failed, plan, "r1"))

    def test_a_replay_of_recorded_checks_runs_nothing(self):
        state = {**self.state(), "settings": {"replay_recording": {"checks": "recorded"}}}

        def never(*_args, **_kwargs):
            raise AssertionError("no command may run")

        with tempfile.TemporaryDirectory() as temp:
            steps.check(state, task("validate", WRONG), {"source_revision": "r1"}, temp, temp, never, base_run=never)


class RunnerTests(unittest.TestCase):
    """The Completion Reviewer's and the Resolver's validate task, through the runner's result application."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        environment = patch.dict(os.environ, {"AUTOCODE_HOME": str(self.root / "registry-home")})
        environment.start()
        self.addCleanup(environment.stop)
        self.git("init", "-q")
        (self.root / ".gitignore").write_text(".autocode/\nregistry-home/\n__pycache__/\n")
        self.git("add", "-A")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "fixture")
        self.run = self.root / ".autocode/runs/fixture"
        self.run.mkdir(parents=True)
        seed_greeting_workspace(self.root)
        self.state = {
            "version": 2,
            "workspace": str(self.root),
            "task": "Greet",
            "status": "RUNNING",
            "iteration": 1,
            "stages": [],
            "history": [],
            "acceptance_criteria": [],
            "base_commit": self.git("rev-parse", "HEAD"),
            "settings": {
                "roles": {r: {"model": f"model-{r}"} for r in ("task_planner", "builder", "tester")},
                "context_soft_tokens": 1000,
                "headroom": {"enabled": False},
            },
        }
        approve_fixture(self.state, runner.goals)
        runner.lifecycle.assign_task(
            self.state, self.decision("CONTINUE", "implement", SUITE), support.snapshot(self.root)
        )

    def git(self, *arguments):
        done = subprocess.run(["git", "-C", str(self.root), *arguments], check=True, capture_output=True, text=True)
        return done.stdout.strip()

    def decision(self, status, kind, *plan, output=None):
        if output:
            (self.run / output).write_text(json.dumps({"status": status}))
        contract = self.state["goal_contract"]
        return {
            "contract_revision": contract["revision"],
            "contract_hash": contract["hash"],
            "task_id": self.state.get("current_task", {}).get("id", ""),
            "deferred_backlog": [],
            "user_request": {
                "kind": "none",
                "discovered": "",
                "impact": "",
                "decision_needed": "",
                "options": [],
                "proposed_delta": "",
            },
            "status": status,
            "next_objective": "Show that the tests open no file",
            "acceptance_criteria": [
                {**row, "status": "unverified", "evidence": "event:check"} for row in self.state["acceptance_criteria"]
            ],
            "next_task": {
                "kind": kind,
                "milestone_id": "M1",
                "requirements": ["Revalidate the greeting without changing it"],
                "acceptance_criteria": ["C1"],
                "validation_plan": list(plan),
                "findings": [],
            },
            "findings": [
                {"severity": "high", "finding": "Empty names are accepted", "evidence": "event:check", "blocking": True}
            ]
            if status == "REWORK"
            else [],
            "finding_dispositions": [],
            "agreed_limitations": [],
            "evidence": ["event:check"],
            "blocker": "",
            "plan": ["Recheck"],
            "affected_paths": ["greet.py"],
        }

    def apply(self, stage, value, output):
        record = {"output": str(self.run / output), "source_revision": support.snapshot(self.root)["revision"]}
        with patch.object(runner, "run_role", side_effect=AssertionError("no agent may launch")):
            runner.apply_result(self.state, stage, value, record, self.root, self.run)

    def ran(self):
        """The directories of the step runs, one for each task whose steps the runner ran."""
        return sorted((self.run / "check-replay").glob("task-steps-*"))

    def refused(self, stage, value, output):
        before = copy.deepcopy(self.state)
        with self.assertRaises(ValueError) as refused:
            self.apply(stage, value, output)
        # The runner's own progress records aside, the run is where it was: no task, no route, no evidence.
        self.assertEqual(before, {key: self.state[key] for key in before})
        return str(refused.exception)

    def test_a_step_that_fails_on_the_reviewed_code_is_refused_with_its_output(self):
        value = self.decision("CONTINUE", "validate", SUITE, WRONG, output="review-01.json")
        reason = self.refused("review_completion", value, "review-01.json")
        self.assertTrue(reason.startswith(f"next_task.validation_plan `{WRONG}` exited 1 when the runner"), reason)
        self.assertIn("AssertionError", reason)
        self.assertIn("a Tester cannot change it", reason)
        self.assertIn("If the step is wrong, reword it", reason)
        # The repaired report, with a step that passes on this code, is the task the Tester gets.
        value = self.decision("CONTINUE", "validate", SUITE, RIGHT, output="review-02.json")
        self.apply("review_completion", value, "review-02.json")
        self.assertEqual("validate", self.state["current_task"]["kind"])
        self.assertIn(RIGHT, self.state["current_task"]["validation_plan"])

    def test_the_workspace_is_not_where_the_step_runs(self):
        marker = "python3 -c \"open('ran-here.txt','w').write('x')\""
        self.apply(
            "review_completion",
            self.decision("CONTINUE", "validate", marker, output="review-01.json"),
            "review-01.json",
        )
        self.assertFalse((self.root / "ran-here.txt").exists())
        self.assertEqual(1, len(self.ran()))
        self.assertEqual("validate", self.state["current_task"]["kind"])

    def test_an_implement_tasks_step_may_fail_until_the_builder_works(self):
        value = self.decision("CONTINUE", "implement", SUITE, WRONG, output="review-01.json")
        self.apply("review_completion", value, "review-01.json")
        self.assertEqual("builder", self.state["next_stage"])
        self.assertEqual([], self.ran())

    def test_a_failure_the_original_code_has_identically_is_not_the_steps(self):
        value = self.decision("CONTINUE", "validate", MISSING, output="review-01.json")
        self.apply("review_completion", value, "review-01.json")
        self.assertIn(MISSING, self.state["current_task"]["validation_plan"])
        # It ran in the clean copy and then on the original code, where it fails the same way.
        (ran,) = self.ran()
        self.assertEqual(["base-01", "check"], sorted(path.name for path in (ran / "step-01").iterdir()))

    def test_the_resolvers_validate_task_is_refused_too(self):
        self.apply(
            "review_completion",
            self.decision("REWORK", "implement", SUITE, output="review-01.json"),
            "review-01.json",
        )
        self.assertEqual("resolve", self.state["next_stage"])
        diagnosis = {
            **self.decision("REWORK", "validate", f"Run `{WRONG}` and read it", output="resolve-01.json"),
            "findings": [],
            "diagnosis": "The code is right; the criterion has no executed evidence",
        }
        reason = self.refused("resolve", diagnosis, "resolve-01.json")
        self.assertIn(f"and read it` plans the command `{WRONG}`, which exited 1 when the runner ran it", reason)
        diagnosis["next_task"]["validation_plan"] = [RIGHT]
        self.apply("resolve", diagnosis, "resolve-01.json")
        self.assertIn(RIGHT, self.state["current_task"]["validation_plan"])


if __name__ == "__main__":
    unittest.main()
