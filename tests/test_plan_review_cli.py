"""The complete pending plan reaches a public approval client, without private-state reads (#3448)."""

import subprocess
import sys
import unittest

import autocode_plan_file as plan_file
import autocode_taskrun as taskrun

from . import test_taskrun as fixture


class PlanReviewCLITests(unittest.TestCase):
    setUp = fixture.TaskRunTests.setUp

    def test_public_pending_plan_shows_every_material_term_before_exact_approval(self):
        run = taskrun.TaskRun.start(
            self.workspace, fixture.BRIEF, options=fixture.FIXTURE_OPTIONS, env=self.env, timeout=90
        )
        shown = run.status()
        self.assertEqual("approve_plan", shown["needs"]["kind"], shown)
        self.assertFalse((run.workspace / "greet.py").exists(), "No Builder before plan approval")
        exported = self.workspace.parent / "reviewed-plan.md"
        result = subprocess.run(
            [
                sys.executable,
                str(fixture.HERE / "autocode.py"),
                "plan",
                "--export",
                str(exported),
                "--workspace",
                str(run.workspace),
                "--run-dir",
                str(run.run_dir),
                "--json",
            ],
            capture_output=True,
            text=True,
            env=self.env,
            timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        body = plan_file.read(exported.read_text()).body
        displayed = shown["displayed_plan"]
        self.assertEqual(shown["needs"]["token"], displayed["token"])
        review = displayed.get("review_markdown", "")
        for field in (
            "intended_outcome",
            "intended_user",
            "deliverables",
            "required_behaviors",
            "important_failure_cases",
            "technical_approach",
        ):
            values = body[field] if isinstance(body[field], list) else [body[field]]
            self.assertTrue(values, field)
            for value in values:
                with self.subTest(field=field, value=value):
                    self.assertIn(value, review, f"Public approval omitted {field}")
        for criterion in body["acceptance_criteria"]:
            self.assertIn(criterion["criterion"], review)
            self.assertIn(criterion["verification_method"], review)
        for decision in body["accepted_assumptions"] + body["delegated_decisions"]:
            self.assertIn(decision["text"], review)
        self.assertNotIn(displayed["token"], review)
        self.assertNotIn("front matter", review)
        with self.assertRaisesRegex(taskrun.TaskRunError, "approve plan exited"):
            run.approve_plan("stale-plan-token")
        self.assertEqual(displayed["token"], run.status()["needs"]["token"])
        self.assertFalse((run.workspace / "greet.py").exists())
