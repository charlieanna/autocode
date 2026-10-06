"""Offline controls for the transactional-outbox holdout oracle (#451), never for a model's reports.

`scenarios/run.py check` only asserts that each broken variant fails `hidden_tests_pass`, so a
variant that stopped importing would still read `ok`. These pin which hidden tests each variant
fails: every failure is the defect the variant was written for, never a setup or import error.
"""
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCENARIO = Path(__file__).resolve().parents[1] / "scenarios" / "catalog" / "ladder-19-transactional-outbox"
sys.path.insert(0, str(SCENARIO.parents[1]))
from harness.oracle import IGNORED, hidden_tests

RECOVERY = "test_repeated_crash_after_sink_acceptance_before_ack"
PARTIAL = "test_partial_delivery_preserves_failed_event_identity"
LARGE_LIMIT = "test_arbitrarily_large_positive_limit_preserves_order_and_publishing"
LARGE_AMOUNT = "test_large_amount_is_lossless_in_order_event_and_replay"
# The recovery test hard-kills the publisher after the sink accepted the first, middle and last event.
CRASH_POSITIONS = {"0", "2", "4"}


class OutboxOracleTests(unittest.TestCase):
    def check_hidden(self, variant=None):
        with tempfile.TemporaryDirectory(prefix="outbox-oracle-control-") as tmp:
            project = Path(tmp) / "project"
            shutil.copytree(SCENARIO / "seed", project, ignore=IGNORED)
            shutil.copytree(SCENARIO / "reference", project, dirs_exist_ok=True, ignore=IGNORED)
            if variant:
                shutil.copytree(SCENARIO / "broken" / variant, project, dirs_exist_ok=True, ignore=IGNORED)
            return hidden_tests(project, SCENARIO / "hidden")

    def assert_rejected_by(self, proc, names):
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", proc.stderr, re.M)),
                         set(names), proc.stderr)
        if RECOVERY in names:
            crashed = set(re.findall(rf"^FAIL: {RECOVERY} .*\(crash_index=(\d+)\)$", proc.stderr, re.M))
            self.assertEqual(CRASH_POSITIONS, crashed, proc.stderr)

    def test_reference_passes_every_hidden_test(self):
        proc = self.check_hidden()
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_exception_only_rollback_fails_only_the_process_recovery_test(self):
        # Acknowledges before the callback and restores the event only when the callback raises:
        # a hard process exit after the sink accepted the event skips the restore.
        self.assert_rejected_by(self.check_hidden("ack-with-exception-rollback"), [RECOVERY])

    def test_broken_controls_fail_only_their_targeted_invariants(self):
        expected = {"ack-before-send": [PARTIAL, RECOVERY],
                    "overflowing-limit": [LARGE_LIMIT],
                    "sqlite-integer-range": [LARGE_LIMIT, LARGE_AMOUNT],
                    "replay-changes-event-id": [PARTIAL, RECOVERY, LARGE_LIMIT, LARGE_AMOUNT]}
        self.assertEqual({path.name for path in (SCENARIO / "broken").iterdir()} - {"ack-with-exception-rollback"},
                         set(expected), "every broken outbox variant needs a pinned failure set")
        for name, failures in expected.items():
            with self.subTest(variant=name):
                self.assert_rejected_by(self.check_hidden(name), failures)


if __name__ == "__main__":
    unittest.main()
