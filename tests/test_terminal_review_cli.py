"""Terminal human review against a real saved run and the explicit fake provider."""

import json
import subprocess
import unittest

from autocode_taskrun import TaskRun, TaskRunError

from . import test_subprocess


class TerminalReviewCLITests(unittest.TestCase):
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ("--engine", "codex")

    def setUp(self):
        test_subprocess.SubprocessFlow.setUp(self)
        self.env["AUTOCODE_FIXTURE_MODE"] = "human-pending"
        self.probe = self.root / "provider-launches.jsonl"
        self.env["AUTOCODE_REGISTRY_LAUNCH_PROBE"] = str(self.probe)
        self.launch(["Build greeting", "--chat", "--max-iterations", "2"], 2, answers="CLI\nyes\n")
        self.run_dir, _ = self.saved()
        self.run = TaskRun(
            self.project,
            self.run_dir,
            command=tuple(self.entry),
            env=self.env,
            cwd=self.root,
            timeout=60,
        )
        self.before = self.run.status()
        self.assertEqual("review", self.before["needs"]["kind"], self.before.get("stop_reason"))
        self.assertEqual(["C1"], self.before["needs"]["criteria"])

    def review(self, *args, answers="", expected=0):
        result = subprocess.run(
            [*self.entry, "review", "--workspace", str(self.project), "--run-dir", str(self.run_dir), *args],
            cwd=self.root,
            env=self.env,
            input=answers,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
        return result

    def receipt(self):
        return (self.run_dir / "state.json").read_bytes(), self.probe.read_bytes()

    def test_inspection_skip_and_end_of_input_leave_the_review_unchanged(self):
        before = self.receipt()
        data = json.loads(self.review("--json").stdout)
        self.assertEqual(self.before["needs"]["token"], data["token"])
        [criterion] = [row for row in data["criteria"] if row["pending"]]
        self.assertEqual("C1", criterion["id"])
        self.assertEqual("Contract holds", criterion["text"])
        self.assertIn("greeting", criterion["verification_method"])
        self.assertTrue(criterion["evidence"] or criterion["evidence_refs"])
        self.assertTrue(criterion["checks"])
        shown = self.review("--show").stdout
        self.assertIn("C1", shown)
        self.assertIn("Contract holds", shown)
        self.assertNotIn("Approve, reject, or skip?", shown)
        self.assertEqual(before, self.receipt())
        for answers in ("skip\n", "\n", "", "reject\n", "reject\n\n"):
            with self.subTest(answers=answers):
                self.review(answers=answers)
                self.assertEqual(before, self.receipt())

    def test_approval_is_saved_without_starting_work_and_explicit_resume_completes(self):
        launches = self.probe.read_bytes()
        result = self.review(answers="approve\n")
        self.assertIn("Approved C1", result.stdout)
        after = self.run.status()
        [criterion] = [row for row in after["evidence"]["acceptance"] if row["id"] == "C1"]
        self.assertTrue(criterion["human_reviewed"])
        self.assertFalse(after["done"])
        self.assertEqual(launches, self.probe.read_bytes())
        self.launch(["--run-dir", str(self.run_dir), "--no-chat"], 0)
        self.assertTrue(self.run.status()["done"])

    def test_rejection_saves_the_note_at_the_existing_planning_boundary(self):
        launches = self.probe.read_bytes()
        note = "The greeting must identify the requested customer, not a generic user."
        result = self.review(answers="reject\n" + note + "\n")
        self.assertIn("Rejected C1", result.stdout)
        after = self.run.status()
        self.assertFalse(after["done"])
        self.assertNotEqual("review", (after["needs"] or {}).get("kind"))
        self.assertEqual("DISCOVERING", after["phase"])
        self.assertEqual("astra_discovery", after["next_stage"])
        self.assertEqual(launches, self.probe.read_bytes())
        displayed = self.launch(["--run-dir", str(self.run_dir), "--show-goal"], 0).stdout
        self.assertIn(note, displayed)
        self.assertEqual(launches, self.probe.read_bytes())

    def test_stale_tokens_and_changed_source_cannot_authorize_a_decision(self):
        before = self.receipt()
        stale = self.before["needs"]["token"] + "-stale"
        with self.assertRaises(TaskRunError):
            self.run.approve_review("C1", stale)
        self.assertEqual(before, self.receipt())
        with self.assertRaises(TaskRunError):
            self.run.reject_review("C1", stale, "The greeting needs revision.")
        self.assertEqual(before, self.receipt())
        source = self.project / "greet.py"
        source.write_text(source.read_text() + "\n# Changed after the artifact was offered for review.\n")
        for answers in ("approve\n", "reject\nThe greeting needs revision.\n"):
            with self.subTest(answers=answers):
                result = self.review(answers=answers, expected=2)
                self.assertTrue(result.stderr.strip(), result.stdout)
                self.assertEqual(before, self.receipt())
