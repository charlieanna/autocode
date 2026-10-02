"""Adaptive planning decisions (autocode_adaptive_planning): pure functions over state and plans."""
import unittest

import autocode_adaptive_planning as adaptive
import autocode_goals as goals
import autocode_workflows as workflows
from units import autoplanner

ON = {"settings": {"adaptive_planning": True}}
OFF = {"settings": {}}


def body(milestones=1, paths_each=1, criteria=1, kind="implement", questions=()):
    rows = [{"id": f"M{i}", "affected_paths": [f"m{i}/f{j}.py" for j in range(paths_each)],
             "depends_on": [f"M{i - 1}"] if i else []} for i in range(milestones)]
    return {"milestones": rows, "acceptance_criteria": [{"id": f"C{i}"} for i in range(criteria)],
            "initial_task": {"kind": kind, "affected_paths": rows[0]["affected_paths"] if rows else []},
            "open_blocking_questions": list(questions)}


class EntryStage(unittest.TestCase):
    def test_clear_build_goes_straight_to_the_planner(self):
        value = {"workflow": "build", "clarity": "clear"}
        self.assertEqual(adaptive.entry_stage(ON, value, "requirements_gather", "astra_discovery"), "astra_discovery")

    def test_vague_build_keeps_requirements(self):
        value = {"workflow": "build", "clarity": "vague"}
        self.assertEqual(adaptive.entry_stage(ON, value, "requirements_gather", "astra_discovery"), "requirements_gather")

    def test_without_the_setting_nothing_changes(self):
        value = {"workflow": "build", "clarity": "clear"}
        self.assertEqual(adaptive.entry_stage(OFF, value, "requirements_gather", "astra_discovery"), "requirements_gather")

    def test_recognition_saves_clarity_and_routes_a_clear_build_to_the_planner(self):
        state = {"settings": {"adaptive_planning": True}, "workspace": "."}
        workflows.begin(state, "requirements_gather")
        workflows.apply(state, {"workflow": "build", "reason": "", "signals": [], "clarity": "clear"}, {})
        self.assertEqual((state["next_stage"], state["workflow"]["clarity"]), ("astra_discovery", "clear"))

    def test_recognition_of_another_kind_keeps_its_own_first_stage(self):
        state = {"settings": {"adaptive_planning": True}, "workspace": "."}
        workflows.begin(state, "requirements_gather")
        workflows.apply(state, {"workflow": "bugfix", "reason": "", "signals": [], "clarity": "clear"}, {})
        self.assertEqual(state["next_stage"], workflows.INVESTIGATE_STAGE)


class NewRunsOnly(unittest.TestCase):
    def test_turning_the_flag_on_for_a_saved_run_is_refused(self):
        self.assertTrue(adaptive.resume_refused({"engine": "opencode"}, True))

    def test_repeating_the_flag_on_an_adaptive_run_or_omitting_it_is_allowed(self):
        self.assertFalse(adaptive.resume_refused({"adaptive_planning": True}, True))
        self.assertFalse(adaptive.resume_refused({"engine": "opencode"}, False))

    def test_new_runs_plan_adaptively_by_default_where_they_can(self):
        self.assertTrue(adaptive.new_run_setting(None, joint=True, v2=False))
        self.assertFalse(adaptive.new_run_setting(None, joint=False, v2=False), "no joint planning: fixed flow")
        self.assertFalse(adaptive.new_run_setting(None, joint=True, v2=True), "planning-v2: fixed flow")
        self.assertFalse(adaptive.new_run_setting(False, joint=True, v2=False), "--no-adaptive-planning opts out")
        self.assertTrue(adaptive.new_run_setting(True, joint=True, v2=False))
        for joint, v2 in ((False, False), (True, True)):
            with self.subTest(joint=joint, v2=v2), self.assertRaisesRegex(ValueError, "needs joint planning"):
                adaptive.new_run_setting(True, joint=joint, v2=v2)

    def test_a_saved_run_keeps_its_planning_flow(self):
        self.assertTrue(adaptive.resume_refused({"adaptive_planning": True}, False), "--no on an adaptive run")
        self.assertFalse(adaptive.resume_refused({"adaptive_planning": True}, None))
        self.assertFalse(adaptive.resume_refused({"engine": "opencode"}, None), "no flag changes nothing")

    def test_the_cli_flag_has_three_states(self):
        import autocode_args
        import autocode_opencode as opencode
        parser = autocode_args.build_parser(None, opencode.DEFAULT_MODELS)
        self.assertIsNone(parser.parse_args(["task"]).adaptive_planning)
        self.assertTrue(parser.parse_args(["task", "--adaptive-planning"]).adaptive_planning)
        self.assertFalse(parser.parse_args(["task", "--no-adaptive-planning"]).adaptive_planning)

    def test_configure_refuses_before_touching_the_saved_run(self):
        import argparse
        import autocode_configure as configure
        args = argparse.Namespace(adaptive_planning=True)
        with self.assertRaisesRegex(ValueError, "new-run policy"):
            configure.configure(args, {"settings": {"engine": "opencode"}}, planning=None, milestones=None,
                                autopilot=None)


