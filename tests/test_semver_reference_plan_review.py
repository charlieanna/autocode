"""The offline editor fixture approves only the complete reviewed plan."""

import copy
import hashlib
import unittest
from unittest import mock

from tests import semver_reference_vscode_fixture as fixture


class SemverPlanReviewTests(unittest.TestCase):
    def setUp(self):
        self.python = "/owned/.venv/bin/python"
        self.plan = {
            "revision": 2,
            "hash": "a" * 64,
            "token": "r2:" + "a" * 64,
            "review_markdown": r"Run /owned/\.venv/bin/python -m pytest",
            "review_summary_markdown": "One scoped task",
            "constraints": ["Only the task files"],
            "scope_exclusions": ["No publishing"],
            "milestones": [{"affected_paths": ["src/file.py"]}],
            "end_to_end_flow": ["Run /owned/.venv/bin/python -m pytest"],
        }
        self.need = {"kind": "approve_plan", "token": self.plan["token"]}
        digest = hashlib.sha256(fixture.canonical_plan(self.plan, self.python)).hexdigest()
        self.review = mock.patch.object(fixture, "REVIEWED_PLAN_DIGEST", digest)
        self.review.start()
        self.addCleanup(self.review.stop)

    def test_exact_plan_and_interpreter_variation_remain_bound(self):
        fixture.require_reviewed_plan(self.plan, self.need, self.python)
        other = copy.deepcopy(self.plan)
        other["review_markdown"] = r"Run /other/\.venv/bin/python -m pytest"
        other["end_to_end_flow"] = ["Run /other/.venv/bin/python -m pytest"]
        fixture.require_reviewed_plan(other, self.need, "/other/.venv/bin/python")

    def test_changed_summary_scope_or_extra_field_fails_closed(self):
        for key, value in (
            ("review_summary_markdown", "Another summary"),
            ("scope_exclusions", []),
            ("extra", {"unreviewed": True}),
        ):
            with self.subTest(key=key):
                changed = copy.deepcopy(self.plan)
                changed[key] = value
                with self.assertRaisesRegex(AssertionError, "full displayed plan differs"):
                    fixture.require_reviewed_plan(changed, self.need, self.python)

    def test_stale_or_malformed_approval_tokens_are_rejected(self):
        for need in (
            {"kind": "approve_plan", "token": "r2:" + "b" * 64},
            {"kind": "continue", "token": self.plan["token"]},
        ):
            with self.subTest(need=need), self.assertRaisesRegex(AssertionError, "approval token"):
                fixture.require_reviewed_plan(self.plan, need, self.python)
        changed = copy.deepcopy(self.plan)
        changed["token"] = "r3:" + "a" * 64
        with self.assertRaisesRegex(AssertionError, "approval token"):
            fixture.require_reviewed_plan(changed, self.need, self.python)


if __name__ == "__main__":
    unittest.main()
