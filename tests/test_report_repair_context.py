"""The report repair handoff retains human clarification provenance."""
import copy
import json
import unittest

import autocode_goal_lifecycle as lifecycle
import autocode_report_repair_context as context
from goal_fixtures import approve_fixture

from . import test_report_repair as repair_fixtures


class ClarificationContextTests(unittest.TestCase):
    def test_revision_and_finalization_repairs_get_only_the_current_planning_exchange(self):
        reports = {
            "astra_discovery": {"output": "current-discovery.json", "report": {"contract": {"scope": ["greet.py"]}}},
            "astra_challenge": {"output": "current-review.json", "report": {"concerns": [{
                "id": "C1", "concern": "Guard test is missing", "requested_change": "Plan the guard test"}]}},
            "glm_revise": {"output": "current-revision.json", "report": {"responses": [{"concern_id": "C1"}]}},
            "astra_finalize": {"report": {"summary": "unrelated final"}},
        }
        state = {"planning": {"reports": reports},
                 "planning_history": [{"reports": {"astra_challenge": {"report": {"concerns": [{"id": "OLD"}]}}}}]}
        before = copy.deepcopy(state)
        for stage, names in (("glm_revise", ("astra_discovery", "astra_challenge")),
                             ("astra_finalize", ("astra_discovery", "astra_challenge", "glm_revise"))):
            with self.subTest(stage=stage):
                result = context.clarification_context(state, stage)
                self.assertEqual({name: reports[name] for name in names}, result["planning_exchange"])
                result["planning_exchange"]["astra_challenge"]["report"]["concerns"][0]["id"] = "changed"
                self.assertEqual(before, state)
        self.assertEqual({}, context.clarification_context(state, "requirements_gather")["planning_exchange"])
        self.assertEqual({}, context.clarification_context({}, "glm_revise")["planning_exchange"])

    def test_repair_instruction_requires_actual_saved_concern_ids_and_substantive_responses(self):
        text = context.instruction("glm_revise")
        self.assertIn("planning_exchange.astra_challenge.report.concerns", text)
        self.assertIn("every saved concern ID exactly once", text)
        self.assertIn("Do not invent a replacement concern ID", text)
        self.assertIn("existing evidence_refs", text)

    def test_finalizer_repair_instructions_use_only_final_report_fields(self):
        text = context.instruction("astra_finalize")

        self.assertIn("planning_exchange.astra_challenge.report.concerns", text)
        self.assertIn("every saved concern ID exactly once in decisions", text)
        self.assertIn("substantive rationale", text)
        self.assertIn("contract.initial_task", text)
        self.assertIn("contract.open_blocking_questions", text)
        self.assertIn("saved stage schema", text)
        for forbidden_instruction in (
                "in responses or decisions", "decisions, with substantive changes and existing evidence_refs",
                "Record human decisions in requirements", "ignored_statements using its exact checklist text",
                "machine_resolutions and access_blockers must be []"):
            with self.subTest(instruction=forbidden_instruction):
                self.assertNotIn(forbidden_instruction, text)

    def test_finalizer_decision_shape_matches_the_strict_saved_schema(self):
        from units import autoplanner

        text = context.instruction("astra_finalize")
        fields = autoplanner.SCHEMAS["astra_finalize"]["properties"]["decisions"]["items"]["properties"]
        self.assertIn("Each decision contains only " + ", ".join(fields), text)
        self.assertNotIn("evidence_refs", fields)
        self.assertNotIn("Add evidence_refs", text)

    def test_finalizer_repair_explains_the_observed_root_initial_task_error(self):
        from autocode_util import validate_schema
        from goal_fixtures import body
        from units import autoplanner

        initial_task = {"objective": "Fix blank names", "affected_paths": ["greet.py"],
                        "kind": "implement", "milestone_id": "M1", "requirements": ["Reject blanks"],
                        "acceptance_criteria": ["C1"], "validation_plan": ["Run the regression"]}
        # Retained revision 31: repairs added the sole extra root field while the
        # contract still omitted it. Both locations must be corrected together.
        report = {"contract": body(), "summary": "Final plan", "decisions": [],
                  "contract_changes": [], "conflict_resolutions": [], "requirement_trace": [],
                  "initial_task": initial_task}
        schema = autoplanner.SCHEMAS["astra_finalize"]
        with self.assertRaisesRegex(ValueError, r"\$: unexpected fields"):
            validate_schema(report, schema)
        without_extra = {key: value for key, value in report.items() if key != "initial_task"}
        with self.assertRaisesRegex(ValueError, r"\$\.contract: missing initial_task"):
            validate_schema(without_extra, schema)

        text = context.instruction("astra_finalize")
        self.assertIn("Remove a root-level initial_task", text)
        self.assertIn("preserving that task inside contract.initial_task", text)

    def test_finalizer_repair_defers_to_alternate_progressive_review_schemas(self):
        text = context.instruction("astra_finalize")

        self.assertIn("For alternate progressive review schemas, follow only their saved fields", text)
        self.assertIn("do not add contract, initial_task or decisions", text)

    def test_planning_repair_gets_only_matching_answers_and_current_investigation(self):
        question = {"id": "Q1", "question": "Which behavior?"}
        handoff = {"report": {"open_questions": [question], "requirements": [
            {"id": "preserve", "source_quote": "Preserve current output", "text": "Keep it stable."}]}}
        request = {"stage": "requirements_gather", "questions": [question],
                   "question_ids": ["Q1"], "handoff_hash": "h1"}
        state = {
            "requirements_handoff": handoff,
            "investigation_request": request,
            "clarification_episode": {"id": "episode-1", "investigation_used": True},
            "answers": {"Q1": {"text": "Use option A"}, "Q9": {"text": "Unrelated"}},
            "brief_feedback": [{"id": "feedback-1", "actor": "user_cli", "text": "Keep output stable",
                                "contract_token": "private-token"}],
        }

        result = context.clarification_context(state, "requirements_gather")

        self.assertEqual(handoff, result["requirements_handoff"])
        self.assertEqual(request, result["investigation_request"])
        self.assertEqual(state["clarification_episode"], result["clarification_episode"])
        self.assertEqual({"preserve": "Preserve current output"}, result["required_source_quotes"])
        self.assertEqual({"Q1": state["answers"]["Q1"]}, result["saved_answers"])
        self.assertEqual([{"id": "feedback-1", "actor": "user_cli", "text": "Keep output stable"}],
                         result["saved_feedback"])
        self.assertNotIn("Q9", result["saved_answers"])

    def test_human_clarification_is_not_a_machine_resolution(self):
        text = context.instruction("astra_discovery")
        self.assertIn("Human answers and feedback are never machine_resolutions", text)
        self.assertIn("only when clarification_context has an investigation_request", text)
        self.assertIn("Without that matching request, machine_resolutions and access_blockers must be []", text)
        self.assertIn("Historical resolutions in a prior handoff are not new resolutions", text)
        self.assertIn("ignored_statements using its exact checklist text", text)
        self.assertIn("Do not expand a prior source_quote to cover a checklist sentence", text)

    def test_nonplanning_report_repair_has_no_clarification_payload_or_rule(self):
        state = {"requirements_handoff": {"sensitive": "unrelated"}}
        self.assertEqual({}, context.clarification_context(state, "terra"))
        self.assertEqual("", context.instruction("terra"))
        self.assertEqual("", context.instruction("sol"))

    def test_original_planning_draft_is_not_an_immutable_answer_baseline(self):
        text = context.baseline_instruction("requirements_gather")
        self.assertIn("original_report is historical planning context", text)
        self.assertIn("Do not restore invalid machine_resolutions", text)
        self.assertNotIn("immutable execution-history baseline", text)
        self.assertIn("immutable execution-history baseline", context.baseline_instruction("terra"))


