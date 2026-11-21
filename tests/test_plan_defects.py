"""A plan's own defect is the Planner's to correct, never the person's question (#3818).

A live sweep stopped two of eight runs on questions such as "May the peer-PING example replace
'peer-ping' with a valid eight-byte payload?" (the plan's own criterion could not run) and "will the
plan be revised to remove its two retained assumptions?" (the plan contradicted itself). Neither had a
second answer a person could give.
"""

import copy
import unittest

import autocode_goal_lifecycle as lifecycle
import autocode_plan_defects as plan_defects
import autocode_resolver as resolver
from goal_fixtures import body

from .test_autoplanner_step2 import EpisodeCase, discovery, requirements
from .test_autoplanner_step3 import NO_TASK


def defect(qid="P1", part="C1", **extra):
    return {
        "id": qid,
        "question": f"May {part}'s example use a valid eight-byte PING payload instead of 'peer-ping'?",
        "why": f"'peer-ping' is nine bytes, so {part} cannot be executed as written.",
        "options": ["Use an eight-byte payload", f"Keep the literal, leaving {part} impossible to execute"],
        "proposed_default": "Use an eight-byte payload",
        "kind": "plan_defect",
        "category": "behavior",
        "delegable": False,
        "convention_ref": "",
        **extra,
    }


def genuine(qid="Q2"):
    return {
        "id": qid,
        "question": "Should the greeting be a CLI or web endpoint?",
        "why": "This determines the delivered interface",
        "options": ["CLI", "Web"],
        "proposed_default": "CLI",
        "kind": "decision",
        "category": "behavior",
        "delegable": False,
        "convention_ref": "",
    }


def state(*, joint=True):
    return {
        "version": 3,
        "task_id": "task-defect",
        "task": "Build a greeting tool",
        "workspace": "/absent-workspace",
        "status": "RUNNING",
        "next_stage": "draft_plan",
        "settings": {"joint_planning": joint},
        "answers": {},
        "user_events": [],
    }


def draft(*questions):
    value = body()
    value["open_blocking_questions"] = [copy.deepcopy(q) for q in questions]
    return value


def corrections(current):
    return [e for e in current["user_events"] if e.get("settled_as") == plan_defects.KIND]


class NamedPartTests(unittest.TestCase):
    def test_a_question_names_the_plans_criteria_and_milestones_as_whole_words(self):
        plan = draft()
        self.assertEqual(["C1"], plan_defects.named_parts(defect(), plan))
        self.assertEqual(["C1", "M1"], plan_defects.named_parts(defect(why="M1 and C1 disagree."), plan))
        for text in ("C10 cannot run", "AC1 cannot run", "C1x is stale", "No criterion is named"):
            with self.subTest(text=text):
                self.assertEqual([], plan_defects.named_parts({"question": text, "why": ""}, plan))


