"""Adaptive planning decisions (autocode_adaptive_planning): pure functions over state and plans."""
import json
import unittest
from pathlib import Path

import autocode_adaptive_planning as adaptive
import autocode_goals as goals
import autocode_util as util
import autocode_workflows as workflows
from goal_fixtures import body as contract_body
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

    def test_clear_follow_up_refreshes_saved_requirements(self):
        value = {"workflow": "build", "clarity": "clear"}
        state = {**ON, "turns": [{"say": "Add optional casefolding."}],
                 "requirements_handoff": {"output": "previous-requirements.json", "report": {}}}
        self.assertEqual("requirements_gather",
                         adaptive.entry_stage(state, value, "requirements_gather", "astra_discovery"))
        # Review findings can deliberately replace requirements; no prior handoff needs refresh otherwise.
        self.assertEqual("astra_discovery",
                         adaptive.entry_stage(state, value, "astra_discovery", "astra_discovery"))
        state.pop("requirements_handoff")
        self.assertEqual("astra_discovery",
                         adaptive.entry_stage(state, value, "requirements_gather", "astra_discovery"))

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


def awaiting(*, setting=True, roles=("requirements", "plan_reviewer"), status="AWAITING_GOAL_APPROVAL", handoff=None):
    """A joint-planning run showing its plan for approval."""
    contract = {"task_id": "task-1", "revision": 1, "body": contract_body()}
    contract["hash"] = util.digest(contract)
    state = {"version": 3, "task_id": "task-1", "task": "Build a greeting CLI", "workspace": "/absent-workspace",
             "status": status, "goal_contract": contract, "answers": {}, "user_events": [], "acceptance_criteria": [],
             "settings": {"joint_planning": True, "adaptive_planning": setting, "roles": {role: {} for role in roles}}}
    if handoff:
        state["requirements_handoff"] = handoff
    return state


HANDOFF = {"report": {"requirements": [{"id": "R1", "text": "Print Hello, NAME for a nonempty name",
                                        "source_quote": "Print Hello, NAME for a nonempty name"}]},
           "output": "iterations/001/requirements_gather-01.json"}


