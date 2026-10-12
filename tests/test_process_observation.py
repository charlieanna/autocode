"""wait_for_stage observes activity from the start and ages it from the monitor's own sample (#621)."""

import subprocess
import sys
import threading
import unittest
from unittest import mock

import autocode_process as processes


class SamplingInstantTests(unittest.TestCase):
    def test_the_monitors_sample_is_used_only_between_the_two_readings_around_its_poll(self):
        monitor = mock.Mock(spec=["sampled_at"])
        for sampled, expected in (
            (3.0, 3.0),  # sampled after waiting 2 s for the lock: that wait is not charged again
            (1.0, 1.0),
            (4.0, 4.0),
            (0.5, 1.0),  # before the poll began: another clock, not this poll's sample
            (9.0, 1.0),  # in the future
            (float("nan"), 1.0),
            (True, 1.0),
            ("3", 1.0),
            (None, 1.0),
        ):
            with self.subTest(sampled=sampled):
                monitor.sampled_at = sampled
                self.assertEqual(expected, processes._sampling_instant(monitor, 1.0, 4.0))
        self.assertEqual(1.0, processes._sampling_instant(object(), 1.0, 4.0))  # a custom monitor keeps none


class ObservationOrderTests(unittest.TestCase):
    def test_events_are_observed_while_root_capture_inspects_the_tree(self):
        polled = threading.Event()

        class Monitor:
            idle_limit = tool_limit = 0

            def poll(self, processes=None, root_pid=None):
                polled.set()
                return {
                    "idle_seconds": 0,
                    "tool_elapsed_seconds": None,
                    "idle_limit_seconds": 0,
                    "tool_limit_seconds": 0,
                }

        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.5)"], start_new_session=True)
        self.addCleanup(lambda: child.poll() is None and (child.kill(), child.wait()))
        capture = processes.ProcessTree.capture_root
        seen = {}
        reaped_before_capture = []
        poll = child.poll

        def capture_root(tree):
            # Root capture can block on native inspection; a queued tool start must still be read meanwhile.
            seen["polled_during_capture"] = polled.wait(5)
            seen["captured"] = True
            return capture(tree)

        def child_poll():
            if not seen.get("captured"):
                reaped_before_capture.append(True)
            return poll()

        with (
            mock.patch.object(processes.ProcessTree, "capture_root", capture_root),
            mock.patch.object(child, "poll", side_effect=child_poll),
        ):
            processes.wait_for_stage(child, None, lambda rows: None, activity=Monitor())
        self.assertTrue(seen["polled_during_capture"])
        self.assertEqual([], reaped_before_capture)  # a fast exit is never reaped before its identity is captured


if __name__ == "__main__":
    unittest.main()
