"""Pure wall-versus-monotonic supervision timing evidence."""

import unittest

import autocode_elapsed_timing as elapsed_timing


class ElapsedTimingTests(unittest.TestCase):
    def test_matching_clocks_report_no_gap(self):
        admitted = elapsed_timing.admission(1000, 50, 300)
        observed = elapsed_timing.observation(admitted, wall_now=1020, monotonic_now=70)
        self.assertEqual(
            {
                "monotonic_elapsed_seconds": 20.0,
                "wall_elapsed_seconds": 20.0,
                "admitted_stage_limit_seconds": 300.0,
                "excess_wall_seconds": 0.0,
                "suspension_tolerance_seconds": elapsed_timing.SUSPENSION_TOLERANCE_SECONDS,
                "likely_host_or_vm_suspension": False,
            },
            observed,
        )
        self.assertEqual(observed, elapsed_timing.normalize_observation(observed))
        unbounded = elapsed_timing.observation(
            elapsed_timing.admission(1000, 50, None), wall_now=1020, monotonic_now=70
        )
        self.assertEqual(unbounded, elapsed_timing.normalize_observation(unbounded))
        missing_limit = dict(unbounded)
        missing_limit.pop("admitted_stage_limit_seconds")
        self.assertIsNone(elapsed_timing.normalize_observation(missing_limit))

    def test_large_wall_only_jump_reports_likely_host_or_vm_suspension(self):
        admitted = elapsed_timing.admission(1000, 50, None)
        observed = elapsed_timing.observation(admitted, wall_now=1120, monotonic_now=70)
        self.assertEqual(
            (20.0, 120.0, 100.0),
            tuple(
                observed[key] for key in ("monotonic_elapsed_seconds", "wall_elapsed_seconds", "excess_wall_seconds")
            ),
        )
        self.assertTrue(observed["likely_host_or_vm_suspension"])
        boundary = elapsed_timing.observation(
            admitted,
            wall_now=1020 + elapsed_timing.SUSPENSION_TOLERANCE_SECONDS,
            monotonic_now=70,
        )
        self.assertFalse(boundary["likely_host_or_vm_suspension"])

    def test_nonfinite_declarations_and_malformed_observations_are_unknown(self):
        for value in (True, float("nan"), float("inf")):
            with self.subTest(value=value):
                self.assertIsNone(elapsed_timing.admission(value, 50, 300))
                self.assertIsNone(elapsed_timing.admission(1000, value, 300))
                self.assertIsNone(elapsed_timing.admission(1000, 50, value))
        self.assertIsNone(elapsed_timing.admission(1000, -1, 300))
        self.assertIsNone(elapsed_timing.normalize_observation({}))
        observed = elapsed_timing.observation(elapsed_timing.admission(1000, 50, 300), wall_now=1120, monotonic_now=70)
        observed["likely_host_or_vm_suspension"] = False
        self.assertIsNone(elapsed_timing.normalize_observation(observed))


if __name__ == "__main__":
    unittest.main()
