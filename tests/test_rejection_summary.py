"""A stop on a refused stage answer names the stage, the problem and what resuming does (2026-11-18).

Every such stop said "The saved report could not be accepted in its current form" with a "Resume after
correction" button, whatever the stage and whatever was wrong, including a read-only investigation's
Requirements answer. These drive the public status view, as a client reads it."""

from __future__ import annotations

import unittest
from pathlib import Path

import autocode_rejection_summary as rejection_summary
import autocode_run_view as run_view

GENERIC = "could not be accepted in its current form"


def view(state):
    return run_view.view(state, workspace=Path("/ws"), run_dir=Path("/run"))


def rejected(stage, reason, **fields):
    return {"stage": stage, "rejected": True, "rejection_reason": reason, "exit_code": 0, **fields}


class RejectedAnswerStopTests(unittest.TestCase):
    def test_a_refused_requirements_answer_names_the_stage_the_problem_and_what_was_tried(self):
        reason = (
            "Assumption A1 supports an unknown requirement id. Every assumption must cite a requirement it "
            "supports. Receipt: /tmp/run/receipts/requirements-01.json"
        )
        state = {
            "status": "PAUSED_INVALID_OUTPUT",
            "next_stage": "requirements_gather",
            "stop_reason": f"Completed requirements_gather output was rejected ({reason}); attempt archived. "
            "Resume explicitly with autocode resume to retry with a fresh request.",
            "stages": [
                rejected("requirements_gather", reason),
                rejected("requirements_gather_report_repair", reason, report_only=True),
                rejected("requirements_gather_report_repair", reason, report_only=True),
            ],
            "stuck_investigations": [
                {
                    "stage": "requirements_gather",
                    "status": "PAUSED_INVALID_OUTPUT",
                    "outcome": "paused",
                    "diagnosis": "The answer cites A1 for R4, which the request never stated. It needs R2.",
                }
            ],
        }
        public = view(state)
        text = public["chat"]["notice"]
        self.assertNotIn(GENERIC, text)
        self.assertIn("AutoCode did not accept the Requirements step's answer.", text)
        self.assertIn("Assumption A1 supports an unknown requirement id.", text)
        self.assertIn("asked the Requirements step to fix its answer twice", text)
        self.assertIn("the Investigator found: the answer cites A1 for R4", text)
        self.assertNotIn("It needs R2", text, "one sentence of the diagnosis")
        self.assertIn("choose “Try the Requirements step again”", text)
        for raw in ("Receipt", "/tmp/", "autocode resume", "PAUSED_"):
            self.assertNotIn(raw, text)
        resume = next(row for row in public["recovery"]["actions"] if row["kind"] == "resume")
        self.assertEqual("Try the Requirements step again", resume["label"])
        self.assertNotIn("corrected", resume["effect"])
        # The raw cause stays in the run's details.
        self.assertEqual(state["stop_reason"], public["recovery"]["saved_reason"])

    def test_an_investigators_own_refused_report_is_not_given_as_the_stages_reason(self):
        state = {
            "status": "PAUSED_INVALID_OUTPUT",
            "next_stage": "tester",
            "stages": [
                rejected(
                    "tester", "Protected tests changed and the original gate did not pass on this implementation."
                ),
                rejected("investigate_stuck", "A probed diagnosed cause needs its example in plain English."),
            ],
        }
        text = view(state)["recovery"]["what_happened"]
        self.assertIn("Tester step's answer. Protected tests changed", text)
        self.assertNotIn("plain English", text)

    def test_commands_flags_and_scratch_paths_never_reach_the_person(self):
        shown = rejection_summary.reason(
            "Check `npm test --silent` was reported as exit 0, but when the runner re-ran it from "
            "/tmp/claude/ws it exited 1. Cite check:<n> naming a listed check."
        )
        self.assertEqual("Cite check:<n> naming a listed check.", shown)
        self.assertEqual(
            "Check “npm test” must exit 0, but it exited 1 in a saved file.",
            rejection_summary.reason("Check `npm test` must exit 0, but it exited 1 in /tmp/x/run."),
        )
        self.assertEqual("", rejection_summary.reason("PAUSED_INVALID_OUTPUT --resume-paused"))

    def test_a_reason_with_nothing_readable_says_so_instead_of_a_vague_report_sentence(self):
        state = {
            "status": "PAUSED_INVALID_OUTPUT",
            "next_stage": "draft_plan",
            "stop_reason": "Completed draft_plan output was rejected (`--flag` SOME_TOKEN); attempt archived.",
        }
        text = view(state)["recovery"]["what_happened"]
        self.assertEqual(
            "AutoCode did not accept the Planner step's answer; the reason it recorded names only internal details. "
            "It stopped rather than spend more on the same answer.",
            text,
        )

    def test_a_metadata_stop_is_not_called_a_report_problem(self):
        for status in ("PAUSED_METADATA", "PAUSED_METADATA_ADMISSION"):
            with self.subTest(status=status):
                text = view({"status": status, "next_stage": "builder", "stop_reason": "x"})["recovery"][
                    "what_happened"
                ]
                self.assertNotIn("report", text)
                self.assertNotIn("answer", text)

    def test_other_stops_are_left_to_their_own_text(self):
        self.assertIsNone(rejection_summary.summary({}, "PAUSED_JOB_FAILURE", "Builder", "builder"))


if __name__ == "__main__":
    unittest.main()
