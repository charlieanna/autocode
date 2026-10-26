"""The scope question and what the plan must record after it (autocode_scope_question, #2066)."""

import unittest

import autocode_scope_question as scope


def state(*parts, answers=None):
    return {
        "requirements_handoff": {
            "report": {"workspace_scope": {"spans_repositories": bool(parts), "external_parts": list(parts)}}
        },
        "answers": answers or {},
    }


HISTORY = {"target": "epp-history", "work": "Store the delivery window", "source_quote": "q", "evidence": ""}


class ScopeQuestionTests(unittest.TestCase):
    def test_a_spanning_request_gets_one_nondelegable_question_naming_each_part(self):
        question = scope.question(state(HISTORY))
        self.assertEqual("workspace-scope-1", question["id"])
        self.assertIn("epp-history: Store the delivery window", question["question"])
        self.assertEqual([scope.THIS_REPOSITORY], question["options"])
        self.assertFalse(question["delegable"])
        self.assertEqual("", question["proposed_default"], "the person decides; there is no default")

    def test_a_request_that_spans_nothing_asks_nothing(self):
        self.assertIsNone(scope.question(state()))
        self.assertIsNone(scope.question({}))
        self.assertEqual([], scope.uncovered(state()))

    def test_choosing_this_repository_ends_the_question_and_leaves_the_part_uncovered(self):
        answered = state(HISTORY, answers={"workspace-scope-1": {"text": "This repository only"}})
        self.assertIsNone(scope.question(answered))
        self.assertEqual("this_repository", scope.decision(answered))
        self.assertEqual([{"target": "epp-history", "work": "Store the delivery window"}], scope.uncovered(answered))

    def test_the_plan_must_record_each_uncovered_part(self):
        answered = state(HISTORY, answers={"workspace-scope-1": {"text": "This repository only"}})
        with self.assertRaisesRegex(ValueError, "does not cover epp-history"):
            scope.check(answered, {"scope_exclusions": ["Deployment"]})
        scope.check(answered, {"scope_exclusions": ["Not covered: changes in epp-history: Store the delivery window"]})
        scope.check(state(HISTORY), {"scope_exclusions": []})  # nothing is left out until the person chooses

    def test_the_view_reports_parts_decision_and_dropped_targets(self):
        answered = state(HISTORY, answers={"workspace-scope-1": {"text": "This repository only"}})
        self.assertEqual(
            {
                "spans_repositories": True,
                "external_parts": [{"target": "epp-history", "work": "Store the delivery window"}],
                "uncovered": ["epp-history"],
                "decision": "this_repository",
                "dropped": ["billing"],
                "linked": [],
            },
            {key: value for key, value in scope.view(answered, ["billing"]).items() if key != "briefs"},
        )

    def test_each_repository_gets_a_ready_brief_this_one_first(self):
        spanning = {**state(HISTORY), "task": "DTI-12398: add the delivery window.", "workspace": "/work/rg-api"}
        here, there = scope.briefs(spanning)
        self.assertEqual("rg-api", here["repository"])
        self.assertIn("DTI-12398: add the delivery window.", here["brief"])
        self.assertIn("Not covered here, a separate run in each repository: epp-history", here["brief"])
        self.assertEqual("epp-history", there["repository"])
        self.assertTrue(there["brief"].startswith("In epp-history: Store the delivery window."))
        self.assertIn("This run owns only epp-history's side", there["brief"])
        self.assertEqual([], scope.briefs(state()))
        self.assertIn("scope.briefs", scope.question(spanning)["question"])


if __name__ == "__main__":
    unittest.main()
