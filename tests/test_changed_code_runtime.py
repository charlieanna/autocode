"""A broken test harness cannot establish behavioral mutation evidence."""

import tempfile
import unittest
from pathlib import Path

import autocode_mutation_check as mutation


class StrictMutationOutcomeTests(unittest.TestCase):
    def test_enforced_collection_does_not_sample_away_changed_branches(self):
        source = (
            "def value(n):\n"
            + "".join(f"    if n == {n}:\n        return {n}\n" for n in range(1, 10))
            + "    return -1\n"
        )
        names = [f"tests.test_branches.Branches.test_{n}" for n in range(1, 10)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "calc.py"
            target.write_text(source)

            def run(command, label, timeout):
                namespace = {}
                exec(compile(target.read_text(), "calc.py", "exec"), namespace)
                passed, failed = [], []
                for number, name in enumerate(names, 1):
                    (passed if namespace["value"](number) == number else failed).append(name)
                return {
                    **self.receipt(passed=passed, failed=failed, exit_code=bool(failed) * 1),
                    "output": str(root / label),
                }

            report = mutation.collect_selected_tests(
                "unittest",
                "python3 -m unittest -v tests.test_branches",
                names,
                root,
                root / "evidence",
                [("calc.py", "added")],
                lambda files: "diff --git a/calc.py b/calc.py\n+++ b/calc.py\n@@ -0,0 +1,20 @@\n",
                run,
                enforcement=True,
            )
            observed = {row["line"] for row in report["files"][0]["mutants"]}
            self.assertTrue(set(range(3, 20, 2)) <= observed, report)
            self.assertEqual("all_eligible_changed_sites", report["selection"])
            self.assertEqual(source, target.read_text())

    def receipt(self, *, passed=(), failed=(), skipped=(), setup=(), exit_code=0):
        return {
            "exit_code": exit_code,
            "timed_out": False,
            "results": {
                "complete": True,
                "total": len(passed) + len(failed) + len(skipped),
                "passed": list(passed),
                "failed": list(failed),
                "skipped": list(skipped),
                "uncollected": [],
                "setup_errors": list(setup),
            },
        }

    def test_named_failure_observes_mutation_but_nonzero_runner_exit_does_not(self):
        self.assertEqual(
            ("killed", ["test_lower"]),
            mutation.proved_outcome(
                self.receipt(passed=["test_upper"], failed=["test_lower"], exit_code=1),
                ["test_upper", "test_lower"],
            ),
        )
        self.assertEqual(
            ("not_run", []),
            mutation.proved_outcome(
                self.receipt(passed=["test_upper", "test_lower"], exit_code=1),
                ["test_upper", "test_lower"],
            ),
        )

    def test_setup_skip_and_missing_selected_test_never_kill_a_mutant(self):
        for receipt in (
            self.receipt(failed=["test_lower"], setup=["test_lower"], exit_code=1),
            self.receipt(skipped=["test_lower"], exit_code=0),
            self.receipt(passed=["different_test"], exit_code=0),
            {"exit_code": 1, "timed_out": False, "results": None},
        ):
            with self.subTest(receipt=receipt):
                self.assertEqual(("not_run", []), mutation.proved_outcome(receipt, ["test_lower"]))

    def test_only_complete_exact_passing_inventory_survives(self):
        self.assertEqual(
            ("survived", ["test_lower", "test_upper"]),
            mutation.proved_outcome(
                self.receipt(passed=["test_upper", "test_lower"]),
                ["test_upper", "test_lower"],
            ),
        )
