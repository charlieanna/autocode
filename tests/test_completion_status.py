"""Public completion status after a real CLI send-back transition.

The provider fixture is supplemental regression coverage, not live qualification.
"""

import json
import subprocess
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

    def summary_text(self, run):
        """`autocode status --summary` on the saved run, as a person would type it."""
        return subprocess.run(
            [*self.entry, "status", "--run-dir", str(run), "--workspace", str(self.project), "--summary"],
            cwd=self.project,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        ).stdout

    def test_success_clears_current_reason_but_retains_report_and_correction(self):
        run, view = self.run_case("once", 0)
        self.assertEqual("TASK_COMPLETE", view["status"])
        self.assertTrue(view["done"])
        self.assertIsNone(view["needs"])
        self.assertIsNone(view["stop_reason"])
        self.assertIn("recorded TASK_COMPLETE", view["explanation"]["what_happened"])
        self.assertNotIn("resume", view["explanation"]["explanation"].lower())
        self.assertNotIn("environment", view["explanation"]["explanation"].lower())
        self.assertEqual(
            {
                "state": "done",
                "label": "Done",
                "sentence": "Complete, and verified against the current checkout.",
                "next_command": None,
            },
            view["summary"],
        )
        self.assertEqual("Done: Complete, and verified against the current checkout.\n", self.summary_text(run))
        # A changed checkout is no longer the delivered work: the summary stops saying done (#2049).
        (self.project / "unverified.txt").write_text("changed after completion\n")
        self.assertTrue(self.summary_text(run).startswith("Stopped: Finished earlier, but its verification"))
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
        run, view = self.run_case("always", 2)
        self.assertEqual("PAUSED_COMPLETION_REVIEW", view["status"])
        self.assertFalse(view["done"])
        self.assertIn("All required criteria already pass", view["stop_reason"])
        self.assertIsNotNone(view["needs"])
        self.assertEqual("needs_you", view["summary"]["state"])
        self.assertEqual(view["next_command"], view["summary"]["next_command"])
        self.assertIsNotNone(view["summary"]["next_command"])
        self.assertEqual(
            [f"Needs you: {view['summary']['sentence']}", f"Next: {view['next_command']}"],
            self.summary_text(run).splitlines(),
        )


if __name__ == "__main__":
    unittest.main()
