"""A public scope handoff cannot bypass a prior Requirements decision."""

import copy
import unittest

import autocode_coordination_admission as admission


def scope_view():
    return {
        "status": "WAITING_FOR_USER",
        "scope": {"identity": "current-scope", "external_parts": [{"target": "history"}], "decision": None},
        "needs": {
            "kind": "answer",
            "resolver_scope": "clarification",
            "questions": [{"id": "workspace-scope-current-scope"}],
        },
    }


class AdmissionTests(unittest.TestCase):
    def test_only_the_exact_current_scope_question_is_ready(self):
        view = scope_view()
        before = copy.deepcopy(view)
        self.assertTrue(admission.pending_scope(view))
        self.assertEqual(before, view)
        for questions in (
            [],
            [{"id": "requirements-review-understanding"}],
            [{"id": "unresolved-business-choice"}],
            [{"id": "workspace-scope-old-scope"}],
            [{"id": "workspace-scope-current-scope"}, {"id": "business-choice"}],
        ):
            with self.subTest(questions=questions):
                candidate = copy.deepcopy(view)
                candidate["needs"]["questions"] = questions
                self.assertFalse(admission.pending_scope(candidate))

    def test_running_incomplete_or_already_decided_views_are_not_ready(self):
        view = scope_view()
        candidates = [
            {},
            {**view, "status": "RUNNING"},
            {**view, "needs": {"kind": "resume"}},
            {**view, "needs": {**view["needs"], "resolver_scope": "operational_exhaustion"}},
            {**view, "scope": {**view["scope"], "decision": "this_repository"}},
            {**view, "scope": {**view["scope"], "external_parts": []}},
            {**view, "scope": {**view["scope"], "identity": None}},
        ]
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                self.assertFalse(admission.pending_scope(candidate))