class DecisionRepairContextTests(unittest.TestCase):
    setUp = repair_fixtures.base.RetrofitTest.setUp

    def test_decision_repair_distinguishes_proposed_tasks_from_executed_history(self):
        runner = repair_fixtures.runner
        approve_fixture(self.state, runner.goals)
        self.state["settings"]["roles"]["resolver"] = copy.deepcopy(self.state["settings"]["roles"]["astra"])
        before = copy.deepcopy(self.state)
        for stage in ("astra_plan", "astra_review", "astra_checkpoint", "astra_resolve"):
            with self.subTest(stage=stage):
                self.state = copy.deepcopy(before)
                self.state["next_stage"] = stage
                # l9folr37's last rejected handoff used this slice ID as a milestone.
                decision = {"status": "CONTINUE", "next_objective": "Revalidate the approved behavior",
                            "affected_paths": ["greet.py"], "next_task": {
                                "kind": "validate", "milestone_id": "s2-recommendations",
                                "requirements": ["Preserve the approved behavior"],
                                "acceptance_criteria": ["C1"], "validation_plan": ["python3 -m unittest"]}}
                if stage == "astra_resolve":
                    decision.update(status="REWORK", evidence=[str(self.evidence)])
                    decision["next_task"]["kind"] = "implement"
                with self.assertRaisesRegex(ValueError, "Task must belong to an approved milestone") as rejected:
                    lifecycle.assign_task(self.state, decision, runner.support.snapshot(self.root))
                report = ({"decision": decision, "validation": {"verdict": "FAIL"}}
                          if stage == "astra_checkpoint" else decision)
                repair_fixtures.RepairTests.queue(self, error=rejected.exception, stage=stage,
                                                role="astra", report=json.dumps(report))
                request = repair_fixtures.RepairTests.repair_request(self)
                prompt = request["prompt"]
                data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
                self.assertEqual(report, data["rejected_report"]["content"])
                self.assertIn("Task must belong to an approved milestone", data["error"])
                self.assertEqual(["M1"], [row["id"] for row in runner.support.read(
                    data["state_file"])["goal_contract"]["body"]["milestones"]])
                self.assertEqual("read-only", request["sandbox"])
                self.assertFalse(request["allow_write"])
                self.assertTrue(request["report_only"])
                self.assertNotIn("original_report is also supplied, it is the immutable execution-history baseline",
                                 prompt)
                for instruction in ("proposed next_task is not executed history",
                                    "goal_contract.body.milestones", "existing approved milestone ID",
                                    "Progressive slice IDs are never milestone IDs",
                                    "requirements, acceptance_criteria and validation_plan must be nonempty",
                                    "Do not invent scope, milestones, unreviewed checks, PASS or citations",
                                    "Preserve executed commands, outcomes, Validator facts, findings, failures and uncertainty",
                                    "Do not change current_task or report_identity"):
                    self.assertIn(instruction, prompt)
                self.assertIn("decision.next_task", prompt)

    def test_missing_requirements_and_unknown_ids_are_not_repaired_by_guessing(self):
        runner = repair_fixtures.runner
        approve_fixture(self.state, runner.goals)
        self.state["next_stage"] = "astra_review"
        report = {"next_task": {"milestone_id": "unknown-milestone", "requirements": []},
                  "acceptance_criteria": [{"id": "C1", "status": "unverified", "evidence": ""}]}
        repair_fixtures.RepairTests.queue(self, stage="astra_review", role="astra",
                                        error=ValueError("The bounded task needs requirements"),
                                        report=json.dumps(report))
        prompt = repair_fixtures.RepairTests.repair_request(self)["prompt"]
        data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
        self.assertEqual(report, data["rejected_report"]["content"])
        self.assertIn("requirements, acceptance_criteria and validation_plan must be nonempty", prompt)
        self.assertIn("If the saved approved context does not establish a valid correction, preserve the uncertainty", prompt)
        self.assertIn("do not guess an ID or default to M1", prompt)
        self.assertIn("Missing evidence must remain NOT_VERIFIED, never invented PASS", prompt)
        latest = copy.deepcopy(report)
        latest["next_task"].update(milestone_id="s2-recommendations", requirements=["Preserve approved behavior"])
        with self.assertRaises(runner.ReportRepairQueued):
            repair_fixtures.RepairTests.reject_repair(self, 6,
                ValueError("Task must belong to an approved milestone and its acceptance criteria"),
                report=json.dumps(latest))
        prompt = repair_fixtures.RepairTests.repair_request(self)["prompt"]
        data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
        self.assertEqual(report, data["original_report"]["content"])
        self.assertEqual(latest, data["rejected_report"]["content"])
        self.assertIn("proposed next_task is not executed history", prompt)
        self.assertIn("existing approved milestone ID", prompt)

    def test_other_roles_and_saved_planning_aliases_keep_the_existing_baseline(self):
        expected = ("If original_report is also supplied, it is the immutable execution-history baseline; "
                    "rejected_report is the latest failed repair and error applies to that draft. ")
        for stage in ("terra", "sol", "astra_challenge", "astra_diagnose", "investigate_stuck",
                      "unknown-stage", "requirements", "plan", "plan_review", "plan_revise", "plan_finalize"):
            with self.subTest(stage=stage):
                self.assertEqual(expected, context.baseline_instruction(stage))


if __name__ == "__main__":
    unittest.main()
