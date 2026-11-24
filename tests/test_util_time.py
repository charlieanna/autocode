"""One reading of a saved timestamp (autocode_util.aware_time) for every rule that compares saved times."""

import datetime as dt
import pathlib
import unittest

import autocode_recovery_streak as recovery_streak
import autocode_util as util

TOOLS = pathlib.Path(util.__file__).resolve().parent
# Modules that each carried a private copy of this reading before it moved to autocode_util.
READERS = (
    "autocode_climb_cap",
    "autocode_escalation_view",
    "autocode_route_ladder",
    "autocode_recovery_history",
    "autocode_recovery_streak",
    "autocode_resolver_recovery",
    "autocode_usage_outcomes",
)


class AwareTimeTests(unittest.TestCase):
    def test_an_offset_or_z_timestamp_reads_as_the_same_aware_time(self):
        expected = dt.datetime(2026, 11, 23, 20, 0, tzinfo=dt.UTC)
        self.assertEqual(expected, util.aware_time("2026-11-23T20:00:00+00:00"))
        self.assertEqual(expected, util.aware_time("2026-11-23T20:00:00Z"))
        self.assertEqual(expected, util.aware_time("2026-11-23T21:00:00+01:00"))

    def test_a_missing_unreadable_or_naive_time_is_none(self):
        for value in (None, "", "yesterday", "2026-11-23T20:00:00", 1700000000, 17.5, ["2026-11-23T20:00:00Z"]):
            with self.subTest(value=value):
                self.assertIsNone(util.aware_time(value))

    def test_the_recovery_streak_reads_the_same_answer(self):
        for value in ("2026-11-23T20:00:00Z", "2026-11-23T20:00:00", None, 5):
            with self.subTest(value=value):
                self.assertEqual(util.aware_time(value), recovery_streak.timestamp(value))

    def test_no_reader_parses_saved_times_itself_again(self):
        for name in READERS:
            with self.subTest(module=name):
                self.assertNotIn("fromisoformat", (TOOLS / f"{name}.py").read_text())


if __name__ == "__main__":
    unittest.main()
