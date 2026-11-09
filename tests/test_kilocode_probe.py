"""Model-free setup retries use fake time and retain every attempt's outcome."""

import subprocess
import unittest
from unittest.mock import Mock, patch

from providers import kilocode_probe as probe


class KiloProbeTests(unittest.TestCase):
    def setUp(self):
        self.clock = 0.0
        clock = patch.object(probe.time, "monotonic", lambda: self.clock)
        clock.start()
        self.addCleanup(clock.stop)
        self.command = ["kilo", "debug", "agent", "code"]

    def timeout(self, seconds):
        self.clock += seconds
        raise subprocess.TimeoutExpired(self.command, seconds, output="private native config")

    def test_one_timeout_retries_with_only_the_remaining_budget_and_records_both_attempts(self):
        result = subprocess.CompletedProcess(self.command, 0, "{}", "")
        calls = []

        def run(seconds):
            calls.append(seconds)
            if len(calls) == 1:
                return self.timeout(seconds)
            self.clock += 12
            return result

        with probe.capture() as rows:
            self.assertIs(result, probe.probe(self.command, run))
        self.assertEqual([60, 60], calls)
        self.assertEqual(["timed_out", "completed"], [row["outcome"] for row in rows])
        self.assertEqual([60, 12], [row["duration_seconds"] for row in rows])
        self.assertEqual([1, 2], [row["attempt"] for row in rows])
        self.assertEqual("model_free_timeout_retry", rows[1]["reason"])
        self.assertNotIn("private native config", str(rows))
        self.assertIsNone(probe.observer())

    def test_exhaustion_reports_actual_attempt_count_and_retains_the_timeout_cause(self):
        with probe.capture() as rows, self.assertRaises(probe.KiloProbeTimeout) as caught:
            probe.probe(self.command, self.timeout)
        self.assertEqual(120, self.clock)
        self.assertEqual([60, 60], [row["timeout_seconds"] for row in rows])
        self.assertIn("120-second setup budget after 2 attempt(s)", str(caught.exception))
        self.assertIn("no model request was launched", str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__, subprocess.TimeoutExpired)

    def test_elapsed_cleanup_time_cannot_expand_the_shared_deadline(self):
        def overshoot(seconds):
            self.clock += 80
            return self.timeout(seconds)

        with probe.capture() as rows, self.assertRaises(probe.KiloProbeTimeout) as caught:
            probe.probe(self.command, overshoot)
        self.assertEqual(1, len(rows))
        self.assertIn("after 1 attempt(s)", str(caught.exception))

    def test_a_smaller_outer_budget_is_preserved(self):
        for budget in (0.5, 15):
            with self.subTest(budget=budget):
                self.clock = 0
                with probe.capture() as rows, self.assertRaises(probe.KiloProbeTimeout):
                    probe.probe(self.command, self.timeout, seconds=budget)
                self.assertEqual(budget, self.clock)
                self.assertEqual([budget], [row["timeout_seconds"] for row in rows])

    def test_os_errors_and_nonzero_exits_are_never_retried(self):
        missing = FileNotFoundError("missing executable")
        run = Mock(side_effect=missing)
        with probe.capture() as rows, self.assertRaises(FileNotFoundError) as caught:
            probe.probe(self.command, run)
        self.assertIs(missing, caught.exception)
        run.assert_called_once_with(60)
        self.assertEqual("exception", rows[0]["outcome"])
        result = subprocess.CompletedProcess(self.command, 1, "private output", "private error")
        run = Mock(return_value=result)
        with probe.capture() as rows:
            self.assertIs(result, probe.probe(self.command, run))
        run.assert_called_once_with(60)
        self.assertEqual(("failed", 1), (rows[0]["outcome"], rows[0]["exit_code"]))
        self.assertNotIn("private", str(rows))

    def test_model_calls_and_tool_execution_are_rejected_before_dispatch(self):
        for command in (["kilo", "run"], ["kilo", "debug", "agent", "code", "--tool", "bash"]):
            with self.subTest(command=command):
                run = Mock()
                with self.assertRaisesRegex(ValueError, "model-free"):
                    probe.probe(command, run)
                run.assert_not_called()

    def test_nested_capture_preserves_the_outer_observer_without_duplicating_an_attempt(self):
        run = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
        with probe.capture() as outer:
            parent = probe.observer()
            with probe.capture() as inner:
                probe.probe(["kilo", "models", "--verbose"], run)
            self.assertIs(parent, probe.observer())
            probe.probe(["kilo", "debug", "config"], run)
        self.assertEqual(2, len(outer))
        self.assertEqual([outer[0]], inner)
