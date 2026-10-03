"""AutoPlanner step 2 (issue #62): the runner-owned clarification episode, the
single investigation pass for discoverable questions, and machine-resolution
validity. Discoverable questions must never reach the user."""
import json
from pathlib import Path
import tempfile
import unittest

import autocode_goals as goals, autopilot
import autocode_goal_lifecycle as lifecycle
from units import autoplanner as planner
from goal_fixtures import body


def question(qid, kind="discoverable", category="technical", default=""):
    return {"id": qid, "question": f"What is {qid}?", "why": f"{qid} changes the design", "options": [],
            "proposed_default": default, "kind": kind, "category": category, "delegable": False}


def requirements(questions, **extra):
    report = {"summary": "Requirements", "intended_outcome": "Local greeting", "required_behaviors": ["Greet"],
              "constraints": [], "acceptance_tests": ["Run it"], "source_refs": ["config.py:1"],
              "proposed_assumptions": [], "open_questions": questions, "requirements": [],
              "ignored_statements": [], "conflicts": [], "proposed_reframes": [], "ignored_requirements": [],
              "machine_resolutions": [], "access_blockers": []}
    report.update(extra)
    return report


def discovery(contract, **extra):
    report = {"contract": contract, "summary": "Draft", "code_refs": ["config.py:1"], "alternatives": [],
              "uncertainties": [], "contract_changes": [], "requirement_trace": [],
              "machine_resolutions": [], "access_blockers": []}
    report.update(extra)
    return report


def clarification_only(questions):
    contract = body()
    contract.update(open_blocking_questions=questions, technical_approach=[], milestones=[])
    return contract


class EpisodeCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name)
        (self.workspace / "config.py").write_text("PROVIDER = 'opencode'\nROUTE = 'default'\n")
        self.state = {"version": 3, "task_id": "task-1", "task": "Build a greeting tool",
                      "workspace": str(self.workspace), "answers": {}, "user_events": [],
                      "acceptance_criteria": [], "status": "RUNNING", "next_stage": "requirements_gather",
                      "settings": {"joint_planning": True,
                                   "roles": {"requirements": {}, "glm": {}, "plan_reviewer": {}}}}

    def apply(self, stage, value, output="report.json"):
        autopilot.apply_planning(self.state, stage, value, {"output": output})

    def queue_pass(self, questions):
        self.apply("requirements_gather", requirements(questions), "first.json")
        return self.state["investigation_request"]["handoff_hash"]

    def resolution(self, qid, handoff_hash, refs=("config.py:1",)):
        return {"question_id": qid, "resolution": "The provider is set in config.py", "source_refs": list(refs),
                "handoff_hash": handoff_hash}


