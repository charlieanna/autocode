"""Terminal exhaustion refuses obsolete CLI retries; legacy domain guards remain explicit."""

import copy
import json
import unittest

import autocode_builder_recovery as recovery

from . import test_subprocess as flow


class SerialRecoveryTests(unittest.TestCase):
    new_run_engine_args = flow.SubprocessFlow.new_run_engine_args
    setUp = flow.SubprocessFlow.setUp
    launch = flow.SubprocessFlow.launch
    saved = flow.SubprocessFlow.saved

    def status(self, run):
        return json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)

    def stopped_run(self):
        self.env["AUTOCODE_FIXTURE_MODE"] = "stalled"
        self.launch(["Build greeting", "--chat", "--pin-model-role", "builder"], 2, answers="CLI\nno\n")
        run, _ = self.saved()
        # Isolate the Builder allowance from the separate stalled-review policy.
        self.launch(
            [
                "--run-dir",
                str(run),
                "--approve-goal",
                self.status(run)["contract_token"],
                "--max-milestone-stalled-reviews",
                "0",
                "--no-chat",
            ],
            0,
        )
        self.launch(["--run-dir", str(run), "--no-chat"], 2)
        return run

    def test_terminal_outcome_refuses_retry_tokens_before_any_settings_change(self):
        run = self.stopped_run()
        before = self.status(run)
        view = before["view"]
        self.assertEqual("PAUSED_INTERVENTION", view["status"])
        self.assertEqual("could_not_finish", view["recovery"]["outcome"]["result"])
        source = (self.project / "greet.py").read_bytes()
        args = ["--run-dir", str(run), "--resume-paused", "--retry-builder", "M1", "--no-chat"]
        for token in ("old", view["recovery"]["token"]):
            with self.subTest(token=token):
                self.launch([*args, "--expected-recovery-token", token, "--builder-reasoning-effort", "low"], 2)
                after = self.status(run)
                for field in ("settings", "contract_token"):
                    self.assertEqual(before[field], after[field])
                for field in ("cost", "usage", "recovery"):
                    self.assertEqual(view[field], after["view"][field])
                self.assertEqual(source, (self.project / "greet.py").read_bytes())

    def test_terminal_retry_with_pause_after_stage_cannot_requeue_a_builder(self):
        run = self.stopped_run()
        before = self.status(run)["view"]
        self.assertEqual("could_not_finish", before["recovery"]["outcome"]["result"])
        self.launch(
            ["--run-dir", str(run), "--resume-paused", "--retry-builder", "M1", "--pause-after-stage", "--no-chat"], 2
        )
        after = self.status(run)["view"]
        self.assertEqual("PAUSED_INTERVENTION", after["status"])
        self.assertIsNone(after["next_command"])
        self.assertTrue(all(not action.get("next_command") for action in after["recovery"]["actions"]))
        self.assertEqual(before["usage"], after["usage"])
        self.assertEqual(before["recovery"]["outcome"], after["recovery"]["outcome"])

    def test_repaired_provider_and_retry_flags_cannot_restart_terminal_exhaustion(self):
        run = self.stopped_run()
        before = self.status(run)
        view = before["view"]
        self.assertEqual("could_not_finish", view["recovery"]["outcome"]["result"])
        source = (self.project / "greet.py").read_bytes()
        # Changing the fixture to a successful provider supplies no new task authority.
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        args = ["--run-dir", str(run), "--resume-paused", "--no-chat"]
        for extra in ([], ["--retry-builder", "M2"], ["--retry-builder", "M1"]):
            with self.subTest(extra=extra):
                self.launch([*args, *extra], 2)
                after = self.status(run)
                self.assertEqual("PAUSED_INTERVENTION", after["status"])
                self.assertEqual(before["settings"], after["settings"])
                self.assertEqual(before["contract_token"], after["contract_token"])
                for field in ("cost", "usage", "recovery"):
                    self.assertEqual(view[field], after["view"][field])
                self.assertEqual(source, (self.project / "greet.py").read_bytes())


class SerialRecoveryGuards(unittest.TestCase):
    def state(self):
        state = {
            "status": "PAUSED_BUILDER_RETRY_LIMIT",
            "next_stage": "builder",
            "settings": {
                "builder_retry": {"enabled": True},
                "roles": {"builder": {"model": "openai/gpt-6-sol", "model_pinned": True}},
            },
            "goal_contract": {"hash": "approved"},
            "current_task": {"id": "t", "milestone_id": "M1", "kind": "implement"},
        }
        state["builder_retries"] = {recovery.policy.key(state): {"action": "pause", "failures": ["failure"]}}
        return state

    def test_uncertain_or_wrong_milestone_attempt_is_not_changed(self):
        variants = [
            ({"active_stage": {"stage": "builder"}}, ["M1"]),
            ({"active_runner_check": {"stage": "tester"}}, ["M1"]),
            ({"pending_report_repair": {"stage": "builder"}}, ["M1"]),
            ({"parent_run": "/parent"}, ["M1"]),
            ({"status": "PAUSED_PERMISSION"}, ["M1"]),
            ({"resolution_request": {"contract_hash": "old"}}, ["M1"]),
            ({}, ["M2"]),
            ({}, ["M1", "M1"]),
        ]
        for change, selected in variants:
            with self.subTest(change=change, selected=selected):
                state = self.state()
                state.update(change)
                original = copy.deepcopy(state)
                with self.assertRaises(ValueError):
                    recovery.request_serial_retry(state, selected)
                self.assertEqual(original, state)

    def test_pending_repair_returns_to_resolver_and_does_not_erase_failures(self):
        state = self.state()
        state["current_task"]["kind"] = "validate"
        state["resolution_request"] = {"contract_hash": "approved"}
        recovery.request_serial_retry(state, ["M1"])
        self.assertEqual("resolve", state["next_stage"])
        self.assertEqual(["failure"], state["builder_retries"][recovery.policy.key(state)]["failures"])
        self.assertTrue(state["settings"]["roles"]["builder"]["model_pinned"])
        with self.assertRaises(ValueError):
            recovery.request_serial_retry(state, ["M1"])
