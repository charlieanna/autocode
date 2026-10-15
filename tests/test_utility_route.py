"""The utility route (#710): --utility-model serves only autocode_roles.UTILITY_STAGES."""

import argparse
import unittest

import autocode_roles as roles
import autocode_utility_route as utility_route
from units import autoplanner

from tests import test_failure_routing_cli as fixture


def _args(**values):
    return argparse.Namespace(**{"utility_model": None, "utility_reasoning_effort": None, **values})


class UtilityStages(unittest.TestCase):
    def test_listed_stages_are_known_and_never_write_code_or_judge_evidence(self):
        self.assertTrue(roles.UTILITY_STAGES)
        for stage in roles.UTILITY_STAGES:
            with self.subTest(stage=stage):
                self.assertIn(stage, roles.STAGE_JOB)
                self.assertNotIn(roles.STAGE_JOB[stage], roles.UTILITY_EXCLUDED_JOBS)
        # Every excluded job is a real job, so a renamed job cannot silently drop out of the guard.
        self.assertLessEqual(set(roles.UTILITY_EXCLUDED_JOBS), set(roles.SCREEN))

    def test_route_for_sends_only_the_listed_stages_to_the_utility_route(self):
        settings = {"roles": {}, "joint_planning": True}
        for role in ("astra", "terra", "sol", "completion", "requirements", "glm", "plan_reviewer", "resolver"):
            settings["roles"][role] = {"engine": "opencode", "model": f"openai/{role}"}
        utility_route.configure(settings, _args(utility_model="ollama/qwen3:8b"), started=False)
        state = {"settings": settings}
        without = {"settings": {**settings, "roles": {k: v for k, v in settings["roles"].items() if k != "utility"}}}
        for stage in roles.STAGE_JOB:
            with self.subTest(stage=stage):
                if stage in roles.UTILITY_STAGES:
                    self.assertEqual("utility", autoplanner.route_for(state, stage))
                else:
                    self.assertNotEqual("utility", autoplanner.route_for(state, stage))
                    # The flag changes nothing for any other stage.
                    try:
                        expected = autoplanner.route_for(without, stage)
                    except Exception as error:  # a v2-only stage pauses the same way with or without it
                        with self.assertRaises(type(error)):
                            autoplanner.route_for(state, stage)
                        continue
                    self.assertEqual(expected, autoplanner.route_for(state, stage))
        # A report repair of a utility stage keeps that stage's route.
        self.assertEqual("utility", utility_route.route(state, "recognize_workflow_report_repair"))

    def test_unset_flag_saves_no_route_and_keeps_every_stage_where_it_was(self):
        settings = {"roles": {"astra": {"model": "gpt-6-sol"}, "requirements": {"model": "gpt-6-luna"}}}
        self.assertIsNone(utility_route.configure(settings, _args(), started=False))
        self.assertNotIn("utility", settings["roles"])
        self.assertEqual("requirements", autoplanner.route_for({"settings": settings}, "recognize_workflow"))

    def test_route_copies_the_plan_reviewers_engine_provider_and_effort(self):
        reviewer = {"engine": "opencode", "provider": None, "model": "openai/gpt-6-sol", "reasoning_effort": "high"}
        reviewer["model_pinned"] = True
        settings = {"roles": {"plan_reviewer": reviewer, "astra": {"engine": "codex", "model": "gpt-6-sol"}}}
        utility_route.configure(settings, _args(utility_model="lmstudio/qwen3-4b"), started=False)
        self.assertEqual(
            {"engine": "opencode", "provider": None, "model": "lmstudio/qwen3-4b", "reasoning_effort": "high"},
            settings["roles"]["utility"],
        )
        utility_route.configure(
            settings, _args(utility_model="lmstudio/qwen3-4b", utility_reasoning_effort="low"), started=False
        )
        self.assertEqual("low", settings["roles"]["utility"]["reasoning_effort"])

    def test_refusals(self):
        with self.assertRaisesRegex(ValueError, "only available when starting a new run"):
            utility_route.configure({}, _args(utility_model="gpt-6-luna"), started=True)
        with self.assertRaisesRegex(ValueError, "needs --utility-model"):
            utility_route.configure({"roles": {}}, _args(utility_reasoning_effort="low"), started=False)


class UtilityRouteCLI(unittest.TestCase):
    """A fake run with the utility route on a second fake model: recognition goes there, nothing else."""

    command = fixture.FailureRoutingCLI.command
    setUp = fixture.FailureRoutingCLI.setUp
    call = fixture.FailureRoutingCLI.call
    status = fixture.FailureRoutingCLI.status
    trace = fixture.FailureRoutingCLI.trace
    call_order = fixture.FailureRoutingCLI.call_order
    seed = fixture.FailureRoutingCLI.seed
    approve = fixture.FailureRoutingCLI.approve
    drive = fixture.FailureRoutingCLI.drive

    UTILITY = "gpt-5.4-mini"

    def test_only_recognition_launches_on_the_utility_model_and_the_run_completes(self):
        self.seed(planner_extra=("--utility-model", self.UTILITY))
        self.drive(codes=(0,))
        self.assertEqual("TASK_COMPLETE", self.status()["status"])
        calls = self.trace()
        recognized = [row for row in calls if row["stage"] == "recognize_workflow"]
        self.assertEqual(1, len(recognized), self.call_order())
        self.assertEqual(self.UTILITY, recognized[0]["model"])
        self.assertEqual("read-only", recognized[0]["argv"][recognized[0]["argv"].index("--sandbox") + 1])
        others = [(row["stage"], row["model"]) for row in calls if row["stage"] != "recognize_workflow"]
        self.assertTrue({"terra", "sol"} <= {stage for stage, _ in others}, others)
        self.assertEqual([], [row for row in others if row[1] == self.UTILITY])
        # A saved run keeps its routes: the flag is refused before anything is saved.
        refused = self.call(
            "autocode", ["--run-dir", str(self.run), "--no-chat", "--utility-model", "gpt-6-luna"], codes=(2,)
        )
        self.assertIn("--utility-model is only available when starting a new run", refused.stdout + refused.stderr)


if __name__ == "__main__":
    unittest.main()