class InvestigationPassTests(EpisodeCase):
    def test_discoverable_question_queues_one_pass_instead_of_reaching_the_user(self):
        self.apply("requirements_gather", requirements([question("Q1"), question("Q2", "decision", "cost")]))
        self.assertEqual("requirements_gather", self.state["next_stage"])
        self.assertNotIn("requirements_handoff", self.state)
        request = self.state["investigation_request"]
        self.assertEqual(["Q1"], request["question_ids"])
        self.assertTrue(self.state["clarification_episode"]["investigation_used"])
        prompt, _ = planner.context(self.state, "requirements_gather", Path("/tmp/state.json"))
        self.assertIn("INVESTIGATION PASS", prompt)
        self.assertIn(request["handoff_hash"], prompt)
        other, _ = planner.context(self.state, "astra_challenge", Path("/tmp/state.json"))
        self.assertNotIn("INVESTIGATION PASS", other)

    def test_code_evidenced_resolution_retires_the_question_without_a_user_event(self):
        handoff_hash = self.queue_pass([question("Q1"), question("Q2", "decision", "cost")])
        events = len(self.state["user_events"])
        self.apply("requirements_gather", requirements([question("Q2", "decision", "cost")],
                   machine_resolutions=[self.resolution("Q1", handoff_hash, ["config.py:1-2"])]))
        self.assertEqual("astra_discovery", self.state["next_stage"])
        self.assertEqual(["Q2"], [q["id"] for q in self.state["requirements_handoff"]["report"]["open_questions"]])
        self.assertEqual("Q1", self.state["machine_resolutions"][0]["question_id"])
        self.assertNotIn("investigation_request", self.state)
        self.assertEqual(events, len(self.state["user_events"]))

    def test_discovery_accepts_a_handoff_question_retired_by_its_own_investigation(self):
        self.apply("requirements_gather", requirements([question("Q3", "decision")]))
        self.apply("astra_discovery", discovery(clarification_only([question("Q3")])))
        self.assertEqual("astra_discovery", self.state["next_stage"])
        self.assertNotIn("goal_contract", self.state)
        handoff_hash = self.state["investigation_request"]["handoff_hash"]
        self.apply("astra_discovery", discovery(body(), machine_resolutions=[self.resolution("Q3", handoff_hash)]))
        self.assertEqual("astra_challenge", self.state["next_stage"])
        self.assertEqual([], self.state["goal_contract"]["body"]["open_blocking_questions"])

    def test_policy_choice_cannot_be_resolved_from_the_workspace(self):
        handoff_hash = self.queue_pass([question("Q1", category="cost")])
        with self.assertRaisesRegex(ValueError, "only a technical fact"):
            self.apply("requirements_gather", requirements([], machine_resolutions=[self.resolution("Q1", handoff_hash)]))
        self.assertNotIn("requirements_handoff", self.state)

    def test_resolution_for_a_decision_question_is_rejected(self):
        handoff_hash = self.queue_pass([question("Q1"), question("Q2", "decision")])
        with self.assertRaisesRegex(ValueError, "does not name an outstanding discoverable question"):
            self.apply("requirements_gather", requirements(
                [question("Q1", "decision")], machine_resolutions=[self.resolution("Q2", handoff_hash)]))

    def test_resolution_outside_a_pass_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "only in the investigation pass"):
            self.apply("requirements_gather", requirements([], machine_resolutions=[self.resolution("Q1", "x")]))

    def test_resolution_bound_to_another_report_is_rejected(self):
        self.queue_pass([question("Q1")])
        with self.assertRaisesRegex(ValueError, "bound to a different report"):
            self.apply("requirements_gather", requirements([], machine_resolutions=[self.resolution("Q1", "stale")]))

    def test_resolution_must_cite_real_workspace_source(self):
        handoff_hash = self.queue_pass([question("Q1")])
        for refs in ([], ["missing.py:1"], ["../outside.py"], ["config.py:40"]):
            with self.subTest(refs=refs), self.assertRaises(ValueError):
                self.apply("requirements_gather", requirements([], machine_resolutions=[
                    self.resolution("Q1", handoff_hash, refs)]))

    def test_missing_source_becomes_an_access_blocker_decision_not_a_retry(self):
        self.queue_pass([question("Q1")])
        blocked = {**question("Q1", "decision"), "why": "The provider config file is not in this workspace"}
        self.apply("requirements_gather", requirements(
            [blocked], access_blockers=[{"question_id": "Q1", "reason": "No provider config is checked in"}]))
        self.assertEqual("astra_discovery", self.state["next_stage"])
        self.assertEqual("decision", self.state["requirements_handoff"]["report"]["open_questions"][0]["kind"])

    def test_pass_cannot_silently_drop_a_question(self):
        self.queue_pass([question("Q1")])
        with self.assertRaisesRegex(ValueError, "dropped questions"):
            self.apply("requirements_gather", requirements([]))

    def test_still_discoverable_after_the_pass_becomes_a_labelled_decision(self):
        self.queue_pass([question("Q1")])
        self.apply("requirements_gather", requirements([question("Q1")]))
        asked = self.state["requirements_handoff"]["report"]["open_questions"][0]
        self.assertEqual("decision", asked["kind"])
        self.assertIn("single investigation pass", asked["why"])
        self.assertNotIn("investigation_request", self.state)

    def test_glm_revise_pass_keeps_the_contract_and_review_concerns(self):
        self.state["workspace"] = "/absent-workspace"
        lifecycle.install_draft(self.state, body(), origin="glm_draft")
        concerns = [{"id": "P1", "concern": "c", "evidence_refs": ["x"], "requested_change": "r",
                     "acceptance_test": "t", "blocking": True}]
        self.state["planning"]["reports"]["astra_challenge"] = {"report": {"concerns": concerns}}
        revision = self.state["goal_contract"]["revision"]
        response = [{"concern_id": "P1", "response": "ok", "evidence_refs": ["x"], "change": "c", "acceptance_test": "t"}]
        revise = {"summary": "Revised", "code_refs": [], "responses": response, "contract_changes": [],
                  "requirement_trace": [], "machine_resolutions": [], "access_blockers": []}
        self.apply("glm_revise", {**revise, "contract": {**body(), "open_blocking_questions": [question("Q1")]}})
        self.assertEqual("glm_revise", self.state["next_stage"])
        self.assertEqual(revision, self.state["goal_contract"]["revision"])
        self.assertEqual(concerns, self.state["planning"]["reports"]["astra_challenge"]["report"]["concerns"])
        self.assertEqual("glm_revise", self.state["investigation_request"]["stage"])


    def test_final_reviewer_question_labelled_discoverable_reaches_the_user_as_a_decision(self):
        self.state["workspace"] = "/absent-workspace"
        lifecycle.install_draft(self.state, body(), origin="glm_draft")
        self.state["planning"]["reports"]["astra_challenge"] = {"report": {"concerns": []}}
        final = {**body(), "open_blocking_questions": [question("Q1")],
                 "initial_task": {"objective": "", "affected_paths": [], "kind": "none", "milestone_id": "",
                                  "requirements": [], "acceptance_criteria": [], "validation_plan": []}}
        self.apply("astra_finalize", {"contract": final, "summary": "Needs a decision", "decisions": [],
                                      "contract_changes": [], "requirement_trace": []})
        # The question is queued; the runner's writer boundary publishes it to the user.
        self.assertEqual("RESOLVER_PENDING", self.state["status"])
        lifecycle.human.evaluate(self.state)
        self.assertEqual("WAITING_FOR_USER", self.state["status"])
        self.assertEqual("clarification", self.state["resolver_human_request"]["scope"])
        self.assertEqual("decision", self.state["pending_questions"][0]["kind"])
        self.assertNotIn("investigation_request", self.state)