class Schemas(unittest.TestCase):
    def test_recognizer_asks_for_clarity_only_in_adaptive_runs(self):
        self.assertNotIn("clarity", autoplanner.schema_for(OFF, autoplanner.RECOGNIZE)["properties"])
        self.assertIn("clarity", autoplanner.schema_for(ON, autoplanner.RECOGNIZE)["properties"])
        self.assertEqual(adaptive.recognizer_rule(OFF), "")

    def test_adaptive_planner_drafts_carry_initial_task(self):
        for stage in ("astra_discovery", "glm_revise"):
            contract = autoplanner.schema_for(ON, stage)["properties"]["contract"]
            self.assertIn("initial_task", contract["required"])
            self.assertNotIn("initial_task", autoplanner.schema_for(OFF, stage)["properties"]["contract"]["properties"])
        self.assertNotIn("initial_task", goals.BODY_SCHEMA["properties"], "the shared schema must not change")

    def test_prompt_rules_follow_the_stage(self):
        self.assertIn(adaptive.NO_REQUIREMENTS_RULE, adaptive.prompt_rule(ON, "astra_discovery"))
        handoff = {**ON, "requirements_handoff": {"report": {}}}
        self.assertNotIn(adaptive.NO_REQUIREMENTS_RULE, adaptive.prompt_rule(handoff, "astra_discovery"))
        self.assertEqual(adaptive.prompt_rule(ON, "astra_challenge"), adaptive.REVIEW_RULE)
        self.assertEqual(adaptive.prompt_rule(ON, "glm_revise"), adaptive.PLANNER_RULE)
        self.assertEqual(adaptive.prompt_rule(ON, "astra_finalize"), "")
        self.assertEqual(adaptive.prompt_rule(OFF, "astra_challenge"), "")


class Size(unittest.TestCase):
    def test_one_milestone_with_few_paths_is_small(self):
        self.assertEqual(adaptive.plan_size(body())["size"], "small")

    def test_three_milestones_is_large(self):
        self.assertEqual(adaptive.plan_size(body(milestones=3))["size"], "large")

    def test_many_paths_is_large(self):
        self.assertEqual(adaptive.plan_size(body(milestones=2, paths_each=5))["size"], "large")

    def test_in_between_is_medium(self):
        sized = adaptive.plan_size(body(milestones=2, paths_each=2, criteria=3))
        self.assertEqual((sized["size"], sized["signals"]["dependency_edges"]), ("medium", 1))

    def test_many_acceptance_criteria_do_not_make_a_one_milestone_change_large(self):
        sized = adaptive.plan_size(body(milestones=1, paths_each=3, criteria=16))
        self.assertEqual((sized["size"], sized["signals"]["acceptance_criteria"]), ("small", 16))

    def test_only_a_large_plan_gets_more_review_calls(self):
        self.assertEqual(adaptive.review_limit("small", 2), 2)
        self.assertEqual(adaptive.review_limit("large", 2), adaptive.LARGE_PLAN_REVIEW_LIMIT)
        self.assertEqual(adaptive.review_limit("large", 5), 5, "never lowers an allowance")
        self.assertEqual(adaptive.review_limit("large", 0), 0, "unlimited stays unlimited")


class Convergence(unittest.TestCase):
    def test_a_draft_with_an_executable_first_task_and_no_questions_is_approvable(self):
        self.assertTrue(adaptive.approvable(body()))
        self.assertFalse(adaptive.approvable(body(kind="none")))
        self.assertFalse(adaptive.approvable(body(questions=[{"id": "Q1"}])))

    def test_only_blocking_concerns_count(self):
        concerns = [{"id": "1", "blocking": False}, {"id": "2", "blocking": True}]
        self.assertEqual([c["id"] for c in adaptive.blocking(concerns)], ["2"])

    def test_default_allowance_revises_once_then_finalizes(self):
        self.assertEqual(adaptive.after_revise(2, 1, 1), "astra_finalize")

    def test_large_allowance_reviews_the_revision_again(self):
        self.assertEqual(adaptive.after_revise(3, 1, 1), "astra_challenge")
        self.assertEqual(adaptive.after_revise(3, 2, 2), "astra_finalize")

    def test_an_early_approved_plan_shows_the_reviewers_notes(self):
        concern = {"id": "1", "concern": "Name the log file", "requested_change": "Use run.log", "blocking": False}
        planning = {"adaptive": {"approved_at": "astra_challenge#1"},
                    "reports": {"astra_challenge": {"report": {"concerns": [concern]}}}}
        self.assertEqual(["  Reviewer note [1]: Name the log file", "    Suggested: Use run.log"],
                         adaptive.review_notes(planning))
        planning["adaptive"]["approved_at"] = None
        self.assertEqual([], adaptive.review_notes(planning), "a plan finalized by the final review shows decisions")

    def test_unlimited_allowance_stops_re_reviewing_after_the_cap(self):
        self.assertEqual(adaptive.after_revise(0, 2, 2), "astra_challenge")
        self.assertEqual(adaptive.after_revise(0, 3, adaptive.MAX_CHALLENGES), "astra_finalize")


if __name__ == "__main__":
    unittest.main()
