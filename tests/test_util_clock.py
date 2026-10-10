"""The #704 clock seam: one monotonic clock and one wait in autocode_util.

autocode_process (the supervision watchdog) reads all of its deadlines through
util.monotonic() and util.sleep(), so a fake that advances on demand replaces
every real wait. These tests pin the seam and demonstrate the payoff.
"""

import pathlib
import time as stdlib_time
import unittest
from unittest import mock

import autocode_process
import autocode_util as util

TOOLS = pathlib.Path(autocode_process.__file__).resolve().parent


class ClockSeamTests(unittest.TestCase):
    def test_the_seam_delegates_to_real_time(self):
        before = util.monotonic()
        util.sleep(0)
        self.assertGreaterEqual(util.monotonic(), before)

    def test_the_watchdog_module_reads_time_only_through_the_seam(self):
        source = (TOOLS / "autocode_process.py").read_text(encoding="utf-8")
        self.assertNotIn("time.monotonic", source, "direct time.monotonic bypasses the #704 seam")
        self.assertNotIn("time.sleep", source, "direct time.sleep bypasses the #704 seam")
        self.assertIn("util.monotonic()", source)
        self.assertIn("util.sleep(", source)

    def test_a_fake_clock_runs_a_watchdog_deadline_loop_in_milliseconds(self):
        steps = [0]

        def fake_monotonic():
            return 1000.0 + steps[0] / 20  # recomputed, not accumulated: no float drift

        def fake_sleep(seconds):
            steps[0] += 1  # the fake advances on demand instead of waiting

        with mock.patch.object(util, "monotonic", fake_monotonic), mock.patch.object(util, "sleep", fake_sleep):
            start = stdlib_time.monotonic()
            # The exact loop shape autocode_process uses for its cleanup deadlines.
            deadline = util.monotonic() + 2
            iterations = 0
            while util.monotonic() < deadline:
                iterations += 1
                util.sleep(0.05)
            elapsed = stdlib_time.monotonic() - start

        self.assertEqual(40, iterations, "2s of deadline at 0.05s per poll")
        self.assertLess(elapsed, 0.05, f"the loop must not take real time; took {elapsed:.4f}s")


if __name__ == "__main__":
    unittest.main()
