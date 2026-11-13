"""The proving tests are not waited for on the original code longer than their passing run explains.

"count_primes(2000000) is too slow, make it fast" (Claude models, 2026-11-13): the Builder's test called it
with no time limit. With the change the test took under a second; on the original code it ran until the
suite's own limit, thirty minutes, and the proof would have repeated that run twice for stability and once
more for the guards, to say "timed out on base". A fix for a loop that never ends meets the same wait.

The proof cases run real unittest commands in a Python project whose original code never returns; no
provider runs. tools/autocode_base_bound.py
"""

from __future__ import annotations

import unittest
from unittest import mock

import autocode_base_bound as base_bound
import autocode_regression as regression
import autocode_util as util
import autocode_verify as verify

from .test_verify import Project

FIXED = 'def drain(items):\n    """Empty ``items``; how many it took."""\n    taken = 0\n    while items:\n        items.pop()\n        taken += 1\n    return taken\n'
# Takes nothing out, so a list with an item in it is never empty.
HANGS = FIXED.replace("        items.pop()\n", "")
EXISTING = (
    "import unittest\n\nfrom poller import drain\n\n\nclass DrainTests(unittest.TestCase):\n"
    "    def test_nothing_to_drain(self):\n        self.assertEqual(0, drain([]))\n"
)
UNBOUNDED = EXISTING + "\n    def test_drains_every_item(self):\n        self.assertEqual(2, drain([1, 2]))\n"
# The same test with a limit of its own: on the original code it fails, and says so at once.
BOUNDED = (
    EXISTING.replace("import unittest\n", "import threading\nimport unittest\n")
    + "\n    def test_drains_every_item(self):\n        taken = []\n"
    "        worker = threading.Thread(target=lambda: taken.append(drain([1, 2])), daemon=True)\n"
    "        worker.start()\n        worker.join(0.5)\n        self.assertEqual([2], taken)\n"
)
SEED = {"poller.py": HANGS, "tests/__init__.py": "", "tests/test_poller.py": EXISTING}
STOPPED = "did not finish on the original code in 2 seconds, the longest the proof lets them run there"


def contract(*methods):
    rows = [{"id": f"AC{n}", "criterion": "drains", "verification_method": m} for n, m in enumerate(methods, 1)]
    sealed = {"task_id": "task-1", "revision": 1, "body": {"task_kind": "bugfix", "acceptance_criteria": rows}}
    return {**sealed, "hash": util.digest(sealed), "approval_status": "approved"}


class RuleTests(unittest.TestCase):
    def test_the_original_code_gets_thirty_times_the_passing_run_and_at_least_five_minutes(self):
        passed = {"exit_code": 0, "timed_out": False}
        for seconds, timeout, allowed in (
            (0.4, 1800, 300.0),
            (20.0, 1800, 600.0),
            (100.0, 1800, 1800),  # never past the suite's own limit
            (0.4, 120, 120),
            (0.4, None, 300.0),  # a run with no limit of its own still has this one
            (60.0, None, 1800.0),
        ):
            with self.subTest(seconds=seconds, timeout=timeout):
                self.assertEqual(allowed, base_bound.limit(passed, seconds, timeout))

    def test_a_run_that_did_not_pass_with_the_change_sets_no_bound(self):
        for receipt in ({"exit_code": 1, "timed_out": False}, {"exit_code": 0, "timed_out": True}, {}, None):
            with self.subTest(receipt=receipt):
                self.assertEqual(1800, base_bound.limit(receipt, 0.4, 1800))
                self.assertIsNone(base_bound.limit(receipt, 0.4, None))

    def test_only_a_run_this_bound_stopped_is_explained_by_it(self):
        stopped = {"exit_code": -9, "timed_out": True}
        said = base_bound.stopped(stopped, 0.42, 300.0, 1800)
        self.assertIn(
            "pass with the change in 0.4 seconds and did not finish on the original code in 300 seconds", said
        )
        self.assertIn("give the test its own time limit", said)
        self.assertIsNotNone(base_bound.stopped(stopped, 0.42, 300.0, None))
        for on_base, allowed, timeout in (
            ({"exit_code": 1, "timed_out": False}, 300.0, 1800),  # it finished
            (stopped, 1800, 1800),  # the suite's own limit ended it
            (stopped, None, None),
            (None, 300.0, 1800),
        ):
            with self.subTest(on_base=on_base, allowed=allowed):
                self.assertIsNone(base_bound.stopped(on_base, 0.42, allowed, timeout))

    def test_a_run_that_timed_out_on_the_original_code_stands_for_the_suite_there(self):
        stopped, failed = {"exit_code": -9, "timed_out": True}, {"exit_code": 1, "timed_out": False}
        targeted = "python -m unittest tests/test_poller.py"
        self.assertIs(stopped, base_bound.ran_with_tests({"regression_on_base": stopped}, targeted))
        self.assertIsNone(base_bound.ran_with_tests({"regression_on_base": failed}, targeted))
        # Without a targeted command that run was the whole suite.
        self.assertIs(failed, base_bound.ran_with_tests({"regression_on_base": failed}, None))
        self.assertIsNone(base_bound.ran_with_tests({}, None))

    def test_a_run_is_timed_by_a_clock_that_only_moves_forward(self):
        with mock.patch.object(base_bound.time, "monotonic", side_effect=[10.0, 12.5]):
            self.assertEqual(("receipt", 2.5), base_bound.timed(lambda: "receipt"))