class SettleTests(unittest.TestCase):
    def test_a_plan_defect_is_settled_and_only_the_genuine_choice_reaches_the_person(self):
        current = state()
        lifecycle.install_draft(current, draft(defect(), genuine()), origin="planner_draft")
        lifecycle.human.evaluate(current)
        self.assertEqual("WAITING_FOR_USER", current["status"])
        self.assertEqual(["Q2"], [q["id"] for q in current["pending_questions"]])
        [event] = corrections(current)
        self.assertEqual(
            ("technical_decision", "autocode", "P1"), (event["kind"], event["actor"], event["question_id"])
        )
        # AutoCode's own correction request, never one of the question's options.
        self.assertIn("Correct the plan instead of asking the person", event["text"])
        self.assertIn("Revise C1 so", event["text"])
        self.assertEqual(["C1"], event["plan_parts"])
        self.assertNotIn(event["text"], defect()["options"])
        self.assertIn("the person approves the corrected plan", event["reason"])

    def test_a_plan_with_only_defects_is_corrected_without_a_person_question(self):
        for joint in (True, False):
            with self.subTest(joint_planning=joint):
                current = state(joint=joint)
                lifecycle.install_draft(current, draft(defect(), defect("P2", part="M1")), origin="revise_plan")
                self.assertEqual(("RUNNING", "draft_plan"), (current["status"], current["next_stage"]))
                self.assertEqual("none", lifecycle.human.evaluate(current))
                self.assertEqual([], current["goal_contract"]["body"]["open_blocking_questions"])
                self.assertEqual("draft", current["goal_contract"]["approval_status"])
                self.assertEqual(["P1", "P2"], [e["question_id"] for e in corrections(current)])

    def test_a_plan_defect_that_names_no_part_of_the_plan_is_asked_as_a_decision(self):
        current = state()
        lifecycle.install_draft(current, draft(defect(part="AC17")), origin="planner_draft")
        lifecycle.human.evaluate(current)
        [asked] = current["pending_questions"]
        self.assertEqual(("P1", "decision"), (asked["id"], asked["kind"]))
        self.assertTrue(asked["why"].startswith("It names no acceptance criterion or milestone of the plan."))
        self.assertEqual([], corrections(current))
        self.assertEqual(asked, current["goal_contract"]["body"]["open_blocking_questions"][0])

    def test_a_question_is_judged_by_what_it_decides_not_by_its_one_option(self):
        one_option = genuine("Q3")
        one_option["options"] = ["Correct the stale assumptions without narrowing the requested outcome"]
        one_option["why"] = "C1 and the assumptions disagree."
        current = state()
        lifecycle.install_draft(current, draft(one_option), origin="planner_draft")
        lifecycle.human.evaluate(current)
        self.assertEqual(["Q3"], [q["id"] for q in current["pending_questions"]])
        self.assertEqual([], corrections(current))

    def test_one_correction_per_round_of_the_persons_input_so_planning_never_loops(self):
        current = state()
        lifecycle.install_draft(current, draft(defect()), origin="revise_plan")
        lifecycle.install_draft(current, draft(defect("P2")), origin="revise_plan")
        lifecycle.human.evaluate(current)
        self.assertEqual(["P2"], [q["id"] for q in current["pending_questions"]])
        self.assertIn("already asked the Planner to correct the plan", current["pending_questions"][0]["why"])
        self.assertEqual(["P1"], [e["question_id"] for e in corrections(current)])
        current["user_events"].append({"kind": "answer", "actor": "user_cli", "question_id": "P2"})
        lifecycle.install_draft(current, draft(defect("P3")), origin="revise_plan")
        self.assertEqual(["P1", "P3"], [e["question_id"] for e in corrections(current)])

    def test_the_persons_approval_of_the_corrected_plan_starts_a_new_round(self):
        # A live run: the corrected plan was approved, then the tool boundary refused one of its commands
        # before the build; that reopen could not correct the plan because the round still looked spent.
        current = state()
        lifecycle.install_draft(current, draft(defect()), origin="revise_plan")
        self.assertTrue(plan_defects.settled_this_round(current))
        current["user_events"].append({"kind": "goal_approval", "actor": "user_cli"})
        self.assertFalse(plan_defects.settled_this_round(current))
        current["user_events"].append({"kind": "permission_answer", "actor": "user_cli"})
        self.assertFalse(plan_defects.settled_this_round(current))

    def test_a_rejected_assumption_obligation_stays_the_persons(self):
        current = state()
        current["deferred_obligations"] = [{"id": "P1", "status": "open"}]
        lifecycle.install_draft(current, draft(defect()), origin="planner_draft")
        lifecycle.human.evaluate(current)
        self.assertEqual(["P1"], [q["id"] for q in current["pending_questions"]])

    def test_a_plan_defect_question_is_a_known_kind_to_the_resolver(self):
        plan = draft(defect())
        self.assertTrue(resolver._validate_body(plan))
        plan["open_blocking_questions"][0]["kind"] = "plan_problem"
        self.assertFalse(resolver._validate_body(plan))


class FinalReviewTests(EpisodeCase):
    def test_the_final_plan_review_returns_a_plan_defect_to_the_planner(self):
        # The reported case: the Plan Reviewer's final report asked the person about the plan's own criterion.
        self.apply("requirements_gather", requirements([]))
        self.apply("draft_plan", discovery(body()))
        self.apply("review_plan", {"summary": "Review", "concerns": []})
        final = draft(defect())
        final["initial_task"] = copy.deepcopy(NO_TASK)
        self.apply(
            "finalize_plan",
            {
                "contract": final,
                "summary": "C1 cannot run as written",
                "decisions": [],
                "contract_changes": [],
                "requirement_trace": [],
            },
        )
        self.assertEqual("draft_plan", self.state["next_stage"])
        self.assertEqual("none", lifecycle.human.evaluate(self.state))
        self.assertTrue(self.state["planning"]["internal_answer_restart"])
        self.assertEqual(["P1"], [e["question_id"] for e in corrections(self.state)])


if __name__ == "__main__":
    unittest.main()
