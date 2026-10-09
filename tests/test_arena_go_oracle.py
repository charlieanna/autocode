"""Go oracle requires actual named test execution before accepting a score."""
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / "arena/cases/go-http2-hung-reset-health/oracle.py"
spec = importlib.util.spec_from_file_location("arena_go_oracle", path)
oracle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oracle)


class NamedOutcomeTests(unittest.TestCase):
    def result(self, output, exit_code=0, timed_out=False):
        return {"output": output, "returncode": exit_code, "timeout": timed_out}

    def test_actual_named_pass_and_failure_are_scored(self):
        for outcome, code in (("PASS", 0), ("FAIL", 1)):
            with self.subTest(outcome=outcome):
                text = f"=== RUN   TestHealth\n--- {outcome}: TestHealth (0.01s)\n{outcome}\n"
                self.assertEqual((outcome, None), oracle.named_test_outcome("TestHealth", self.result(text, code)))

    def test_exit_zero_without_requested_execution_is_an_error(self):
        for text in ("", "PASS\n", "=== RUN   TestOther\n--- PASS: TestOther (0.00s)\nPASS\n",
                     "--- PASS: TestHealth (0.00s)\n=== RUN   TestHealth\n"):
            with self.subTest(output=text):
                outcome, error = oracle.named_test_outcome("TestHealth", self.result(text))
                self.assertIsNone(outcome)
                self.assertIsNotNone(error)

    def test_skips_duplicates_exit_disagreement_and_timeouts_are_errors(self):
        start = "=== RUN   TestHealth\n"
        end = "--- PASS: TestHealth (0.00s)\n"
        results = (self.result(start + "--- SKIP: TestHealth (0.00s)\n"),
                   self.result(start + start + end), self.result(start + end + end),
                   self.result(start + end, 1),
                   self.result(start + "--- FAIL: TestHealth (0.00s)\n", 0),
                   self.result(start + end, 0, True))
        for result in results:
            with self.subTest(result=result):
                outcome, error = oracle.named_test_outcome("TestHealth", result)
                self.assertIsNone(outcome)
                self.assertIsNotNone(error)