class EpisodeBudgetTests(EpisodeCase):
    def test_regenerated_ids_and_reworded_handoffs_do_not_replenish_the_pass(self):
        self.queue_pass([question("Q1")])
        episode = dict(self.state["clarification_episode"])
        self.apply("requirements_gather", requirements([question("Q1", "decision")]))
        self.apply("requirements_gather", {**requirements([question("Q9")]), "summary": "Reworded"})
        self.assertNotIn("investigation_request", self.state)
        self.assertEqual("decision", self.state["requirements_handoff"]["report"]["open_questions"][0]["kind"])
        self.assertEqual(episode["id"], self.state["clarification_episode"]["id"])

    def test_non_delegated_answer_starts_a_new_episode_with_one_new_pass(self):
        lifecycle.install_draft(self.state, body(questions=True), origin="glm_draft")
        lifecycle.human.evaluate(self.state)  # the runner's writer boundary publishes the clarification
        first = goals.clarification_episode(self.state)
        first["investigation_used"] = True
        goals.answer(self.state, "Q1", "CLI")
        episode = self.state["clarification_episode"]
        self.assertNotEqual(first["id"], episode["id"])
        self.assertFalse(episode["investigation_used"])
        self.assertTrue(self.state["answers"]["Q1"]["starts_episode"])

    def test_delegation_resumes_without_replenishing_the_pass(self):
        lifecycle.install_draft(self.state, body(questions=True), origin="glm_draft")
        lifecycle.human.evaluate(self.state)  # the runner's writer boundary publishes the clarification
        episode = goals.clarification_episode(self.state)
        episode["investigation_used"] = True
        goals.answer(self.state, "Q1", "accept default", delegated=True)
        self.assertEqual(episode["id"], self.state["clarification_episode"]["id"])
        self.assertTrue(self.state["clarification_episode"]["investigation_used"])
        self.assertNotIn("starts_episode", self.state["answers"]["Q1"])

    def test_feedback_and_edited_goal_start_new_episodes(self):
        lifecycle.install_draft(self.state, body(questions=True), origin="glm_draft")
        lifecycle.human.evaluate(self.state)  # the runner's writer boundary publishes the clarification
        first = goals.clarification_episode(self.state)["id"]
        goals.feedback(self.state, "Also support a web page")
        second = self.state["clarification_episode"]
        self.assertNotEqual(first, second["id"])
        self.assertEqual(self.state["brief_feedback"][-1]["id"], second["started_by"])
        lifecycle.install_draft(self.state, body(), origin="user_cli_edit")
        self.assertNotEqual(second["id"], self.state["clarification_episode"]["id"])

    def test_new_episode_discards_a_pending_pass(self):
        self.queue_pass([question("Q1")])
        goals.start_clarification_episode(self.state, "feedback-x")
        self.assertNotIn("investigation_request", self.state)


