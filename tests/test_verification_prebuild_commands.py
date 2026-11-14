"""Correct a known verification command before another build/check starts."""

import copy
import json
import shlex
import sys
import unittest
from types import SimpleNamespace

import autocode_verification_config as configuration

from . import test_bugfix_workflow as bugfix


class PrebuildCommandGuards(unittest.TestCase):
    def test_requested_prebuild_pauses_accept_and_record_the_correction(self):
        for status in ("PAUSED_REQUESTED", "PAUSED_INTERVENTION"):
            for stage in ("orchestrator", "builder", "write_acceptance_tests"):
                with self.subTest(status=status, stage=stage):
                    state = {
                        "status": status,
                        "next_stage": stage,
                        "settings": {"regression": {"test_command": "old suite"}},
                        "goal_contract": {"hash": "approved"},
                    }
                    args = SimpleNamespace(test_command="new suite", resume_paused=True)
                    configuration.configure_resume(state, state["settings"], args)
                    self.assertEqual("new suite", state["settings"]["regression"]["test_command"])
                    self.assertEqual(stage, state["next_stage"])
                    self.assertEqual({"hash": "approved"}, state["goal_contract"])
                    self.assertEqual("old suite", state["user_events"][0]["previous"]["test_command"])
                    self.assertEqual("new suite", state["user_events"][0]["current"]["test_command"])

    def test_prebuild_correction_still_requires_resume_and_reconciled_attempts(self):
        for stage in ("orchestrator", "builder", "write_acceptance_tests"):
            for key in (
                None,
                "active_stage",
                "active_runner_check",
                "runner_check",
                "uncertain_artifacts",
                "pending_report_repair",
            ):
                with self.subTest(stage=stage, key=key):
                    state = {"status": "PAUSED_REQUESTED", "next_stage": stage, "settings": {"regression": {}}}
                    if key:
                        state[key] = {"stage": stage}
                    before = copy.deepcopy(state)
                    args = SimpleNamespace(test_command="new suite", resume_paused=key is not None)
                    with self.assertRaises(ValueError):
                        configuration.configure_resume(state, state["settings"], args)
                    self.assertEqual(before, state)

    def test_completion_and_operational_holds_cannot_take_the_prebuild_shortcut(self):
        for status, stage in (
            ("PAUSED_REQUESTED", "review_completion"),
            ("PAUSED_REQUESTED", "resolve"),
            ("PAUSED_TIMEOUT_RECOVERY", "builder"),
            ("PAUSED_ACCEPTANCE_TESTS", "orchestrator"),
        ):
            with self.subTest(status=status, stage=stage):
                state = {"status": status, "next_stage": stage, "settings": {"regression": {}}}
                before = copy.deepcopy(state)
                with self.assertRaises(ValueError):
                    configuration.configure_resume(
                        state, state["settings"], SimpleNamespace(test_command="new suite", resume_paused=True)
                    )
                self.assertEqual(before, state)


class PrebuildCommandCLI(unittest.TestCase):
    new_run_engine_args = ()
    setUp = bugfix.BugfixWorkflow.setUp
    prepare = bugfix.BugfixWorkflow.prepare
    draft = bugfix.BugfixWorkflow.draft
    launch = bugfix.BugfixWorkflow.launch
    saved = bugfix.BugfixWorkflow.saved
    builder_writes = bugfix.BugfixWorkflow.builder_writes

    def status(self, run):
        return json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)

    def test_prebuild_correction_reaches_completion_only_through_the_new_proof(self):
        self.builder_writes(bugfix.references.BUGFIX_REFERENCE)
        run, approved = self.draft()
        self.launch(["--run-dir", str(run), "--approve-goal", approved["displayed_goal"]], 0)
        self.launch(["--run-dir", str(run), "--no-chat", "--pause-after-stage"], 2)
        before = self.status(run)
        self.assertEqual("builder", before["next_stage"])
        suite = f"{shlex.quote(sys.executable)} -m unittest -v test_greet"
        regression = f"{shlex.quote(sys.executable)} -m unittest -v test_greet.TestGreet.test_blank_name_rejected"
        self.launch(
            [
                "--run-dir",
                str(run),
                "--resume-paused",
                "--no-chat",
                "--pause-after-stage",
                "--test-command",
                suite,
                "--regression-command",
                regression,
            ],
            2,
        )
        after = self.status(run)
        self.assertEqual("tester", after["next_stage"], after.get("view", {}).get("stop_reason"))
        self.assertEqual(before["contract_token"], after["contract_token"])
        for key in ("roles", "limits", "builder_retry"):
            self.assertEqual(before["settings"][key], after["settings"][key])
        self.assertEqual(suite, after["settings"]["regression"]["test_command"])
        self.launch(["--run-dir", str(run), "--resume-paused", "--no-chat"], 0)
        final = self.saved()[1]
        self.assertEqual("TASK_COMPLETE", self.status(run)["status"])
        self.assertEqual("PASS", final["regression_proof"]["verdict"])
        self.assertEqual(suite, final["regression_proof"]["commands"]["suite"])
        self.assertEqual(regression, final["regression_proof"]["commands"]["regression"])
        self.assertEqual(1, sum(e["kind"] == "verification_commands_changed" for e in final["user_events"]))
