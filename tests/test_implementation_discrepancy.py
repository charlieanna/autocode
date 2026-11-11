"""A person's report that the built code departs from the approved plan keeps the plan approved (#3045).

Driven through the CLI on the goal fixtures' isolated Git workspace; no model is launched.
"""

import copy
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import autocode as runner
import autocode_goals as g
import autocode_implementation_discrepancy as discrepancy
import autocode_stage_context as stage_context

from tests.test_goals import GoalTests

MESSAGE = "AC2 imports the new module inside the test body; the approved plan says it fails on base by assertion."


def load_tests(loader, tests, pattern):
    """Only this module's own tests: the class reuses GoalTests' fixture, not its tests."""
    return unittest.TestSuite(
        case(name)
        for case in (ImplementationDiscrepancyTests, DiscrepancyRecordTests)
        for name in sorted(vars(case))
        if name.startswith("test")
    )


class ImplementationDiscrepancyTests(GoalTests):
    def built(self):
        """An approved plan whose task the Builder built, paused at the person's request before the Tester."""
        self.approve()
        self.state["settings"]["roles"]["completion"] = {"model": "tester", "reasoning_effort": "high"}
        runner.apply_result(self.state, "plan_task", self.decision(), {"output": "plan"}, self.root, self.run)
        self.state["implementation"] = {
            "commands_run": ["python greet.py Ada"],
            "results": ["Hello, Ada"],
            "addressed_requirements": self.state["current_task"]["requirements"],
            "recommended_checks": ["Invalid input"],
        }
        self.state.update(status="PAUSED_REQUESTED", stop_reason="Requested checkpoint", next_stage="tester")
        return g.token(self.state["goal_contract"])

    def packet(self, stage):
        prompt, _ = stage_context.context_packet(self.state, stage, self.run / "state.json")
        return json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])

    def test_report_keeps_the_plan_approved_and_reaches_the_reviewers(self):
        token = self.built()
        self.assertEqual(0, self.invoke("--status", "--json"), self.stdout + self.stderr)
        view = json.loads(self.stdout)["view"]
        offered = view["implementation_discrepancy"]
        self.assertEqual([], offered["reports"])
        command = offered["offer"]["next_command"]
        self.assertEqual(["--review-token", token, "--report-discrepancy", "MESSAGE"], command[-4:])
        before = copy.deepcopy(self.state)
        product = (self.root / "greet.py").read_bytes()

        argv = [MESSAGE if word == "MESSAGE" else word for word in command]
        self.assertEqual(0, self.invoke(*argv), self.stdout + self.stderr)

        # The plan, the pause and everything that grants authority are as they were.
        self.assertTrue(g.approved(self.state))
        self.assertEqual(token, g.token(self.state["goal_contract"]))
        self.assertEqual(before["goal_contract"], self.state["goal_contract"])
        for key in (
            "status",
            "next_stage",
            "current_task",
            "acceptance_criteria",
            "stages",
            "history",
            "user_events",
            "findings_ledger",
            "unresolved_findings",
            "validation",
            "iteration",
        ):
            self.assertEqual(before.get(key), self.state.get(key), key)
        self.assertEqual(before["settings"]["roles"], self.state["settings"]["roles"])
        for key, value in before["settings"]["limits"].items():  # loading fills absent defaults only
            self.assertEqual(value, self.state["settings"]["limits"][key], key)
        self.assertNotIn("brief_feedback", self.state)
        self.assertEqual(product, (self.root / "greet.py").read_bytes())
        [report] = self.state["implementation_discrepancies"]
        self.assertEqual(MESSAGE, report["text"])
        self.assertEqual(token, report["contract_token"])
        self.assertEqual(self.state["current_task"]["id"], report["task_id"])
        self.assertEqual("PAUSED_REQUESTED", report["pause_status"])
        self.assertTrue(report["source_revision"])

        # Sent again, it is kept once.
        self.assertEqual(0, self.invoke(*argv), self.stdout + self.stderr)
        self.assertIn("Already saved", self.stdout)
        self.assertEqual([report], self.state["implementation_discrepancies"])

        # The view lists it and the run still waits for the person's resume.
        self.assertEqual(0, self.invoke("--status", "--json"), self.stdout + self.stderr)
        view = json.loads(self.stdout)["view"]
        self.assertEqual("resume", view["needs"]["kind"])
        [shown] = view["implementation_discrepancy"]["reports"]
        self.assertEqual((report["id"], MESSAGE, True), (shown["id"], shown["text"], shown["current"]))

        # The independent reviewers read it as an allegation to check; the Builder does not.
        for stage in ("tester", "review_checkpoint", "review_completion"):
            reported = self.packet(stage)["implementation_discrepancies"]
            self.assertEqual([MESSAGE], [row["text"] for row in reported["reports"]], stage)
            self.assertIn("allegation to check", reported["instruction"])
        self.assertNotIn("implementation_discrepancies", self.packet("builder"))

    def test_refused_reports_change_nothing(self):
        token = self.built()
        initial = copy.deepcopy(self.state)
        for name, change, argv in (
            ("stale token", {}, ("--review-token", "r9:stale", "--report-discrepancy", MESSAGE)),
            ("empty text", {}, ("--review-token", token, "--report-discrepancy", "  ")),
            ("too long", {}, ("--review-token", token, "--report-discrepancy", "x" * (discrepancy.MAX_TEXT + 1))),
            ("open question", {"pending_questions": [{"id": "Q1", "question": "Which scope?"}]}, None),
            ("waiting request", {"user_request": {"kind": "human_review"}}, None),
            (
                "held operational pause",
                {"status": "PAUSED_INTERVENTION", "pause_intent": {"held_pause": {"status": "PAUSED_TIME_LIMIT"}}},
                None,
            ),
            ("uncertain stage", {"uncertain_artifacts": [{"stage": "builder"}]}, None),
            ("budget pause", {"status": "PAUSED_TIME_LIMIT"}, None),
            ("no task yet", {"current_task": None}, None),
        ):
            with self.subTest(name):
                self.state = {**copy.deepcopy(initial), **change}
                expected = copy.deepcopy(self.state)
                code = self.invoke(*(argv or ("--review-token", token, "--report-discrepancy", MESSAGE)))
                self.assertEqual(2, code, self.stdout + self.stderr)
                self.assertNotIn("implementation_discrepancies", self.state)
                self.assertEqual(expected.get("goal_contract"), self.state.get("goal_contract"))
                self.assertEqual(expected.get("status"), self.state.get("status"))
                self.assertTrue(g.approved(self.state))

    def test_a_draft_plan_takes_feedback_not_a_report(self):
        token = self.built()
        self.assertEqual(0, self.invoke("--feedback", "Make the greeting configurable"), self.stdout + self.stderr)
        self.assertFalse(g.approved(self.state))
        self.assertEqual(2, self.invoke("--review-token", token, "--report-discrepancy", MESSAGE))
        self.assertIn("approved plan", self.stderr + self.stdout)
        self.assertNotIn("implementation_discrepancies", self.state)

    def test_the_report_needs_the_plan_token(self):
        self.built()
        with self.assertRaises(SystemExit):
            self.invoke("--report-discrepancy", MESSAGE)
        self.assertNotIn("implementation_discrepancies", self.state)