class LegacyCompatibilityTests(EpisodeCase):
    def test_pre_structured_string_assumptions_are_still_accepted(self):
        self.apply("requirements_gather", requirements([], proposed_assumptions=["A CLI may suffice"]))
        self.assertEqual(["A CLI may suffice"], self.state["requirements_handoff"]["report"]["proposed_assumptions"])

    def test_generation_schema_gives_the_model_a_structured_assumption(self):
        schema = planner.SCHEMAS["requirements_gather"]["properties"]["proposed_assumptions"]
        self.assertEqual("object", schema["items"]["type"])
        self.assertIn("convention_ref", schema["items"]["required"])

    def test_legacy_run_without_an_episode_gets_one_lazily(self):
        self.assertNotIn("clarification_episode", self.state)
        episode = goals.clarification_episode(self.state)
        self.assertEqual("initial_task", episode["started_by"])
        self.assertFalse(episode["investigation_used"])


if __name__ == "__main__":
    unittest.main()


class ContractListTests(EpisodeCase):
    """Live discovery reports were sent back for repair for an empty deliverables, required_behaviors or
    permission_boundaries list (two Claude-model trials, 2026-09-29): the model is told what each list is for."""

    STAGES = ("astra_discovery", "glm_revise", "astra_finalize")

    def test_the_schema_the_model_gets_says_what_each_list_is_for(self):
        for stage in (*self.STAGES, "plan", "plan_revise", "plan_finalize"):
            fields = planner.SCHEMAS[stage]["properties"]["contract"]["properties"]
            for key in ("deliverables", "required_behaviors", "permission_boundaries"):
                with self.subTest(stage=stage, key=key):
                    self.assertIn("At least one", fields[key]["description"])
            self.assertIn("do not invent", fields["scope_exclusions"]["description"])
            criteria = fields["acceptance_criteria"]["items"]["properties"]
            self.assertEqual(planner.HUMAN_REVIEW_NOTE, criteria["human_review"]["description"], stage)
        # The shared contract schema itself is unchanged.
        self.assertNotIn("description", goals.BODY_SCHEMA["properties"]["deliverables"])
        self.assertNotIn("description", goals.BODY_SCHEMA["properties"]["acceptance_criteria"]["items"]
                         ["properties"]["human_review"])

    def test_the_planning_prompts_carry_the_rule_and_the_review_does_not(self):
        for stage in self.STAGES:
            prompt, _ = planner.context(self.state, stage, Path("/tmp/state.json"))
            self.assertIn(planner.CONTRACT_FIELDS_RULE, prompt, stage)
            self.assertIn("HUMAN REVIEW: an acceptance criterion's human_review is true only when", prompt, stage)
        review, _ = planner.context(self.state, "astra_challenge", Path("/tmp/state.json"))
        self.assertNotIn(planner.CONTRACT_FIELDS_RULE, review)

    def test_validation_is_unchanged(self):
        schema = planner.SCHEMAS["astra_discovery"]
        from autocode_support import validate_schema
        validate_schema(discovery(body(), conflict_resolutions=[]), schema)
        # A draft that still has questions may leave the lists empty, as before.
        draft = clarification_only([{"id": "Q1", "question": "CLI or web?", "why": "Interface",
                                     "options": ["CLI", "Web"], "proposed_default": "CLI"}])
        draft.update(deliverables=[], required_behaviors=[], permission_boundaries=[])
        lifecycle.validate_body(self.state, draft)
        ready = body()
        ready["permission_boundaries"] = []
        with self.assertRaisesRegex(ValueError, "missing permission_boundaries"):
            lifecycle.validate_body(self.state, ready)


