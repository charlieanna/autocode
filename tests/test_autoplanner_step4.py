"""AutoPlanner step 4 (issue #62, rule E9): the Plan Preview shown at a planning
clarification stop. Counts only, bound to the displayed revision and handoff."""
import copy
import unittest

import autocode_goals as goals, autocode_support as support
import autocode_goal_lifecycle as lifecycle
from goal_fixtures import body
from .test_planner_invariants import state as base_state

TASK = "Greet the user by name."


def question(qid, default="", delegable=False):
    return {"id": qid, "question": f"Decide {qid}?", "why": f"{qid} changes the interface",
            "options": ["CLI", "Web"], "proposed_default": default, "kind": "decision",
            "category": "behavior", "delegable": delegable}


def assumption(aid, text, category="technical"):
    return {"id": aid, "text": text, "kind": "inferable", "category": category,
            "convention_ref": "greet.py:3", "rationale": "Existing CLI", "supports": ["R1"]}


class PreviewCase(unittest.TestCase):
    def setUp(self):
        self.state = base_state(TASK)
        self.state["settings"]["joint_planning"] = False
        self.state["requirements_handoff"] = {"output": "requirements.json", "report": {
            "requirements": [{"id": "R1", "text": "Greet by name", "source_quote": TASK}],
            "source_refs": ["task", "greet.py:1-9"], "acceptance_tests": ["Run it", "Run it empty"],
            "proposed_assumptions": [assumption("A1", "A CLI is enough"), assumption("A2", "Names are ASCII"),
                                     "Output goes to stdout"]}}
        contract = body()
        contract["open_blocking_questions"] = [question("Q1", "CLI", delegable=True), question("Q2")]
        contract["accepted_assumptions"].append({"text": "CLI invocation is sufficient",
                                                 "basis": "agent_proposed", "answer_id": ""})
        lifecycle.install_draft(self.state, contract, origin="glm_draft")
        # The clarification stop exists once the runner's writer boundary publishes it.
        self.assertEqual("RESOLVER_PENDING", self.state["status"])
        lifecycle.human.evaluate(self.state)

    def preview(self):
        return "\n".join(goals.plan_preview(self.state))


class PlanPreviewTests(PreviewCase):
    def test_preview_shows_known_assumed_and_undecided_at_a_clarification_stop(self):
        self.assertEqual("WAITING_FOR_USER", self.state["status"])
        self.assertEqual("clarification", self.state["resolver_human_request"]["scope"])
        text = lifecycle.present(self.state)
        self.assertIn("PLAN PREVIEW for " + goals.token(self.state["goal_contract"]), text)
        handoff = support.digest(self.state["requirements_handoff"]["report"])[:12]
        self.assertIn(f"requirements handoff {handoff}", text)
        self.assertIn("Answering nothing leaves execution blocked", text)
        self.assertIn(f'[R1] Greet by name (you said: "{TASK}")', text)
        self.assertIn("Files read: greet.py:1-9", text)
        self.assertIn("[A1] A CLI is enough (technical; evidence: greet.py:3)", text)
        self.assertIn("Unstructured, from an older run: Output goes to stdout", text)
        self.assertIn("Planner: CLI invocation is sufficient", text)
        self.assertIn("[Q1] Decide Q1?", text)
        self.assertIn("Proposed default: CLI\n", text + "\n")
        self.assertNotIn("Proposed default: CLI (not delegable)", text)
        self.assertIn("--delegate-all", text)

    def test_readiness_is_counts_only(self):
        text = self.preview()
        self.assertIn("Blocking decisions: 2", text)
        self.assertIn("Assumptions relied on: 4", text)
        self.assertIn("Acceptance tests in requirements: 2", text)
        self.assertIn("Acceptance criteria in the contract: 1 (0 need your review)", text)
        self.assertIn("Open obligations: 0 for your decision, 0 awaiting remediation", text)
        for label in ("%", "HIGH", "MEDIUM", "LOW", "partial", "complete"):
            self.assertNotIn(label, text.split("Readiness:")[1])

    def test_late_revision_preview_has_no_answered_question_or_rejected_assumption(self):
        goals.answer(self.state, "Q1", "CLI")
        # The answer retired the published request; the runner asks the remaining question again.
        remaining = self.state["goal_contract"]["body"]["open_blocking_questions"]
        self.assertEqual(["Q2"], [q["id"] for q in remaining])
        lifecycle.human.queue(self.state, "clarification", {"stage": "astra_discovery"}, questions=remaining)
        lifecycle.human.evaluate(self.state)
        self.assertEqual("WAITING_FOR_USER", self.state["status"])
        lifecycle.present(self.state)
        goals.reject_assumption(self.state, "A2", goals.token(self.state["goal_contract"]))
        contract = body()
        contract["open_blocking_questions"] = [question("Q3")]
        lifecycle.install_draft(self.state, contract, origin="glm_draft")
        lifecycle.human.evaluate(self.state)
        text = self.preview()
        self.assertNotIn("[Q1]", text)
        self.assertNotIn("Names are ASCII", text)
        self.assertIn("[A1] A CLI is enough", text)
        self.assertIn("[Q3] Decide Q3?", text)
        self.assertIn("Open obligations: 0 for your decision, 1 awaiting remediation", text)

    def test_machine_resolutions_appear_as_known_from_the_codebase(self):
        self.state["machine_resolutions"] = [{"question_id": "Q9", "resolution": "Provider is set in config.py",
                                              "source_refs": ["config.py:2"]}]
        self.assertIn("[Q9] Provider is set in config.py (source: config.py:2)", self.preview())

    def test_no_preview_outside_a_planning_clarification_stop(self):
        for change in ({"status": "AWAITING_GOAL_APPROVAL"}, {"pending_questions": []},
                       {"user_request": {"kind": "permission"}}):
            with self.subTest(change):
                current = copy.deepcopy(self.state)
                current.update(change)
                self.assertEqual([], goals.plan_preview(current))
                self.assertNotIn("PLAN PREVIEW", lifecycle.render(current))

    def test_run_without_a_handoff_still_previews_safely(self):
        self.state.pop("requirements_handoff")
        text = self.preview()
        self.assertIn("requirements handoff none", text)
        self.assertIn("(no quoted requirements recorded)", text)
        self.assertIn("(nothing cited from the workspace)", text)

    def test_empty_contract_render_is_unchanged(self):
        self.assertEqual("No contract yet; resume to interview with the Requirements Gatherer.",
                         lifecycle.render({"status": "WAITING_FOR_USER"}))


if __name__ == "__main__":
    unittest.main()