class DiscrepancyRecordTests(unittest.TestCase):
    def test_reports_follow_their_plan_and_task(self):
        state = {"goal_contract": {}, "current_task": {"id": "T2"}}
        rows = [
            {"id": "a", "contract_token": "r1:x", "task_id": "T2", "text": "same plan, this task"},
            {"id": "b", "contract_token": "r1:x", "task_id": "T1", "text": "same plan, earlier task"},
            {"id": "c", "contract_token": "r0:y", "task_id": "T2", "text": "earlier plan"},
        ]
        state["implementation_discrepancies"] = rows
        sealed = mock.patch.object(discrepancy.identity, "sealed", return_value=True)
        named = mock.patch.object(discrepancy.identity, "token", return_value="r1:x")
        with sealed, named:
            self.assertEqual(["a"], [row["id"] for row in discrepancy.current(state, "tester")])
            self.assertEqual(["a", "b"], [row["id"] for row in discrepancy.current(state, "review_completion")])
            self.assertEqual([], discrepancy.current(state, "builder"))
            shown = discrepancy.project(state)
        self.assertIsNone(shown["offer"])
        self.assertEqual([True, True, False], [row["current"] for row in shown["reports"]])
        self.assertIsNone(discrepancy.project({"status": "RUNNING"}))


if __name__ == "__main__":
    unittest.main()
