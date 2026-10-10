"""A test-only diff on a build contract proves coverage of existing behavior (#510).

The proof goes through the public production entry point (regression.prove) with real
unittest subprocesses; no model shim. A bugfix contract still has to change product code.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

import autocode_regression as regression
import autocode_verify as verify

from tests.test_verify import Project

CALC = "def add(a, b):\n    return a + b\n\n\ndef keep():\n    return 9\n"
EXISTING_TESTS = (
    "import unittest\nfrom calc import add\n\n\nclass AddTests(unittest.TestCase):\n"
    "    def test_add(self):\n        self.assertEqual(5, add(2, 3))\n"
)


def build_state(project, criterion):
    return {
        "base_commit": project.base,
        "settings": {},
        "iteration": 1,
        "stages": [],
        "history": [],
        "goal_contract": {
            "body": {"task_kind": "build", "acceptance_criteria": [criterion], "milestones": [{"id": "M1"}]}
        },
    }


class CoverageBuildProofTests(unittest.TestCase):
    def setUp(self):
        self.project = Project({"calc.py": CALC, "test_calc.py": EXISTING_TESTS})
        self.addCleanup(self.project.close)
        self.run_dir = Path(tempfile.mkdtemp(prefix="coverage-build-"))
        self.addCleanup(shutil.rmtree, self.run_dir, True)

    def coverage_test(self, expected, assertion):
        return (
            "import unittest\nfrom calc import keep\n\n\nclass KeepTests(unittest.TestCase):\n"
            "    def test_keep_is_nine(self):\n"
            f"        self.assertEqual({expected}, keep()){assertion}\n"
        )

    def test_a_test_only_diff_proves_coverage_when_the_tests_pass_on_the_original_code(self):
        criterion = {
            "id": "C1",
            "criterion": "keep() returns 9",
            "verification_method": "test: test_keep_is_nine — characterizes existing behavior",
        }
        self.project.write({"test_keep.py": self.coverage_test(9, "")})
        proof = regression.prove(build_state(self.project, criterion), self.project.root, self.run_dir)
        self.assertEqual(verify.PASS, proof["verdict"], str(proof["failures"]) + str(proof["unverified"]))
        self.assertEqual([], proof["fail_to_pass"], proof)
        self.assertIn("test_keep.KeepTests.test_keep_is_nine", proof["pass_to_pass"], proof)
        self.assertEqual({"C1": ["test_keep.KeepTests.test_keep_is_nine"]}, proof["case_tests"], proof)
        self.assertNotIn("Only test files changed", " ".join(proof["failures"]), proof["failures"])

    def test_a_lazy_feature_build_cannot_hide_behind_tests_that_fail_on_the_original_code(self):
        criterion = {
            "id": "C1",
            "criterion": "keep() returns 10",
            "verification_method": "test: test_keep_is_nine — the behavior this build must add",
        }
        self.project.write({"test_keep.py": self.coverage_test(10, "")})
        proof = regression.prove(build_state(self.project, criterion), self.project.root, self.run_dir)
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertTrue(
            any("regression tests fail on the candidate" in failure for failure in proof["failures"]), proof["failures"]
        )

    def test_a_bugfix_contract_still_requires_product_code_for_a_test_only_diff(self):
        criterion = {
            "id": "C1",
            "criterion": "keep() returns 9",
            "verification_method": "test: test_keep_is_nine — characterizes existing behavior",
        }
        self.project.write({"test_keep.py": self.coverage_test(9, "")})
        state = build_state(self.project, criterion)
        state["goal_contract"]["body"]["task_kind"] = "bugfix"
        proof = regression.prove(state, self.project.root, self.run_dir)
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertTrue(any("Only test files changed" in failure for failure in proof["failures"]), proof["failures"])


if __name__ == "__main__":
    unittest.main()
