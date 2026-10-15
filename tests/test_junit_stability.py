"""A JUnit command repeats only when its whole report is proving cases."""

import unittest

import autocode_proof_stability as stability

C1 = "example.PageCountTest::test_c1_partial"
C2 = "example.PageCountTest::test_c2_exact"
OTHER = "example.OtherTest::test_unrelated"
CASES = [
    {"id": "C1", "test_name": "test_c1_partial", "kind": "restore"},
    {"id": "C2", "test_name": "test_c2_exact", "kind": "preserve"},
]
COMMAND = "mvn -o -Dtest=PageCountTest test"


def receipt(*, passed=(), failed=(), skipped=()):
    return {
        "exit_code": 1 if failed else 0,
        "timed_out": False,
        "results": {
            "complete": True,
            "total": len(passed) + len(failed) + len(skipped),
            "passed": list(passed),
            "failed": list(failed),
            "skipped": list(skipped),
        },
    }


class JUnitStabilityTests(unittest.TestCase):
    def repeat(self, *, candidate=None, base=None, command=COMMAND, flaky=False):
        candidate = candidate if candidate is not None else receipt(passed=[C1, C2])
        base = base if base is not None else receipt(passed=[C2], failed=[C1])
        calls = []

        def run(repeated_command, side, label):
            calls.append((repeated_command, side, label))
            if flaky and label == "stability-candidate-2":
                return receipt(passed=[C2], failed=[C1])
            return candidate if side == "candidate" else base

        unstable, record = stability.repeat("junit", command, CASES, candidate, base, run, base_tree=True)
        return unstable, record, calls

    def test_a_report_containing_exclusively_proving_cases_repeats_the_exact_trusted_command(self):
        unstable, record, calls = self.repeat()
        self.assertEqual([], unstable)
        self.assertIsNone(record["note"])
        self.assertEqual(
            [
                (COMMAND, "candidate", "stability-candidate-2"),
                (COMMAND, "base", "stability-base-2"),
                (COMMAND, "candidate", "stability-candidate-3"),
                (COMMAND, "base", "stability-base-3"),
            ],
            calls,
        )

    def test_unrelated_cases_on_either_side_prevent_a_whole_suite_repeat(self):
        for candidate, base in (
            (receipt(passed=[C1, C2, OTHER]), None),
            (receipt(passed=[C1, C2], skipped=[OTHER]), None),
            (None, receipt(passed=[C2, OTHER], failed=[C1])),
            (None, receipt(passed=[C2], failed=[C1], skipped=[OTHER])),
        ):
            with self.subTest(candidate=candidate, base=base):
                unstable, record, calls = self.repeat(candidate=candidate, base=base)
                self.assertEqual([], calls)
                self.assertEqual([], unstable)
                self.assertIn("cannot select the proving tests, so they ran once", record["note"])

    def test_shell_composition_cannot_authorize_a_repeat_from_an_exclusive_report(self):
        unstable, record, calls = self.repeat(command=COMMAND + " && echo finished")
        self.assertEqual([], calls)
        self.assertEqual([], unstable)
        self.assertIn("ran once", record["note"])

    def test_a_proving_case_that_changes_outcome_is_reported_unstable(self):
        unstable, record, calls = self.repeat(flaky=True)
        self.assertEqual(4, len(calls))
        self.assertEqual(
            [(C1, "candidate", ["pass", "fail", "pass"])], [(row["test"], row["side"], row["runs"]) for row in unstable]
        )
        self.assertEqual(unstable, record["unstable"])
        self.assertIn("Fix the unstable test", record["next_step"])


if __name__ == "__main__":
    unittest.main()