class ProofTests(unittest.TestCase):
    def setUp(self):
        self.project = Project(SEED)
        self.addCleanup(self.project.close)
        # Two seconds on the original code, in place of five minutes; the suite's own limit is a minute.
        for name, value in (("FLOOR", 2.0), ("FACTOR", 1)):
            patch = mock.patch.object(base_bound, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def prove(self, test_file):
        self.project.write({"poller.py": FIXED, "tests/test_poller.py": test_file})
        state = {
            "workspace": str(self.project.root),
            "base_commit": self.project.base,
            "iteration": 1,
            "stages": [],
            "history": [],
            "settings": {"regression": {"test_timeout": 60}},
            "goal_contract": contract("test: test_drains_every_item", "guard: test_nothing_to_drain"),
        }
        with mock.patch.object(verify.diff_coverage, "collect", return_value=None):
            return regression.prove(state, self.project.root, self.project.root.parent / "run")

    def test_a_test_that_never_finishes_on_the_original_code_is_stopped_and_run_there_once(self):
        proof = self.prove(UNBOUNDED)
        self.assertEqual(verify.UNVERIFIED, proof["verdict"], proof)
        self.assertEqual([], proof["failures"])
        self.assertIn(STOPPED, " ".join(proof["unverified"]))
        self.assertIn("give the test its own time limit", " ".join(proof["unverified"]))
        self.assertEqual(1, sum("regression_on_base" in reason for reason in proof["unverified"]))
        checks = proof["checks"]
        self.assertEqual(
            (0, False), (checks["regression_on_candidate"]["exit_code"], checks["regression_on_candidate"]["timed_out"])
        )
        self.assertTrue(checks["regression_on_base"]["timed_out"])
        # Neither the stability repeats nor the guards' suite waits for the same test again.
        self.assertEqual([], sorted(label for label in checks if label.startswith("stability-")))
        self.assertNotIn("suite_on_base_with_tests", checks)
        self.assertIsNone(proof.get("proof_stability"))
        self.assertEqual(0, checks["suite_on_candidate"]["exit_code"])

    def test_a_test_with_its_own_time_limit_fails_on_the_original_code_and_proves_the_fix(self):
        proof = self.prove(BOUNDED)
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertFalse(proof["checks"]["regression_on_base"]["timed_out"])
        self.assertEqual(1, len(proof["fail_to_pass"]), proof["fail_to_pass"])
        self.assertIn("test_drains_every_item", proof["fail_to_pass"][0])
        self.assertIn("stability-base-2", proof["checks"])
        self.assertIn("suite_on_base_with_tests", proof["checks"])


if __name__ == "__main__":
    unittest.main()
