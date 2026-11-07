"""Run modes a client offers for a new run: the list, and what each mode's start flags do."""

import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_run_modes as run_modes
import autocode_subcommands as subcommands
import autocode_taskrun as taskrun

from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS, HERE


class RunModeListTests(unittest.TestCase):
    def listed(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, subcommands.dispatch(["modes", "list", "--json"]))
        return json.loads(output.getvalue())

    def test_list_names_each_mode_with_its_start_flags_and_the_default(self):
        listed = self.listed()
        self.assertEqual(1, listed["schema_version"])
        # Every chat starts with Requirements (owner decision 2026-10-22): "quick", which skipped it, is gone.
        self.assertEqual(["full"], [mode["id"] for mode in listed["modes"]])
        self.assertEqual("full", listed["default"])
        for mode in listed["modes"]:
            self.assertEqual({"id", "label", "description", "start_args", "spend_confirmation"}, set(mode))
            self.assertTrue(all(word.startswith("--") for word in mode["start_args"]), mode)
            self.assertNotIn("--no-spec-first", mode["start_args"], "no mode skips the Requirements conversation")
        self.assertEqual(["--joint-planning", "--spec-first"], listed["modes"][0]["start_args"])
        self.assertEqual(["planning"], [mode["spend_confirmation"] for mode in listed["modes"]])

    def test_a_listed_mode_cannot_be_changed_through_the_view(self):
        self.listed()["modes"][0]["start_args"].append("--auto-approve")
        self.assertNotIn("--auto-approve", self.listed()["modes"][0]["start_args"])


class RunModeCliTests(unittest.TestCase):
    """Each mode's start_args, passed as a client would, through the offline provider."""

    def setUp(self):
        root = Path(tempfile.mkdtemp(prefix="run-modes-"))
        self.addCleanup(shutil.rmtree, root)
        self.workspace = root / "project"
        self.workspace.mkdir()
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.workspace),
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@example.test",
                "commit",
                "-q",
                "--allow-empty",
                "-m",
                "base",
            ],
            check=True,
        )
        bindir = root / "bin"
        bindir.mkdir()
        shutil.copy2(HERE / "live_fixture_provider.py", bindir / "codex")
        (bindir / "codex").chmod(0o755)
        self.env = {
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "AUTOCODE_HOME": str(root / "registry"),
            "PYTHONDONTWRITEBYTECODE": "1",
            # The recognizer judges this brief clear; Requirements still talks with the person first.
            "LIVE_FIXTURE_CLARITY": "clear",
        }

    def start(self, mode_id):
        mode = next(mode for mode in run_modes.view()["modes"] if mode["id"] == mode_id)
        run = taskrun.TaskRun.start(
            self.workspace, BRIEF, options=(*FIXTURE_OPTIONS, *mode["start_args"]), env=self.env, timeout=300
        )
        return run, run.advance_until_input()

    @staticmethod
    def count(run, slug):
        return len(list(run.run_dir.glob(f"iterations/*/{slug}-*.jsonl")))

    def test_full_mode_confirms_requirements_with_the_person_before_planning(self):
        run, view = self.start("full")
        self.assertEqual("answer", view["needs"]["kind"], view.get("needs"))
        self.assertTrue(view["needs"]["questions"][-1]["id"].startswith("requirements-review-"))
        self.assertEqual(1, self.count(run, "requirements-gather"))
        self.assertEqual(0, self.count(run, "plan-challenge"), "Full mode plans only after you confirm requirements")

    def test_full_mode_plans_only_after_requirements_and_still_needs_approval_and_checks(self):
        run, view = self.start("full")
        question = view["needs"]["questions"][-1]["id"]
        self.assertTrue(question.startswith("requirements-review-"), view.get("needs"))
        run.answer(question, "plan")
        view = run.advance_until_input()
        self.assertEqual("approve_plan", view["needs"]["kind"], view.get("needs"))
        self.assertFalse(view["planning_depth"]["requirements_skipped"], view.get("planning_depth"))
        self.assertFalse((self.workspace / "greet.py").exists(), "Nothing is built before approval")
        run.approve_plan(view["needs"]["token"])
        done = run.advance_until_input()
        self.assertEqual("TASK_COMPLETE", done["status"])
        self.assertGreater(self.count(run, "validator"), 0, "The Tester still checks the work")
        self.assertGreater(
            self.count(run, "completion-review") + self.count(run, "completion-checkpoint"),
            0,
            "The Completion Reviewer still checks the work",
        )


if __name__ == "__main__":
    unittest.main()
