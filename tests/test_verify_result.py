"""The failed-verification summary a Builder reads (autocode_verify_result.feedback)."""

import unittest

import autocode_verify as verify
import autocode_verify_result as verify_result


def _result(**overrides):
    result = {
        "verdict": "FAIL",
        "framework": {"note": "pytest from pyproject.toml"},
        "failures": ["the new test passes on base"],
        "unverified": ["suite timed out"],
        "notes": ["one skipped test"],
        "fail_to_pass": [f"tests/test_a.py::test_{n}" for n in range(25)],
        "commands": {
            "regression": "python -m pytest tests/test_a.py",
            "regression_source": "derived",
            "suite": "python -m pytest",
            "suite_source": "detected",
        },
        "checks": {
            "regression": {"exit_code": 1, "timed_out": False, "tail": "x" * 50 + "END"},
            "suite": {"exit_code": -9, "timed_out": True, "tail": "suite tail"},
        },
    }
    result.update(overrides)
    return result


class FeedbackCase(unittest.TestCase):
    def test_lists_verdict_reasons_commands_and_each_check_tail(self):
        text = verify_result.feedback(_result(), limit=10)
        lines = text.splitlines()
        self.assertEqual("Verdict: FAIL", lines[0])
        self.assertEqual("Note: pytest from pyproject.toml", lines[1])
        self.assertIn("- FAIL: the new test passes on base", lines)
        self.assertIn("- UNVERIFIED: suite timed out", lines)
        self.assertIn("- Note: one skipped test", lines)
        fail_to_pass = next(line for line in lines if line.startswith("Fail-to-pass tests: "))
        self.assertEqual(20, fail_to_pass.count("tests/test_a.py::"))
        self.assertIn("Regression command (derived): python -m pytest tests/test_a.py", lines)
        self.assertIn("Suite command (detected): python -m pytest", lines)
        self.assertIn("## regression: exit 1", lines)
        self.assertIn("## suite: exit -9 (timed out)", lines)
        self.assertIn("xxxxxxxEND", lines)
        self.assertNotIn("x" * 8 + "END", text)

    def test_leaves_out_what_the_result_does_not_carry(self):
        text = verify_result.feedback(_result(framework=None, failures=[], unverified=[], notes=[], fail_to_pass=None))
        self.assertNotIn("Note:", text)
        self.assertNotIn("Fail-to-pass", text)
        self.assertNotIn("- FAIL", text)

    def test_verify_keeps_its_public_name(self):
        self.assertIs(verify_result.feedback, verify.feedback)


if __name__ == "__main__":
    unittest.main()
