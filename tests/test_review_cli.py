"""Terminal metadata is displayed literally at the human decision boundary."""

import contextlib
import io
import unittest
from pathlib import Path
from types import SimpleNamespace

import autocode_review_cli as review_cli


class ReviewCLIDisplayTests(unittest.TestCase):
    def display(self, view):
        run = SimpleNamespace(
            workspace=Path("/tmp/review-fixture"),
            run_dir=Path("/tmp/review-fixture/.autocode/runs/example"),
            status=lambda **_kwargs: view,
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, review_cli.review(run, show=True))
        return output.getvalue()

    def test_show_escapes_question_reasons_and_criterion_ids(self):
        cid = "C1\x1b[2J"
        text = self.display(
            {
                "status": "WAITING_FOR_USER",
                "needs": {
                    "kind": "review",
                    "criteria": [cid],
                    "token": "current-review",
                    "question": "Review this artifact\x1b[8m",
                },
                "verification": {
                    "freshness": "current",
                    "reasons": ["Evidence reason\x1b[2K"],
                    "coverage": [{"id": cid, "criterion": "Readable greeting", "human_review": True}],
                },
            }
        )
        self.assertNotIn("\x1b", text)
        self.assertIn("Review this artifact\\x1b[8m", text)
        self.assertIn("Evidence reason\\x1b[2K", text)
        self.assertIn("C1\\x1b[2J: Readable greeting", text)

    def test_no_pending_review_escapes_the_next_action_reason(self):
        text = self.display(
            {
                "status": "PAUSED_STALE_VALIDATION",
                "needs": {"kind": "resume", "reason": "Source changed\x1b[8m"},
            }
        )
        self.assertNotIn("\x1b", text)
        self.assertIn("Source changed\\x1b[8m", text)
        self.assertIn("--explain", text)
