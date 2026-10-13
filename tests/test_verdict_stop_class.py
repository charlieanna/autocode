"""Separate outcomes for false completion, safety stops, provider failures, budgets (#455)."""

from __future__ import annotations

import subprocess
import unittest

# isort: split
from scenarios import run  # noqa: F401, I001 - initialize the harness before importing it

# isort: split
from harness import stats, verdict


class StopClassTests(unittest.TestCase):
    def test_pass_false_and_skipped_keep_their_own_classes(self):
        self.assertEqual(verdict.STOP_CLASS_PASSED, verdict.stop_class(verdict.PASS, "TASK_COMPLETE"))
        self.assertEqual(verdict.STOP_CLASS_FALSE, verdict.stop_class(verdict.FALSE_COMPLETE, "TASK_COMPLETE"))
        self.assertEqual(verdict.STOP_CLASS_SKIPPED, verdict.stop_class(verdict.SKIPPED))
        self.assertEqual(verdict.STOP_CLASS_NOT_EXERCISED, verdict.stop_class(verdict.NOT_EXERCISED))

    def test_harness_and_budget_are_not_product_stops(self):
        self.assertEqual(
            verdict.STOP_CLASS_HARNESS, verdict.stop_class(verdict.ERROR, "RUNNING", "harness stopped: drive died")
        )
        self.assertEqual(
            verdict.STOP_CLASS_BUDGET, verdict.stop_class(verdict.INTERRUPTED_UNGRADED, "", "time budget used up")
        )
        self.assertEqual(
            verdict.STOP_CLASS_BUDGET, verdict.stop_class(verdict.HONEST_BLOCKER, "PAUSED_BUDGET", "iteration ceiling")
        )
        self.assertEqual(
            verdict.STOP_CLASS_BUDGET,
            verdict.stop_class(verdict.HONEST_BLOCKER, "PAUSED_INTERRUPTED", "workers stopped"),
        )

    def test_provider_and_setup_stops_are_not_safety_stops(self):
        self.assertEqual(
            verdict.STOP_CLASS_PROVIDER,
            verdict.stop_class(
                verdict.HONEST_BLOCKER, "PAUSED_TOOL_CONTAINMENT", "strict tool containment requires macOS sandbox-exec"
            ),
        )
        self.assertEqual(
            verdict.STOP_CLASS_PROVIDER, verdict.stop_class(verdict.HONEST_BLOCKER, "PAUSED_CONTENT_FILTER", "refused")
        )
        self.assertEqual(
            verdict.STOP_CLASS_PROVIDER,
            verdict.stop_class(
                verdict.HONEST_BLOCKER, "PAUSED_UNCERTAIN_STAGE", "OpenCode exhausted its output token limit"
            ),
        )

    def test_verification_preparation_expiry_is_typed_across_authentication_phases(self):
        for command in (
            "Verification source packing",
            "Verification source authentication",
            "Verification source final authentication",
        ):
            with self.subTest(command=command):
                summary = str(subprocess.TimeoutExpired(command, 900))
                self.assertEqual(
                    verdict.STOP_CLASS_BUDGET,
                    verdict.stop_class(verdict.HONEST_BLOCKER, "PAUSED_VERIFICATION_PREPARATION", summary),
                )

    def test_product_and_operator_pauses_are_safety_stops(self):
        self.assertEqual(
            verdict.STOP_CLASS_SAFETY,
            verdict.stop_class(
                verdict.HONEST_BLOCKER, "PAUSED_INVALID_OUTPUT", "CLI output differs from the original brief format"
            ),
        )
        self.assertEqual(
            verdict.STOP_CLASS_SAFETY,
            verdict.stop_class(verdict.HONEST_BLOCKER, "WAITING_FOR_USER", "Should this run stop or continue?"),
        )
        self.assertEqual(
            verdict.STOP_CLASS_SAFETY,
            verdict.stop_class(
                verdict.HONEST_BLOCKER, "PAUSED_BUILDER_CLASSIFICATION", "Builder completed without source changes"
            ),
        )

    def test_every_class_is_in_the_documented_set(self):
        for outcome, status, summary in (
            (verdict.PASS, "TASK_COMPLETE", ""),
            (verdict.FALSE_COMPLETE, "TASK_COMPLETE", ""),
            (verdict.ERROR, "", "oracle error"),
            (verdict.HONEST_BLOCKER, "PAUSED_TOOL_CONTAINMENT", ""),
            (verdict.HONEST_BLOCKER, "PAUSED_BUDGET", ""),
            (verdict.HONEST_BLOCKER, "WAITING_FOR_USER", ""),
        ):
            self.assertIn(verdict.stop_class(outcome, status, summary), verdict.STOP_CLASSES)


