"""Public completion status after a real CLI send-back transition.

The provider fixture is supplemental regression coverage, not live qualification.
"""

import json
import unittest

from autocode_taskrun import TaskRun

from . import test_subprocess


class CompletionStatusCLITests(unittest.TestCase):
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ("--engine", "codex")

    def setUp(self):
        test_subprocess.SubprocessFlow.setUp(self)
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"

    def run_case(self, repeats, expected):
        self.env["AUTOCODE_FIXTURE_REDUNDANT_COMPLETION"] = repeats
        self.launch(["Build a greeting tool", "--chat"], expected, answers="CLI\nyes\n")
        run, _ = self.saved()
        public = TaskRun(self.project, run, command=self.entry, env=self.env, timeout=60)
        return run, public.status()

    def test_success_clears_current_reason_but_retains_report_and_correction(self):
        run, view = self.run_case("once", 0)
        self.assertEqual("TASK_COMPLETE", view["status"])
        self.assertTrue(view["done"])
        self.assertIsNone(view["needs"])
        self.assertIsNone(view["stop_reason"])
        reports = [
            json.loads(p.read_text())
            for p in run.glob("iterations/*/completion-review-*.json")
            if not any(p.name.endswith(s) for s in (".before.json", ".after.json", ".tools.json"))
        ]
        self.assertTrue(any(r.get("status") == "CONTINUE" for r in reports))
        self.assertTrue(any(r.get("status") in ("COMPLETE", "TASK_COMPLETE") for r in reports))
        self.assertTrue(
            any(
                "You returned CONTINUE" in p.read_text() for p in run.glob("iterations/*/completion-review-*.prompt.md")
            )
        )

    def test_repeated_continue_retains_actionable_pause_reason(self):
        _, view = self.run_case("always", 2)
        self.assertEqual("PAUSED_COMPLETION_REVIEW", view["status"])
        self.assertFalse(view["done"])
        self.assertIn("All required criteria already pass", view["stop_reason"])
        self.assertIsNotNone(view["needs"])


if __name__ == "__main__":
    unittest.main()