class RequirementTraceRowsTests(EpisodeCase):
    """Live planner reports returned requirement_trace [] although the handoff listed R1..Rn
    (Claude models, 2026-09-29): the stages that trace requirements get the IDs and the rule."""

    REQUIREMENTS = [{"id": "R1", "text": "Print Hello, NAME", "source_quote": "Print Hello, NAME"},
                    {"id": "R2", "text": "Reject an empty name", "source_quote": "Reject an empty name"}]

    def setUp(self):
        super().setUp()
        self.state["requirements_handoff"] = {"report": {"requirements": self.REQUIREMENTS}, "output": "req.json"}

    def packet(self, stage):
        prompt, _ = planner.context(self.state, stage, Path("/tmp/state.json"))
        return prompt, json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])

    def test_the_tracing_stages_get_every_requirement_id_and_the_rule(self):
        for stage in planner.TRACE_STAGES:
            prompt, packet = self.packet(stage)
            with self.subTest(stage=stage):
                self.assertEqual([{"requirement_id": row["id"], "requirement": row["text"],
                                   "source_quote": row["source_quote"]} for row in self.REQUIREMENTS],
                                 packet["requirement_trace_rows"])
                self.assertIn(planner.REQUIREMENT_TRACE_RULE, prompt)

    def test_plan_review_gets_the_sources_without_a_trace_output_requirement(self):
        prompt, packet = self.packet("astra_challenge")
        self.assertEqual([row["source_quote"] for row in self.REQUIREMENTS],
                         [row["source_quote"] for row in packet["requirement_trace_rows"]])
        self.assertNotIn(planner.REQUIREMENT_TRACE_RULE, prompt)
        self.assertNotIn("requirement_trace", planner.SCHEMAS["astra_challenge"]["properties"])
        self.state["requirements_handoff"] = {"report": {"requirements": []}, "output": "req.json"}
        prompt, packet = self.packet("astra_discovery")
        self.assertNotIn("requirement_trace_rows", packet)
        self.assertNotIn(planner.REQUIREMENT_TRACE_RULE, prompt)

    def test_the_runners_check_is_unchanged(self):
        contract = body()
        with self.assertRaisesRegex(ValueError, "dropped requirements with no trace: R1, R2"):
            goals.check_requirement_trace(self.state, {"requirement_trace": []}, contract)
        paraphrase = [{"requirement_id": "R1", "disposition": "covered", "evidence": "It greets people"},
                      {"requirement_id": "R2", "disposition": "covered", "evidence": "C1"}]
        with self.assertRaisesRegex(ValueError, "R1 is not covered"):
            goals.check_requirement_trace(self.state, {"requirement_trace": paraphrase}, contract)
        cited = [{"requirement_id": "R1", "disposition": "covered", "evidence": "C1 checks this"},
                 {"requirement_id": "R2", "disposition": "covered", "evidence": "C1"}]
        goals.check_requirement_trace(self.state, {"requirement_trace": cited}, contract)


class QuestionDraftTraceTests(EpisodeCase):
    """A draft that asks first has no criteria yet: its covered rows may say what they wait on
    (a live feature-refund-window plan paid a report repair for "pending Q1", 2026-09-29)."""

    def setUp(self):
        super().setUp()
        self.state["requirements_handoff"] = {"report": {"requirements": RequirementTraceRowsTests.REQUIREMENTS},
                                              "output": "req.json"}

    QUESTION = {"id": "Q1", "question": "May store_date() be corrected?", "why": "It ignores the store offset",
                "options": ["Yes", "No"], "proposed_default": "Yes"}

    def bind(self, trace, contract):
        report = discovery(contract, requirement_trace=trace)
        autopilot._bind_plan(self.state, report, "glm_draft", {"output": "draft.json"})

    def pending(self, *ids):
        return [{"requirement_id": rid, "disposition": "covered", "evidence": "pending Q1"} for rid in ids]

    def test_a_draft_with_open_questions_may_leave_coverage_pending(self):
        self.bind(self.pending("R1", "R2"), clarification_only([self.QUESTION]))
        self.assertEqual(["Q1"], [q["id"] for q in self.state["goal_contract"]["body"]["open_blocking_questions"]])

    def test_it_must_still_trace_every_requirement_and_justify_an_exclusion(self):
        with self.assertRaisesRegex(ValueError, "dropped requirements with no trace: R2"):
            self.bind(self.pending("R1"), clarification_only([self.QUESTION]))
        excluded = self.pending("R1") + [{"requirement_id": "R2", "disposition": "excluded",
                                          "evidence": "Web service"}]
        with self.assertRaisesRegex(ValueError, "without a saved user event"):
            self.bind(excluded, {**clarification_only([self.QUESTION]), "scope_exclusions": ["Web service"]})

    def test_a_draft_without_questions_still_needs_real_coverage(self):
        self.assertTrue(planner.traces_coverage(body()))
        self.assertFalse(planner.traces_coverage(clarification_only([self.QUESTION])))
        with self.assertRaisesRegex(ValueError, "R1 is not covered"):
            self.bind(self.pending("R1", "R2"), body())


class NewBehaviorCriterionRuleTests(EpisodeCase):
    """A live plan made "the package is importable" a test: criterion; it passes before the change
    (a namespace package), so the milestone could never be proven (parallel-diamond, 2026-09-29)."""

    def test_build_planning_says_a_test_criterion_is_new_behavior(self):
        prompt, _ = planner.context(self.state, "astra_discovery", Path("/tmp/state.json"))
        rule = 'A "test:" criterion describes behavior that does not exist before the run'
        self.assertIn(rule, planner.EXAMPLE_CRITERIA_RULE)
        self.assertIn(rule, prompt)
        self.assertIn("imports without __init__.py", prompt)
