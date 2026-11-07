"""Public queued plan correction reuses Requirements and still requires independent review."""

import json
import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

from tests import ROOT
from tests.test_agent_step import fixture_workspace
from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS


@unittest.skipUnless(sys.platform == "linux", "supervised provider requires supported Linux")
class ReviewedPlanFeedbackCliTests(unittest.TestCase):
    def test_queued_exact_plan_feedback_revises_once_and_reopens_at_fresh_approval(self):
        workspace, env = fixture_workspace(self)
        provider = Path(env["PATH"].split(os.pathsep)[0]) / "codex"
        calls = provider.parent / "calls"
        marker = '    output = Path(sys.argv[sys.argv.index("-o") + 1])'
        amendment = textwrap.dedent(f"""
            with Path({str(calls)!r}).open('a') as stream:
                stream.write(stage + '\\n')
            feedback_rows = [row for row in data.get('requirement_trace_rows', [])
                             if row.get('source_quote') == 'Keep the same scope and standard-library-only constraint.']
            if stage == 'astra_discovery' and feedback_rows:
                report['requirements_rerun'] = ''
                report['requirement_trace'].extend(
                    _trace((row['requirement_id'], 'Python standard library only'))[0] for row in feedback_rows)
        """)
        self.assertIn(marker, provider.read_text())
        provider.write_text(provider.read_text().replace(marker, textwrap.indent(amendment, "    ") + "\n" + marker))
        run = taskrun.TaskRun.start(
            workspace, BRIEF, options=(*FIXTURE_OPTIONS, "--adaptive-planning"), env=env, timeout=240
        )
        original = run.advance_until_input()
        self.assertEqual("approve_plan", original["needs"]["kind"], original["needs"])
        stages_before = calls.read_text().splitlines()
        text = "Keep the same scope and standard-library-only constraint."
        command = [
            sys.executable,
            str(ROOT / "tools/autocode.py"),
            "intervention",
            "submit",
            "--workspace",
            str(workspace),
            "--run-dir",
            str(run.run_dir),
            "--request-id",
            "reviewed-plan-feedback",
            "--kind",
            "feedback",
            "--text",
            text,
            "--json",
        ]
        submitted = subprocess.run(command, env={**os.environ, **env}, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, submitted.returncode, submitted.stdout + submitted.stderr)
        self.assertEqual(original["needs"]["token"], json.loads(submitted.stdout)["receipt"]["observed_goal_token"])
        held = run.advance()
        self.assertEqual("PAUSED_INTERVENTION", held["status"])
        self.assertEqual("resume", held["needs"]["kind"])
        self.assertEqual("autocode", held["pause_category"])
        self.assertEqual("Feedback applied. Your work is saved; continue to update the plan.", held["chat"]["notice"])
        self.assertEqual("astra_discovery", held["next_stage"])
        self.assertEqual(stages_before, calls.read_text().splitlines(), "Consuming feedback launches no model")
        repeated = subprocess.run(command, env={**os.environ, **env}, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, repeated.returncode, repeated.stdout + repeated.stderr)
        self.assertTrue(json.loads(repeated.stdout)["idempotent"])
        resumed = run.resume_paused()
        reviewed = run.advance_until_input() if resumed["needs"]["kind"] == "continue" else resumed
        self.assertEqual(
            "approve_plan",
            reviewed["needs"]["kind"],
            (reviewed["status"], reviewed["needs"], reviewed.get("recoveries"), calls.read_text()),
        )
        stages = calls.read_text().splitlines()
        new = stages[len(stages_before) :]
        self.assertEqual(0, new.count("requirements_gather"), new)
        self.assertEqual(1, new.count("astra_discovery"), new)
        self.assertGreaterEqual(new.count("astra_challenge"), 1, "Independent review must still run")
        self.assertNotIn("terra", new)
        self.assertFalse((workspace / "greet.py").exists())
        self.assertNotEqual(original["needs"]["token"], reviewed["needs"]["token"])
        self.assertEqual(1, sum(row.get("text") == text for row in reviewed["request_sources"]))
        self.assertGreaterEqual(reviewed["cost"]["spent_usd"], original["cost"]["spent_usd"])
        reopened = taskrun.TaskRun(workspace, run.run_dir, env=env).status()
        self.assertEqual(reviewed["needs"], reopened["needs"])
        self.assertEqual(reviewed["cost"], reopened["cost"])
        self.assertEqual(stages, calls.read_text().splitlines(), "Reopen and receipt retry do not replay work")