class FeedbackOnAShownPlan(unittest.TestCase):
    def test_feedback_on_a_plan_waiting_for_approval_goes_to_the_planner(self):
        state = awaiting(handoff=HANDOFF)
        goals.feedback(state, "Also accept --shout.")
        event = state["brief_feedback"][-1]
        self.assertEqual("astra_discovery", state["next_stage"])
        self.assertEqual({"requirements_handoff": HANDOFF["output"]}, event[adaptive.FEEDBACK])
        self.assertEqual("draft", state["goal_contract"]["approval_status"])

    def test_other_feedback_restarts_from_requirements(self):
        for state in (awaiting(setting=False), awaiting(status="WAITING_FOR_USER"),
                      awaiting(status="PAUSED_PLANNING_BUDGET")):
            with self.subTest(status=state["status"], adaptive=state["settings"]["adaptive_planning"]):
                goals.feedback(state, "Also accept --shout.")
                self.assertEqual("requirements_gather", state["next_stage"])
                self.assertNotIn(adaptive.FEEDBACK, state["brief_feedback"][-1])

    def test_feedback_is_a_requirement_until_a_requirements_report_reads_it(self):
        state = awaiting(handoff=HANDOFF)
        goals.feedback(state, "Also accept --shout.")
        event_id = state["brief_feedback"][-1]["id"]
        rows = adaptive.feedback_requirements(state)
        self.assertEqual([{"id": event_id, "text": "Also accept --shout.", "source_quote": "Also accept --shout."}], rows)
        self.assertEqual(["R1", event_id], [row["requirement_id"] for row in autoplanner.trace_rows(state, "astra_discovery")])
        self.assertEqual([], autoplanner.trace_rows(state, "requirements_gather"))
        state["requirements_handoff"] = {**HANDOFF, "output": "iterations/002/requirements_gather-01.json"}
        self.assertEqual([], adaptive.feedback_requirements(state), "the new Requirements report took it in")

    def test_without_a_requirements_stage_the_feedback_is_still_traced(self):
        state = awaiting()
        goals.feedback(state, "Also accept --shout.")
        self.assertEqual(["Also accept --shout."], [row["text"] for row in adaptive.feedback_requirements(state)])

    def test_the_revision_must_deliver_the_feedback(self):
        state = awaiting(handoff=HANDOFF)
        goals.feedback(state, "Also accept --shout.")
        event_id = state["brief_feedback"][-1]["id"]
        revised = contract_body()
        r1 = {"requirement_id": "R1", "disposition": "covered", "evidence": "C1"}
        with self.assertRaisesRegex(ValueError, "Planner dropped requirements with no trace: " + event_id):
            goals.check_requirement_trace(state, {"requirement_trace": [r1]}, revised)
        unsupported = {"requirement_id": event_id, "disposition": "covered", "evidence": "Accept --shout"}
        with self.assertRaisesRegex(ValueError, f"Requirement {event_id} is not covered"):
            goals.check_requirement_trace(state, {"requirement_trace": [r1, unsupported]}, revised)
        revised["required_behaviors"].append("Accept --shout")
        goals.check_requirement_trace(state, {"requirement_trace": [r1, unsupported]}, revised)  # does not raise

    def test_only_a_draft_revising_from_feedback_may_send_it_back_to_requirements(self):
        state = awaiting(handoff=HANDOFF)
        self.assertNotIn("requirements_rerun", autoplanner.schema_for(state, "astra_discovery")["properties"])
        self.assertEqual("", adaptive.requirements_rerun(state, {"requirements_rerun": "New product"}))
        goals.feedback(state, "Make it a web page instead.")
        self.assertIn("requirements_rerun", autoplanner.schema_for(state, "astra_discovery")["properties"])
        self.assertNotIn("requirements_rerun", autoplanner.schema_for(state, "astra_discovery")["required"])
        self.assertNotIn("requirements_rerun", autoplanner.schema_for(state, "glm_revise")["properties"])
        self.assertEqual("New product", adaptive.requirements_rerun(state, {"requirements_rerun": " New product "}))
        no_stage = awaiting(roles=("plan_reviewer",))
        goals.feedback(no_stage, "Make it a web page instead.")
        self.assertEqual("", adaptive.requirements_rerun(no_stage, {"requirements_rerun": "New product"}),
                         "nowhere to send it without a Requirements stage")

    def test_a_send_back_discards_the_draft_and_runs_requirements(self):
        state = awaiting(handoff=HANDOFF)
        goals.feedback(state, "Make it a web page instead.")
        self.assertFalse(autoplanner.rerun_requirements(state, {"requirements_rerun": "", "summary": ""}))
        self.assertEqual("astra_discovery", state["next_stage"])
        self.assertTrue(autoplanner.rerun_requirements(state, {"requirements_rerun": "A web page is another product",
                                                                "summary": ""}))
        self.assertEqual(("requirements_gather", "RUNNING"), (state["next_stage"], state["status"]))

    def test_prompt_rules_name_the_feedback_only_while_it_is_traced(self):
        state = awaiting(handoff=HANDOFF)
        self.assertNotIn(adaptive.FEEDBACK_RULE, adaptive.prompt_rule(state, "astra_discovery"))
        goals.feedback(state, "Also accept --shout.")
        planner = adaptive.prompt_rule(state, "astra_discovery")
        self.assertIn(adaptive.FEEDBACK_RULE, planner)
        self.assertIn(adaptive.RERUN_RULE, planner)
        self.assertIn(adaptive.FEEDBACK_REVIEW_RULE, adaptive.prompt_rule(state, "astra_challenge"))
        self.assertIn(adaptive.FEEDBACK_TRACE_RULE, adaptive.prompt_rule(state, "glm_revise"))
        self.assertEqual(adaptive.FEEDBACK_TRACE_RULE, adaptive.prompt_rule(state, "astra_finalize"))

    def test_the_runner_names_a_trace_row_that_can_only_be_the_feedback(self):
        # The live tiny-greeting draft (2026-10-02): the feedback was planned (AC8) but its only trace row
        # had no requirement_id, which cost two report repairs before the run stopped.
        state = awaiting()
        goals.feedback(state, "Also accept --shout.")
        event_id = state["brief_feedback"][-1]["id"]
        row = {"disposition": "covered", "evidence": "AC8 verifies the --shout flag"}
        filled = autoplanner.fill_trace_id(state, "astra_discovery", {"requirement_trace": [row]})
        self.assertEqual(event_id, filled["requirement_trace"][0]["requirement_id"])
        self.assertNotIn("requirement_id", row, "the reported row is left as it was")
        revised = contract_body()
        revised["acceptance_criteria"].append({"id": "AC8", "criterion": "--shout prints upper case",
                                               "verification_method": "Run greet.py --shout Ann", "human_review": False})
        goals.check_requirement_trace(state, filled, revised)  # does not raise

    def test_an_ambiguous_trace_row_is_left_for_the_checks_to_refuse(self):
        state = awaiting(handoff=HANDOFF)
        goals.feedback(state, "Also accept --shout.")
        two_unnamed = {"requirement_trace": [{"disposition": "covered", "evidence": "C1"},
                                             {"disposition": "covered", "evidence": "C1"}]}
        self.assertIs(two_unnamed, autoplanner.fill_trace_id(state, "astra_discovery", two_unnamed))
        complete = {"requirement_trace": [{"requirement_id": "R1", "disposition": "covered", "evidence": "C1"}]}
        self.assertIs(complete, autoplanner.fill_trace_id(state, "astra_discovery", complete),
                      "no unnamed row: the missing feedback row stays missing")
        one = {"requirement_trace": [{"disposition": "covered", "evidence": "C1"}]}
        self.assertIs(one, autoplanner.fill_trace_id(state, "astra_discovery", one), "R1 and the feedback both untraced")
        self.assertIs(one, autoplanner.fill_trace_id(state, "astra_challenge", one), "only Planner reports carry a trace")

    def test_trace_rows_naming_nothing_are_dropped_when_nothing_must_be_traced(self):
        # The first adaptive draft of a clear request (no Requirements stage): GLM traced anyway, without IDs.
        state = awaiting()
        value = {"requirement_trace": [{"disposition": "covered", "evidence": "AC1"},
                                       {"requirement_id": "R1", "disposition": "covered", "evidence": "AC1"}]}
        self.assertEqual([{"requirement_id": "R1", "disposition": "covered", "evidence": "AC1"}],
                         autoplanner.fill_trace_id(state, "astra_discovery", value)["requirement_trace"])

    def test_explicit_empty_and_unknown_trace_ids_are_not_reassigned(self):
        state = awaiting(handoff=HANDOFF)
        goals.feedback(state, "Also accept --shout.")
        for rows in (
            [{"requirement_id": "R1"}, {"requirement_id": ""}],
            [{"requirement_id": "unknown"}, {}],
            [{"requirement_id": "R1"}, {"requirement_id": "R1"}, {}],
            [{"requirement_id": "R1"}, {"requirement_id": state["brief_feedback"][-1]["id"]}, {}],
        ):
            with self.subTest(rows=rows):
                value = {"requirement_trace": [dict(row, disposition="covered", evidence="AC1") for row in rows]}
                self.assertIs(value, autoplanner.fill_trace_id(state, "astra_discovery", value))

    def test_loader_recovers_feedback_id_before_strict_planning_metadata(self):
        import json
        import tempfile
        from pathlib import Path
        import autocode as runner
        state = awaiting()
        goals.feedback(state, "Also accept --shout.")
        ident = state["brief_feedback"][-1]["id"]
        raw = {"requirement_trace": [{"disposition": "covered", "evidence": "AC1"}]}
        schema = {"type": "object", "properties": {"requirement_trace": {"type": "array", "items": {
            "type": "object", "required": ["requirement_id", "disposition", "evidence"],
            "properties": {"requirement_id": {"type": "string"}, "disposition": {"type": "string"},
                           "evidence": {"type": "string"}}}}}}
        with tempfile.TemporaryDirectory() as directory:
            output, schema_path = Path(directory)/"report.json", Path(directory)/"schema.json"
            output.write_text(json.dumps(raw)); schema_path.write_text(json.dumps(schema))
            record = {"stage": "astra_discovery", "engine": "codex", "output": str(output), "schema": str(schema_path)}
            value = runner.load_stage_report(record, state=state)
            self.assertEqual(ident, value["requirement_trace"][0]["requirement_id"])
            self.assertEqual(raw, json.loads(Path(record["reported_output"]).read_text()))
            self.assertEqual(value, runner.load_stage_report(record, state=state))

    def test_a_trace_error_names_the_requirements_it_needs(self):
        state = awaiting(handoff=HANDOFF)
        goals.feedback(state, "Also accept --shout.")
        event_id = state["brief_feedback"][-1]["id"]
        guessed = [{"requirement_id": "R1", "disposition": "covered", "evidence": "C1"},
                   {"requirement_id": "R2", "disposition": "covered", "evidence": "C1"}]
        with self.assertRaisesRegex(ValueError, f"exactly once: R1, {event_id}"):
            goals.check_requirement_trace(state, {"requirement_trace": guessed}, contract_body())


