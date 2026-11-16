"""Typed finite choices on owned, exhausted checkpoints from real fake-provider plans.

Only fixture setup seeds the planning-budget pause after the CLI has produced a
reviewed but unapproved plan. Choices, stale rejection, operational information,
reevaluation and continuation all use the public CLI. No live models.
"""

import copy
import hashlib
import json
import sys
import unittest

import autocode as runner
import autocode_support as support
from harness.conformance import ConformanceDriver

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
            status="PAUSED_PLANNING_BUDGET", next_stage="finalize_plan", stop_reason="2/2 plan-review calls used"
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
                    {"stage": "finalize_plan", "timeout_reason": "fixture idle watchdog"} for _ in range(3)
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

    def test_saved_inherited_review_false_pause_resumes_under_the_unchanged_allowance(self):
        self.run, state = self.draft()
        self.assertEqual("approve_plan", self.status()["needs"]["kind"])
        planning = state["planning"]
        charges = planning["review_charges"]
        self.assertEqual(2, len(charges), "The fixture supplies two completed real fake-provider reviews")
        self.assertNotEqual(charges[0], charges[1])
        self.assertEqual(2, planning["plan_review_calls"])
        # Owned fault boundary from #2087: A and B were reported in two cycles of this turn;
        # the current cycle inherited B. Reduced-depth activation counted A + B + inherited B.
        # Keep the actual review reports, stage records and usage; only seed the accounting shape.
        turn = "fixture-inherited-review-turn"
        history = state.setdefault("planning_history", [])
        for charge in charges:
            archived = copy.deepcopy(planning)
            archived.update(plan_review_calls=1, review_charges=[charge], turn_event_id=turn)
            archived.pop("inherited_review", None)
            archived.pop("review_depth_reduced", None)
            history.append(archived)
        planning.update(
            plan_review_calls=3,
            review_charges=[charges[-1]],
            turn_event_id=turn,
            inherited_review={
                "plan_review_calls": 1,
                "cycle": len(history) - 1,
                "reports": ["review_plan", "revise_plan"],
                "next_stage": "finalize_plan",
            },
            review_depth_reduced=True,
            review_calls_before_policy=2,
            review_call_limit=3,
            review_call_limit_origin="runner_default",
            revision_review_renewals=1,
        )
        state["workflow"].update(kind="build", clarity="clear", brief_size="small")
        state.update(
            status="PAUSED_PLANNING_BUDGET",
            phase="PLANNING",
            next_stage="finalize_plan",
            stop_reason="3/3 plan-review calls used",
        )
        runner.write_json(self.run / "state.json", state)
        state_file = self.run / "state.json"
        initial_history = copy.deepcopy(state["planning_history"])
        initial_hash = hashlib.sha256(state_file.read_bytes()).hexdigest()
        before = self.payload()
        view = before["view"]
        self.client = object.__new__(ConformanceDriver)
        self.client.project, self.client.run_dir = self.project, self.run
        self.assertNotIn("command_inputs", view["needs"], "A duplicated charge cannot require a larger allowance")
        words, env, _ = self.client.named_command(view)
        self.assertEqual(["--resume-paused"], words)
        self.assertNotIn("--planning-review-call-limit", json.dumps(view["needs"]))
        self.status()
        self.assertEqual(initial_hash, hashlib.sha256(state_file.read_bytes()).hexdigest(), "Status must be read-only")
        old_attempts = {row["identity"]: row for row in view["usage"]["accounting"]["attempts"]}
        code, launched, output = self.invoke(*words, env=env)
        self.assertEqual((2, True), (code, launched), output)
        current = self.payload()
        after = current["view"]
        self.assertEqual("approve_plan", after["needs"]["kind"], after["stop_reason"])
        self.assertEqual(before["settings"], current["settings"])
        _, saved = self.saved()
        self.assertEqual(initial_history, saved["planning_history"])
        self.assertEqual(3, saved["planning"]["review_call_limit"])
        self.assertEqual("runner_default", saved["planning"]["review_call_limit_origin"])
        self.assertEqual(
            3, saved["planning"]["plan_review_calls"], "Two unique earlier reviews plus one new final review"
        )
        attempts = {row["identity"]: row for row in after["usage"]["accounting"]["attempts"]}
        for identity, original in old_attempts.items():
            self.assertEqual(original, attempts[identity], "Historical reported usage must stay intact")
        new_reviews = [row for identity, row in attempts.items() if identity not in old_attempts]
        self.assertEqual(["finalize_plan"], [row["stage"] for row in new_reviews])
        self.assertFalse(new_reviews[0]["report_only"])
        self.assertNotIn("approved_contract", after)
        self.assertFalse((self.project / "greet.py").exists(), "Resume and review cannot approve the Builder")
        code, launched, output = self.invoke("--approve-goal", "r0:not-the-plan-that-was-shown")
        self.assertEqual((2, False), (code, launched), output)
        self.assertEqual("approve_plan", self.status()["needs"]["kind"])
        approval, approval_env, _ = self.client.named_command(after)
        code, launched, output = self.invoke(*approval, env=approval_env)
        self.assertEqual((0, False), (code, launched), output)
        self.assertFalse((self.project / "greet.py").exists())
        code, launched, output = self.invoke("--no-chat")
        self.assertEqual((0, True), (code, launched), output)
        self.assertEqual("TASK_COMPLETE", self.status()["status"])
        self.assertTrue((self.project / "greet.py").is_file())

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

    def test_managed_planning_exhaustion_retains_evidence_without_a_machinery_question(self):
        self.checkpoint(operational=True)
        before = self.payload()
        view = before["view"]
        self.assertEqual("blocked", view["recovery"]["category"])
        self.assertIsNone(view["needs"])
        self.assertIsNone(view["next_command"])
        self.assertIsNone(view["chat"]["input"])
        self.assertIsNone(view["chat"]["gate"])
        code, launched, output = self.resume()
        self.assertEqual((2, False), (code, launched), output)
        after = self.payload()
        self.assertEqual(before["settings"], after["settings"])
        self.assertEqual(
            before["view"]["usage"]["accounting"]["attempts"], after["view"]["usage"]["accounting"]["attempts"]
        )
        self.assertFalse((self.project / "greet.py").exists())

    def test_explicit_operator_allowance_only_saves_without_resuming_or_approving(self):
        self.checkpoint(operational=True)
        self.assertIsNone(self.status()["needs"])
        # The public CLI still accepts an explicit operator decision. No client
        # invents an action, and no person is asked to repair internal machinery.
        code, launched, output = self.invoke("--planning-review-call-limit", "4")
        self.assertEqual((0, False), (code, launched), output)
        self.assert_unapproved()
        current = self.status()
        self.assertIsNone(current["needs"])
        self.assertIsNone(current["next_command"])
        self.assertEqual("blocked", current["recovery"]["category"])
        self.assertFalse((self.project / "greet.py").exists())

    def test_restarted_reduced_plan_keeps_the_explicit_finite_allowance(self):
        self.checkpoint()
        # Seed only the reduced-depth classification at this owned fault
        # boundary. The plan and its two actual fake reviews are unchanged.
        run, state = self.saved()
        state["workflow"].update(kind="build", clarity="clear", brief_size="small")
        state["planning"]["review_depth_reduced"] = True
        runner.write_json(run / "state.json", state)
        selected, env = self.choose(self.status())
        code, launched, output = self.invoke(*selected, env=env)
        self.assertEqual((0, False), (code, launched), output)
        self.assert_unapproved()

        # Feedback begins another real fake-provider planning cycle. Its
        # reviews must use the remaining two calls, not the default 2/2 hold.
        before = self.status()["usage"]["accounting"]["attempts"]
        self.launch(["--run-dir", str(run), "--feedback", "Print the greeting on its own line."], 0)
        self.launch(["--run-dir", str(run), "--no-chat"], 2)
        current = self.status()
        self.assertEqual("approve_plan", current["needs"]["kind"], current["stop_reason"])
        self.assert_unapproved(used=4)
        attempts = current["usage"]["accounting"]["attempts"]
        old_ids = {row["identity"] for row in before}
        reviews = [
            row
            for row in attempts
            if row["identity"] not in old_ids
            and row["stage"] in ("review_plan", "finalize_plan")
            and not row["report_only"]
            and not row["runner_owned"]
        ]
        self.assertEqual(2, len(reviews))
        self.assertEqual(self.initial_settings, self.payload()["settings"])

    def test_explicit_operator_grant_replenishes_one_slot_without_other_authority(self):
        self.checkpoint(operational=True, grant=True)
        before = self.status()["usage"]["accounting"]["attempts"]
        self.assertIsNone(self.status()["needs"])
        self.assertIsNone(self.status()["next_command"])
        code, launched, output = self.invoke("--resume-paused", "--grant-recovery", "0")
        self.assertEqual((2, False), (code, launched), output)
        code, launched, output = self.invoke("--resume-paused", "--grant-recovery", "1")
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
