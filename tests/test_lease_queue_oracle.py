"""Offline controls for the lease contract oracle, never for a model's reports."""
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCENARIO = Path(__file__).resolve().parents[1] / "scenarios" / "catalog" / "ladder-18-durable-lease-queue"
sys.path.insert(0, str(SCENARIO.parents[1]))
from harness.oracle import IGNORED, hidden_tests


def claim_variant(guard, action):
    return ("\n_original_claim = LeaseQueue.claim\n"
            "def claim(self, now, lease_seconds):\n"
            f"    if {guard}:\n        {action}\n"
            "    return _original_claim(self, now, lease_seconds)\n"
            "LeaseQueue.claim = claim\n")


INVALID_TYPE = "type(now) is not int or type(lease_seconds) is not int"
INVALID_TIME = f"{INVALID_TYPE} or now < 0 or lease_seconds <= 0"
INVALID_TEST = "test_invalid_time_and_unknown_ack"


class LeaseOracleTests(unittest.TestCase):
    def check_hidden(self, extra="", overlay=None):
        with tempfile.TemporaryDirectory(prefix="lease-oracle-control-") as tmp:
            project = Path(tmp) / "project"
            shutil.copytree(SCENARIO / "seed", project, ignore=IGNORED)
            shutil.copytree(SCENARIO / "reference", project, dirs_exist_ok=True, ignore=IGNORED)
            if overlay:
                shutil.copytree(overlay, project, dirs_exist_ok=True, ignore=IGNORED)
            source = project / "leasequeue" / "__init__.py"
            source.write_text(source.read_text() + extra)
            return hidden_tests(project, SCENARIO / "hidden")

    def assert_rejected_by(self, proc, names):
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", proc.stderr, re.M)),
                         set(names), proc.stderr)

    def test_reference_valueerror_convention_passes(self):
        proc = self.check_hidden()
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_typeerror_for_bools_is_allowed(self):
        proc = self.check_hidden(claim_variant(
            "isinstance(now, bool) or isinstance(lease_seconds, bool)",
            "raise TypeError('invalid type')"))
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_typeerror_for_other_types_is_allowed(self):
        proc = self.check_hidden(claim_variant(
            "not isinstance(now, int) or not isinstance(lease_seconds, int)",
            "raise TypeError('invalid type')"))
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_silent_invalid_claim_is_rejected(self):
        proc = self.check_hidden(claim_variant(INVALID_TIME, "return None"))
        self.assert_rejected_by(proc, [INVALID_TEST])

    def test_overflow_and_arbitrary_crashes_are_not_validation(self):
        for exception in ("OverflowError", "RuntimeError"):
            with self.subTest(exception=exception):
                proc = self.check_hidden(claim_variant(INVALID_TYPE, f"raise {exception}('crash')"))
                self.assert_rejected_by(proc, [INVALID_TEST])

    def test_rejected_claim_cannot_create_a_lease(self):
        for exception in ("ValueError", "TypeError"):
            with self.subTest(exception=exception):
                proc = self.check_hidden(claim_variant(
                    INVALID_TYPE, f"_original_claim(self, 0, 10); raise {exception}('too late')"))
                self.assert_rejected_by(proc, [INVALID_TEST])

    def test_invalid_ack_is_rejected_without_mutation(self):
        self.check_invalid_finish("ack")

    def test_invalid_nack_is_rejected_without_mutation(self):
        self.check_invalid_finish("nack")

    def check_invalid_finish(self, method):
        for action in ("return False", "_original_finish(self, id, token, 0); raise ValueError('too late')"):
            with self.subTest(method=method, action=action):
                extra = (f"\n_original_finish = LeaseQueue.{method}\n"
                         "def finish(self, id, token, now):\n"
                         "    if type(now) is not int or now < 0:\n"
                         f"        {action}\n"
                         "    return _original_finish(self, id, token, now)\n"
                         f"LeaseQueue.{method} = finish\n")
                self.assert_rejected_by(self.check_hidden(extra), [f"test_invalid_{method}_time_preserves_lease"])

    def test_invalid_integer_ranges_still_require_valueerror(self):
        proc = self.check_hidden(claim_variant(
            "type(now) is int and type(lease_seconds) is int and (now < 0 or lease_seconds <= 0)",
            "raise TypeError('wrong range exception')"))
        self.assert_rejected_by(proc, [INVALID_TEST])

    def test_payload_conflicts_still_require_valueerror(self):
        proc = self.check_hidden(
            "\n_original_enqueue = LeaseQueue.enqueue\n"
            "def enqueue(self, id, payload):\n"
            "    try:\n        return _original_enqueue(self, id, payload)\n"
            "    except ValueError:\n        raise TypeError('wrong conflict exception')\n"
            "LeaseQueue.enqueue = enqueue\n")
        self.assert_rejected_by(proc, ["test_deadline_boundary_fencing_and_retry_order"])

    def test_broken_controls_fail_only_their_targeted_invariants(self):
        boundary = "test_deadline_boundary_fencing_and_retry_order"
        large_fencing = "test_large_injected_times_and_deadlines_preserve_fencing"
        large_fifo = "test_large_leases_keep_fifo_among_eligible_jobs"
        expected = {"late-expiration": [boundary, large_fencing, large_fifo],
                    "unfenced-ack": [boundary, large_fencing],
                    "sqlite-integer-range": [large_fencing, large_fifo]}
        for name, failures in expected.items():
            with self.subTest(variant=name):
                self.assert_rejected_by(self.check_hidden(overlay=SCENARIO / "broken" / name), failures)


if __name__ == "__main__":
    unittest.main()
