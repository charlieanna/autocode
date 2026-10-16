"""Terminal metadata is displayed literally at the human decision boundary."""

import contextlib
import io
import json
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

    def pending_view(self):
        return {
            "status": "WAITING_FOR_USER",
            "needs": {"kind": "review", "criteria": ["C1"], "token": "current-review"},
            "verification": {"coverage": [{"id": "C1", "criterion": "Readable greeting", "human_review": True}]},
            "changes_by_criterion": {"criteria": [{"id": "C1", "checkpoints": [], "overlapping": False}]},
        }

    def test_json_includes_criterion_diffs_for_review_criteria(self):
        run = SimpleNamespace(
            workspace=Path("/tmp/review-fixture"), run_dir=Path("/tmp/run"), status=lambda **_: self.pending_view()
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, review_cli.review(run, as_json=True))
        shown = json.loads(output.getvalue())["criterion_diffs"]
        self.assertEqual([{"id": "C1", "checkpoints": [], "overlapping": False}], shown["criteria"])
        self.assertEqual("unavailable", shown["availability"])

    def test_text_shows_the_attributed_steps_section(self):
        text = self.display(self.pending_view())
        self.assertIn("Saved Builder steps attributed to this criterion", text)
        self.assertIn("No saved Builder step is attributed to this criterion yet.", text)