class StatsStopClassTests(unittest.TestCase):
    def test_summarize_counts_stop_classes_beside_the_verdict(self):
        rows = stats.summarize(
            [
                {"scenario": "s", "mode": "fake", "verdict": verdict.PASS, "summary": "", "started_at": "1"},
                {
                    "scenario": "s",
                    "mode": "fake",
                    "verdict": verdict.FALSE_COMPLETE,
                    "summary": "",
                    "status": "TASK_COMPLETE",
                    "started_at": "2",
                },
                {
                    "scenario": "s",
                    "mode": "fake",
                    "verdict": verdict.HONEST_BLOCKER,
                    "summary": "",
                    "status": "PAUSED_TOOL_CONTAINMENT",
                    "started_at": "3",
                },
                {
                    "scenario": "s",
                    "mode": "fake",
                    "verdict": verdict.HONEST_BLOCKER,
                    "summary": "",
                    "status": "PAUSED_BUDGET",
                    "started_at": "4",
                },
            ]
        )
        self.assertEqual(
            {"passed": 1, "false_completion": 1, "provider_failure": 1, "budget_exhaustion": 1}, rows[0]["stop_classes"]
        )
        self.assertEqual(1, rows[0]["passes"])
        table = stats.format_table(rows)
        self.assertIn("stop classes", table.splitlines()[0])
        self.assertIn("passed=1 false_completion=1 provider_failure=1 budget_exhaustion=1", table)

    def test_format_shows_a_dash_for_a_row_without_stop_classes(self):
        self.assertEqual("-", stats.format_stop_classes({}))
        self.assertEqual("safety_stop=2", stats.format_stop_classes({"safety_stop": 2, "passed": 0}))

    def test_decisions_are_counted_by_who_made_them(self):
        """#455: the person's own answers, served defaults and auto-served approvals are reported apart;
        the harness approving a plan is not the product deciding on its own."""
        results = [
            {
                "scenario": "s",
                "mode": "fake",
                "verdict": verdict.PASS,
                "summary": "",
                "started_at": "1",
                "answers": [
                    {"id": "Q1", "answer": "Keep data.", "source": "proposed_default"},
                    {"id": "Q2", "answer": "Mine.", "source": "person", "explicit": True},
                    {"id": "C1", "answer": "approved", "source": "approval"},
                ],
                "approvals": [
                    {"gate": "approve_plan", "token": "plan:1"},
                    {"gate": "review", "criterion": "C1", "token": "review:1"},
                ],
            },
            {
                "scenario": "s",
                "mode": "fake",
                "verdict": verdict.HONEST_BLOCKER,
                "summary": "",
                "status": "PAUSED_BUDGET",
                "started_at": "2",
                "answers": [{"id": "Q1", "answer": "Keep data.", "source": "proposed_default"}],
                "approvals": [{"gate": "approve_plan", "token": "plan:2"}],
            },
        ]
        (row,) = stats.summarize(results)
        self.assertEqual((1, 2, 3), (row["person_answers"], row["default_answers"], row["approvals"]))
        header, _, line = stats.format_table([row]).splitlines()
        self.assertIn("person answers  default answers  approvals", header)
        self.assertEqual(["passed=1", "budget_exhaustion=1", "1", "2", "3"], line.split()[-5:], line)

    def test_answers_saved_before_sources_were_recorded_are_counted_by_what_they_say(self):
        """A result from before `source` and `approvals` existed: the person's own answers (flagged explicit),
        review acceptances (a bare `approved`, which would overstate the served defaults) and defaults."""
        counted = stats.decisions(
            [
                {
                    "answers": [
                        {"id": "Q1", "answer": "Keep data."},
                        {"id": "Q2", "answer": "Mine.", "explicit": True},
                        {"id": "C1", "answer": "approved"},  # an old review acceptance has no source
                        {"id": "C2", "answer": "approved"},
                        {"id": "Q3", "answer": "approved", "explicit": True},  # the person said it
                    ]
                },
                {"answers": None},
                {},
            ]
        )
        self.assertEqual({"person_answers": 2, "default_answers": 1, "approvals": 2}, counted)

    def test_a_result_with_sources_is_never_recounted_by_its_answer_text(self):
        counted = stats.decisions(
            [
                {
                    "answers": [
                        {"id": "Q1", "answer": "approved", "source": "proposed_default"},
                        {"id": "C1", "answer": "approved", "source": "approval"},
                    ],
                    "approvals": [{"gate": "review", "criterion": "C1", "token": "review:1"}],
                }
            ]
        )
        self.assertEqual({"person_answers": 0, "default_answers": 1, "approvals": 1}, counted)

    def test_an_unfinished_attempt_reports_no_decisions(self):
        (row,) = stats.summarize(
            [
                {
                    "scenario": "s",
                    "mode": "fake",
                    "verdict": verdict.INTERRUPTED_UNGRADED,
                    "summary": "",
                    "started_at": "1",
                }
            ]
        )
        self.assertEqual((0, 0, 0), (row["person_answers"], row["default_answers"], row["approvals"]))
        self.assertEqual({"budget_exhaustion": 1}, row["stop_classes"])


if __name__ == "__main__":
    unittest.main()