class ReReviewAfterAnswers(unittest.TestCase):
    """A review after the user answered the previous review's questions sees that review (both pipelines)."""

    def cycle(self, questions=("Q1",)):
        concern = {"id": "C1", "concern": "AC2 contradicts the request", "requested_change": "Reword AC2",
                   "acceptance_test": "AC2 matches the request", "evidence_refs": ["task"], "blocking": True}
        decision = {"concern_id": "C1", "decision": "Rewording protected AC2 needs the user's permission",
                    "rationale": "AC2 is protected", "acceptance_test": "AC2 matches the request", "resolved": False}
        contract = {**contract_body(), "open_blocking_questions": [
            {"id": question, "question": "May AC2 be reworded to match the request?", "why": "AC2 is protected",
             "options": ["Yes", "No"], "proposed_default": "Yes"} for question in questions]}
        return {"astra_calls": 2, "final_token": None, "reports": {
            "astra_challenge": {"report": {"summary": "", "concerns": [concern]}},
            "astra_finalize": {"report": {"summary": "", "decisions": [decision], "contract": contract}}}}

    def test_a_review_after_answered_questions_sees_its_earlier_review(self):
        state = {**awaiting(status="RUNNING"), "planning_history": [self.cycle()],
                 "planning": {"astra_calls": 0, "reports": {}, "final_token": None}}
        self.assertIsNone(autoplanner.previous_review(state), "the question is not answered yet")
        state["answers"] = {"Q1": {"kind": "answer", "text": "Yes, reword AC2."}}
        earlier = autoplanner.previous_review(state)
        self.assertEqual([{"id": "Q1", "question": "May AC2 be reworded to match the request?",
                           "answer": "Yes, reword AC2."}], earlier["answered_questions"])
        self.assertEqual([{"concern_id": "C1", "decision": "Rewording protected AC2 needs the user's permission",
                           "resolved": False}], earlier["decisions"])
        self.assertEqual([{"id": "C1", "concern": "AC2 contradicts the request", "blocking": True}], earlier["concerns"])
        path = Path(state["workspace"]) / "state.json"
        review, _ = autoplanner.context(state, "astra_challenge", path)
        self.assertIn(autoplanner.REREVIEW_RULE, review)
        self.assertEqual(earlier, json.loads(review.split("CURRENT HANDOFF DATA\n", 1)[1])["previous_review"])
        revise, _ = autoplanner.context(state, "glm_revise", path)
        self.assertNotIn(autoplanner.REREVIEW_RULE, revise)

    def test_a_cycle_that_ended_without_questions_gives_nothing(self):
        self.assertIsNone(autoplanner.previous_review({**awaiting(), "planning_history": [self.cycle(questions=())]}))
        self.assertIsNone(autoplanner.previous_review(awaiting()))


if __name__ == "__main__":
    unittest.main()
