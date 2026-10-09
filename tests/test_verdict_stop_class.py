"""Separate outcomes for false completion, safety stops, provider failures, budgets (#455)."""
from __future__ import annotations

import unittest

from scenarios import run  # Establish the scenario harness import root.
from harness import stats, verdict


class StopClassTests(unittest.TestCase):
    def test_pass_false_and_skipped_keep_their_own_classes(self):
        self.assertEqual(verdict.STOP_CLASS_PASSED, verdict.stop_class(verdict.PASS, "TASK_COMPLETE"))
        self.assertEqual(verdict.STOP_CLASS_FALSE, verdict.stop_class(verdict.FALSE_COMPLETE, "TASK_COMPLETE"))
        self.assertEqual(verdict.STOP_CLASS_SKIPPED, verdict.stop_class(verdict.SKIPPED))
        self.assertEqual(verdict.STOP_CLASS_NOT_EXERCISED, verdict.stop_class(verdict.NOT_EXERCISED))

    def test_harness_and_budget_are_not_product_stops(self):
        self.assertEqual(verdict.STOP_CLASS_HARNESS,
                         verdict.stop_class(verdict.ERROR, "RUNNING", "harness stopped: drive died"))
        self.assertEqual(verdict.STOP_CLASS_BUDGET,
                         verdict.stop_class(verdict.INTERRUPTED_UNGRADED, "", "time budget used up"))
        self.assertEqual(verdict.STOP_CLASS_BUDGET,
                         verdict.stop_class(verdict.HONEST_BLOCKER, "PAUSED_BUDGET", "iteration ceiling"))
        self.assertEqual(verdict.STOP_CLASS_BUDGET,
                         verdict.stop_class(verdict.HONEST_BLOCKER, "PAUSED_INTERRUPTED", "workers stopped"))

    def test_provider_and_setup_stops_are_not_safety_stops(self):
        self.assertEqual(verdict.STOP_CLASS_PROVIDER,
                         verdict.stop_class(verdict.HONEST_BLOCKER, "PAUSED_TOOL_CONTAINMENT",
                                            "strict tool containment requires macOS sandbox-exec"))
        self.assertEqual(verdict.STOP_CLASS_PROVIDER,
                         verdict.stop_class(verdict.HONEST_BLOCKER, "PAUSED_CONTENT_FILTER", "refused"))
        self.assertEqual(verdict.STOP_CLASS_PROVIDER,
                         verdict.stop_class(verdict.HONEST_BLOCKER, "PAUSED_UNCERTAIN_STAGE",
                                            "OpenCode exhausted its output token limit"))

    def test_product_and_operator_pauses_are_safety_stops(self):
        self.assertEqual(verdict.STOP_CLASS_SAFETY,
                         verdict.stop_class(verdict.HONEST_BLOCKER, "PAUSED_INVALID_OUTPUT",
                                            "CLI output differs from the original brief format"))
        self.assertEqual(verdict.STOP_CLASS_SAFETY,
                         verdict.stop_class(verdict.HONEST_BLOCKER, "WAITING_FOR_USER",
                                            "Should this run stop or continue?"))
        self.assertEqual(verdict.STOP_CLASS_SAFETY,
                         verdict.stop_class(verdict.HONEST_BLOCKER, "PAUSED_BUILDER_CLASSIFICATION",
                                            "Builder completed without source changes"))

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
        rows = stats.summarize([
            {"scenario": "s", "mode": "fake", "verdict": verdict.PASS, "summary": "", "started_at": "1"},
            {"scenario": "s", "mode": "fake", "verdict": verdict.FALSE_COMPLETE, "summary": "",
             "status": "TASK_COMPLETE", "started_at": "2"},
            {"scenario": "s", "mode": "fake", "verdict": verdict.HONEST_BLOCKER, "summary": "",
             "status": "PAUSED_TOOL_CONTAINMENT", "started_at": "3"},
            {"scenario": "s", "mode": "fake", "verdict": verdict.HONEST_BLOCKER, "summary": "",
             "status": "PAUSED_BUDGET", "started_at": "4"},
        ])
        self.assertEqual({"passed": 1, "false_completion": 1, "provider_failure": 1, "budget_exhaustion": 1},
                         rows[0]["stop_classes"])
        self.assertEqual(1, rows[0]["passes"])


if __name__ == "__main__":
    unittest.main()
