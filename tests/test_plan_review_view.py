"""The Plan Reviewer's latest verdict in the status view (``plan_review``)."""

import unittest

import autocode_plan_review_view as plan_review_view
import autocode_run_view as run_view


def concern(id, blocking):
    return {
        "id": id,
        "concern": f"concern {id}",
        "blocking": blocking,
        "requested_change": f"change {id}",
        "evidence_refs": ["plan"],
        "acceptance_test": "",
        "draft_failure_correction": None,
    }


def state(reports, *, stages=(), history=()):
    return {
        "status": "WAITING_FOR_USER",
        "planning": {"reports": reports},
        "stages": list(stages),
        "planning_history": list(history),
    }


def launched(stage, output, model="model-a", effort="low"):
    return {"stage": stage, "output": output, "launch_route": {"model": model}, "reasoning_effort": effort}


class PlanReviewViewTest(unittest.TestCase):
    def test_no_review_yet_shows_none(self):
        self.assertIsNone(run_view.view({"status": "RUNNING"})["plan_review"])
        self.assertIsNone(plan_review_view.project(state({"draft_plan": {"report": {}, "output": "d.json"}})))

    def test_a_blocking_concern_requests_changes_and_names_it(self):
        saved = state(
            {"review_plan": {"report": {"concerns": [concern("C1", True), concern("C2", False)]}, "output": "r.json"}},
            stages=[launched("review_plan", "r.json")],
        )
        review = run_view.view(saved)["plan_review"]
        self.assertEqual("changes_requested", review["verdict"])
        self.assertEqual(["C1"], review["blocking"])
        self.assertEqual(
            {"id": "C2", "concern": "concern C2", "blocking": False, "requested_change": "change C2"},
            review["concerns"][1],
        )
        self.assertEqual(
            ("review_plan", "Plan Reviewer", "model-a", "low"),
            tuple(review[k] for k in ("stage", "role", "model", "reasoning_effort")),
        )
        self.assertFalse(review["from_earlier_plan"])

    def test_only_non_blocking_concerns_approve(self):
        saved = state({"review_plan": {"report": {"concerns": [concern("C1", False)]}, "output": "r.json"}})
        review = plan_review_view.project(saved)
        self.assertEqual(("approved", []), (review["verdict"], review["blocking"]))
        self.assertIsNone(review["model"], "a saved report without a launch record names no model")

    def test_the_final_review_is_the_latest_and_judges_by_its_decisions(self):
        challenge = {"report": {"concerns": [concern("C1", True), concern("C2", True)]}, "output": "r.json"}
        for decisions, questions, verdict, blocking in (
            ([{"concern_id": "C1", "resolved": True}, {"concern_id": "C2", "resolved": True}], [], "approved", []),
            (
                [{"concern_id": "C1", "resolved": True}, {"concern_id": "C2", "resolved": False}],
                [],
                "unresolved",
                ["C2"],
            ),
            (
                [{"concern_id": "C1", "resolved": True}, {"concern_id": "C2", "resolved": True}],
                ["Q1"],
                "unresolved",
                [],
            ),
        ):
            with self.subTest(verdict=verdict, blocking=blocking):
                final = {
                    "report": {"decisions": decisions, "contract": {"open_blocking_questions": questions}},
                    "output": "f.json",
                }
                review = plan_review_view.project(state({"review_plan": challenge, "finalize_plan": final}))
                self.assertEqual(
                    ("finalize_plan", verdict, blocking), (review["stage"], review["verdict"], review["blocking"])
                )
                self.assertEqual(["C1", "C2"], [row["id"] for row in review["concerns"]])

    def test_a_review_carried_over_from_an_earlier_plan_says_so(self):
        challenge = {"report": {"concerns": [concern("C1", True)]}, "output": "r.json"}
        carried = state({"review_plan": challenge}, history=[{"reports": {"review_plan": challenge}}])
        self.assertTrue(plan_review_view.project(carried)["from_earlier_plan"])
        fresh = {"report": {"concerns": []}, "output": "r2.json"}
        renewed = state({"review_plan": fresh}, history=[{"reports": {"review_plan": challenge}}])
        self.assertFalse(plan_review_view.project(renewed)["from_earlier_plan"])


if __name__ == "__main__":
    unittest.main()
