"""Regression cases from live AutoPlanner trials: clarification and intent changes."""
from pathlib import Path
import tempfile
import unittest

from . import autocode_goals as goals, autopilot
from .units import autoplanner as planner
from .goal_fixtures import body
from .test_planner_invariants import state


def draft_report(draft):
    return {"contract": draft, "summary": "Draft", "code_refs": [], "alternatives": [],
            "uncertainties": [], "contract_changes": [], "requirement_trace": []}


def handoff(task="Build an agent dashboard"):
    return {"summary": "Requirements", "intended_outcome": task,
            "required_behaviors": [task], "constraints": [], "acceptance_tests": ["Verify requested behavior"],
            "source_refs": [], "proposed_assumptions": [], "open_questions": [],
            "requirements": [{"id": "R1", "text": task, "source_quote": task}],
            "ignored_statements": [], "conflicts": []}


class PlannerIntentTests(unittest.TestCase):
    def test_ambiguous_idea_cannot_produce_tasks_until_answered(self):
        current = state("Build something like Cursor but better for multiple agents")
        value = draft_report(body(questions=True))
        with self.assertRaisesRegex(ValueError, "clarification-only"):
            autopilot.apply_planning(current, "astra_discovery", value, {"output": "draft.json"})
        self.assertNotIn("goal_contract", current)
        value["contract"]["milestones"] = []
        value["contract"]["technical_approach"] = []
        autopilot.apply_planning(current, "astra_discovery", value, {"output": "questions.json"})
        self.assertEqual("WAITING_FOR_USER", current["status"])
        self.assertEqual([], current["goal_contract"]["body"]["milestones"])
        goals.answer(current, "Q1", "CLI")
        autopilot.apply_planning(current, "astra_discovery", draft_report(body()), {"output": "plan.json"})
        self.assertEqual("astra_challenge", current["next_stage"])
        self.assertTrue(current["goal_contract"]["body"]["milestones"])
        self.assertFalse(goals.approved(current))

    def test_late_reviewer_question_is_not_erased_by_revision_routing(self):
        current = state()
        goals.install_draft(current, body(), origin="glm_draft")
        current["planning"]["reports"]["astra_challenge"] = {"report": {"concerns": []}}
        draft = body(questions=True)
        value = {"contract": draft, "summary": "Need a decision", "code_refs": [],
                 "responses": [], "contract_changes": [], "requirement_trace": []}
        autopilot.apply_planning(current, "glm_revise", value, {"output": "revision.json"})
        self.assertEqual("WAITING_FOR_USER", current["status"])
        self.assertEqual("Q1", current["pending_questions"][0]["id"])
        self.assertNotEqual("astra_finalize", current["next_stage"])

    def test_saved_correction_supersedes_old_requirement_and_invalidates_approval(self):
        current = state("Monitoring only; no controls")
        current["settings"]["joint_planning"] = False
        goals.install_draft(current, body(), origin="fixture")
        goals.present(current)
        goals.approve(current, goals.token(current["goal_contract"]))
        self.assertTrue(goals.approved(current))
        current["settings"]["joint_planning"] = True
        # Return to the conversation checkpoint, then apply changed user intent.
        current["status"] = "AWAITING_GOAL_APPROVAL"
        old_token = goals.token(current["goal_contract"])
        goals.feedback(current, "Actually add Pause, Resume and Cancel")
        event = current["brief_feedback"][-1]
        self.assertIsNone(current["goal_contract"]["approval_event"])
        current["requirements_handoff"] = {"report": {
            "requirements": [{"id": "R1"}, {"id": "R2"}], "open_questions": [],
            "conflicts": [{"requirement_ids": ["R1", "R2"], "description": "Controls vs monitoring only"}]}}
        value = draft_report(body())
        value["requirement_trace"] = [
            {"requirement_id": "R1", "disposition": "superseded",
             "evidence": f"Amended by saved feedback {event['id']} to permit the three controls."},
            {"requirement_id": "R2", "disposition": "covered", "evidence": "C1"}]
        autopilot.apply_planning(current, "astra_discovery", value, {"output": "changed.json"})
        self.assertNotEqual(old_token, goals.token(current["goal_contract"]))
        self.assertFalse(goals.approved(current))
        self.assertEqual("astra_challenge", current["next_stage"])

    def test_supersession_does_not_accept_forged_or_partial_event_ids(self):
        current = state()
        event = {"id": "feedback-abc123", "kind": "brief_feedback", "text": "Replace it"}
        current.update(user_events=[event], brief_feedback=[event])
        current["requirements_handoff"] = {"report": {"requirements": [{"id": "R1"}]}}
        for evidence in ("feedback-abc123", "Replaced by feedback-abc123 (explicit correction)"):
            goals.check_requirement_trace(current, {"requirement_trace": [
                {"requirement_id": "R1", "disposition": "superseded", "evidence": evidence}]}, body())
        for evidence in ("feedback-abc1234", "xfeedback-abc123", "feedback-abc123 and feedback-forged"):
            with self.subTest(evidence=evidence), self.assertRaisesRegex(ValueError, "saved user event"):
                goals.check_requirement_trace(current, {"requirement_trace": [
                    {"requirement_id": "R1", "disposition": "superseded", "evidence": evidence}]}, body())
        current["user_events"] = []
        self.assertFalse(goals._cites_saved_user_event(current, "feedback-abc123"))

    def test_impossible_guarantee_reframe_requires_its_own_question(self):
        task = "Guarantee zero bugs"
        current = state(task)
        value = handoff(task)
        value["proposed_reframes"] = [{"requirement_id": "R1", "proposal": "Zero known defects after checks",
                                       "question_id": "accept-bounded-guarantee"}]
        with self.assertRaisesRegex(ValueError, "explicit user acceptance question"):
            goals.check_requirement_handoff(current, value)
        question = {"id": "accept-bounded-guarantee", "question": "Accept bounded verification?",
                    "why": "Unknown bugs cannot be ruled out", "options": [], "proposed_default": ""}
        value["open_questions"] = [question]
        goals.check_requirement_handoff(current, value)
        current["requirements_handoff"] = {"report": value}
        trace = {"requirement_trace": [{"requirement_id": "R1", "disposition": "covered", "evidence": "C1"}]}
        with self.assertRaisesRegex(ValueError, "reframe must remain"):
            goals.check_requirement_trace(current, trace, body(questions=True))
        draft = body()
        draft["open_blocking_questions"] = [question]
        goals.check_requirement_trace(current, trace, draft)

    def test_feedback_requirements_cannot_be_silently_omitted(self):
        current = state("Build dashboard")
        current["brief_feedback"] = [{"text": "Never touch auth."}]
        value = handoff("Build dashboard")
        with self.assertRaisesRegex(ValueError, "Never touch auth"):
            goals.check_requirement_handoff(current, value)

    def test_inventory_finds_nested_ui_without_git_or_package_json(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "tools/dashboard").mkdir(parents=True)
            (root / "tools/dashboard/dashboard.html").write_text("<button>Pause</button>")
            (root / ".autocode").mkdir()
            (root / ".autocode/private.json").write_text("{}")
            inventory = planner.workspace_inventory(root, "agent dashboard")
            self.assertEqual(["tools/dashboard/dashboard.html"], inventory["files"])
            self.assertFalse(inventory["truncated"])
            current = state("Build an agent dashboard")
            current["workspace"] = temp
            value = handoff()
            value["source_refs"] = ["task"]
            with self.assertRaisesRegex(ValueError, "cite existing source"):
                autopilot.apply_planning(current, "requirements_gather", value, {"output": "req.json"})
            value["source_refs"] = ["tools/dashboard/dashboard.html:1", "https://example.test/design-spec"]
            autopilot.apply_planning(current, "requirements_gather", value, {"output": "req.json"})

    def test_source_citations_allow_explanations_but_check_the_full_range(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "runner.py").write_text("def complete():\n    return True\n")
            current = state()
            current["workspace"] = temp
            for ref in ("runner.py:1", "runner.py:1-2 (completion gate)", "runner.py:2 — result"):
                autopilot._check_code_refs(current, [ref])
            for ref in ("runner.py:1-3 (past end)", "runner.py:2-1", "runner.py:0", "runner.py:bogus"):
                with self.subTest(ref=ref), self.assertRaises(ValueError):
                    autopilot._check_code_refs(current, [ref])


if __name__ == "__main__":
    unittest.main()
