"""Typed finite choices on owned, exhausted checkpoints from real fake-provider plans.

Only fixture setup seeds the planning-budget pause after the CLI has produced a
reviewed but unapproved plan. Choices, stale rejection, operational information,
reevaluation and continuation all use the public CLI. No live models.
"""

import copy
import json
import sys
import unittest

import autocode as runner
import autocode_support as support
from harness.conformance import ConformanceDriver, ConformanceGap

from . import test_operational_information as operational_fixture
from . import test_planning as planning_fixture


@unittest.skipUnless(sys.platform == "linux", "Supervised fake providers require supported Linux")
class PlanningAllowanceCLITests(unittest.TestCase):
    setUp = planning_fixture.JointFlow.setUp
    saved = planning_fixture.JointFlow.saved
    launch = planning_fixture.JointFlow.launch
    prepare = planning_fixture.JointFlow.prepare
    draft = planning_fixture.JointFlow.draft
    new_run_engine_args = ()
    status = operational_fixture.OperationalInformationCLITests.status
    inform = operational_fixture.OperationalInformationCLITests.inform
    invoke = operational_fixture.OperationalInformationCLITests.invoke
    resume = operational_fixture.OperationalInformationCLITests.resume

    def checkpoint(self, *, operational=False, grant=False):
        self.run, state = self.draft()
        self.assertEqual("approve_plan", self.status()["needs"]["kind"])
        self.original_plan = self.status()["displayed_plan"]
        # Own-fixture fault boundary: the completed real fake reviews remain
        # charged, and the plan has never been approved. No model result is forged.
        state.update(
            status="PAUSED_PLANNING_BUDGET", next_stage="astra_finalize", stop_reason="2/2 plan-review calls used"
        )
        if grant:
            # This is a seeded, already-exhausted accounting boundary, not a
            # wall-clock timeout test. The real fake plan remains unapproved.
            state.update(
                status="PAUSED_TIMEOUT_RECOVERY",
                stop_reason="Automatic recovery budget exhausted",
                automatic_recoveries_since_resume=3,
                consecutive_timeout_recoveries=3,
                automatic_timeout_recoveries=[
                    {"stage": "astra_finalize", "timeout_reason": "fixture idle watchdog"} for _ in range(3)
                ],
            )
        if operational:
            self.assertTrue(
                runner.resolver_runtime.record_operational_exhaustion(
                    runner, state, self.run, support.Paused(state["status"], state["stop_reason"])
                )
            )
        runner.write_json(self.run / "state.json", state)
        self.initial_settings = copy.deepcopy(self.payload()["settings"])
        self.client = object.__new__(ConformanceDriver)
        self.client.project, self.client.run_dir = self.project, self.run
        return self.status()

    def payload(self):
        return json.loads(self.launch(["--run-dir", str(self.run), "--status"], 0).stdout)

    def choose(self, view):
        self.assertIn("command_inputs", view["needs"], "The exhausted public action must type its N slot")
        row = view["needs"]["command_inputs"][0]
        self.assertEqual((2, 2, 3), (row["current"], row["used"], row["minimum"]))
        words, env, _ = self.client.named_command(view)
        selected = self.client.decide(
            view["needs"], words, command_inputs={"planning_review_call_limit": "4"}, view=view, env=env
        )
        self.assertNotIn("--approve-goal", selected)
        return selected, env

    def assert_unapproved(self, used=2):
        public = self.status()
        self.assertNotIn("approved_contract", public)
        self.assertFalse((self.project / "greet.py").exists())
        self.assertEqual(
            self.initial_settings, self.payload()["settings"], "Other saved limits and routes cannot change"
        )
        text = self.launch(["--run-dir", str(self.run), "--show-goal"], 0).stdout
        self.assertIn(f"{used}/4 plan-review calls used", text, "Saving an allowance must preserve charged calls")

    def test_finite_resume_choice_checks_freshness_and_never_approves_the_plan(self):
        view = self.checkpoint()
        selected, env = self.choose(view)
        self.assertIn("--resume-paused", selected)
        stale = {**env, "AUTOCODE_EXPECTED_RECOVERY_TOKEN": env["AUTOCODE_EXPECTED_RECOVERY_TOKEN"] + "stale"}
        code, launched, output = self.invoke(*selected, env=stale)
        self.assertEqual((2, False), (code, launched), output)
        self.assertIn("saved pause changed", output)
        code, launched, output = self.invoke(*selected, env=env)
        self.assertEqual((0, False), (code, launched), output)
        words, _, _ = self.client.named_command(self.status())
        self.assertEqual(["--resume-paused"], words)
        code, launched, output = self.invoke(*words)
        self.assertEqual((2, True), (code, launched), output)
        self.assertEqual("approve_plan", self.status()["needs"]["kind"])
        self.assertNotEqual(self.original_plan["token"], self.status()["displayed_plan"]["token"])
        self.assert_unapproved(used=3)

    def test_held_choice_only_saves_without_resuming_or_approving(self):
        view = self.checkpoint(operational=True)
        self.inform(view, "The operator requests total4 review calls; keep scope and plan approval unchanged")
        self.assertNotIn("command_inputs", self.status()["needs"], "Information itself cannot choose an allowance")
        code, launched, output = self.resume()
        self.assertEqual((2, False), (code, launched), output)
        held = self.status()
        self.assertEqual("held", held["information_review"]["status"])
        selected, env = self.choose(held)
        self.assertNotIn("--resume-paused", selected)
        code, launched, output = self.invoke(*selected, env=env)
        self.assertEqual((0, False), (code, launched), output)
        self.assert_unapproved()
        current = self.status()
        words, _, _ = self.client.named_command(current)
        self.assertEqual(["--resume-paused"], words)
        # Only this separately named continuation returns to the retained,
        # unread plan. The allowance-setting action above launched nothing.
        code, launched, output = self.invoke(*words)
        self.assertEqual((2, True), (code, launched), output)
        self.assertEqual("approve_plan", self.status()["needs"]["kind"])
        self.assert_unapproved(used=3)

    def test_finite_recovery_grant_replenishes_one_slot_without_other_authority(self):
        view = self.checkpoint(operational=True, grant=True)
        before = self.status()["usage"]["accounting"]["attempts"]
        self.inform(view, "The operator inspected the retained evidence; do not change any saved model or limit")
        pending = self.status()
        self.assertNotIn("command_inputs", pending["needs"])
        code, launched, output = self.resume()
        self.assertEqual((2, False), (code, launched), output)
        held = self.status()
        self.assertEqual("held", held["information_review"]["status"])
        self.assertIn("command_inputs", held["needs"])
        row = held["needs"]["command_inputs"][0]
        self.assertEqual(
            ("grant_recovery", 3, 3, 1, 3), (row["id"], row["current"], row["used"], row["minimum"], row["maximum"])
        )
        words, env, _ = self.client.named_command(held)
        for invalid in ("", "0", "1.5", "4", "9007199254740992"):
            with self.subTest(invalid=invalid), self.assertRaises(ConformanceGap):
                self.client.decide(held["needs"], words, command_inputs={"grant_recovery": invalid}, view=held, env=env)
        selected = self.client.decide(held["needs"], words, command_inputs={"grant_recovery": "1"}, view=held, env=env)
        self.assertEqual(["--resume-paused", "--grant-recovery", "1", "--expected-recovery-token", "-"], selected)
        stale = {**env, "AUTOCODE_EXPECTED_RECOVERY_TOKEN": env["AUTOCODE_EXPECTED_RECOVERY_TOKEN"] + "stale"}
        code, launched, output = self.invoke(*selected, env=stale)
        self.assertEqual((2, False), (code, launched), output)
        self.assertIn("saved pause changed", output)
        code, launched, output = self.invoke(*selected, env=env)
        self.assertEqual((2, False), (code, launched), output)
        self.assertIn("Recovery grant recorded: 1", output)
        self.assertIn("(3 -> 2 counted); history retained", output)
        current = self.status()
        self.assertNotIn("approved_contract", current)
        self.assertFalse((self.project / "greet.py").exists())
        self.assertEqual(self.initial_settings, self.payload()["settings"])
        after = {row["identity"]: row for row in current["usage"]["accounting"]["attempts"]}
        for previous in before:
            self.assertEqual(
                previous, after[previous["identity"]], "Historical attempts and their usage remain retained"
            )


if __name__ == "__main__":
    unittest.main()
