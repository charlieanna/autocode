"""An adaptive approved-plan correction gets its review round without an Investigator."""

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
class CorrectiveReviewCliTests(unittest.TestCase):
    def test_small_adaptive_correction_reviews_an_accepted_repaired_draft_without_investigation(self):
        workspace, env = fixture_workspace(self)
        (workspace / "README.md").write_text(
            "Greeting CLI: preserve the standard-library scope and check both CLI outcomes.\n"
        )
        subprocess.run(["git", "-C", str(workspace), "add", "README.md"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(workspace),
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@example.test",
                "commit",
                "-qm",
                "Document fixture scope",
            ],
            check=True,
        )
        provider = Path(env["PATH"].split(os.pathsep)[0]) / "codex"
        calls, rejected = provider.parent / "calls", provider.parent / "citation-rejected"
        message = "Keep the same scope and standard-library-only constraint."
        amendment = textwrap.dedent(f"""
            with Path({str(calls)!r}).open('a') as stream:
                stream.write(stage + ('_report_repair' if repairing else '') + '\\n')
            if stage == 'astra_challenge':
                report['concerns'] = [{{'id': 'preserve-scope', 'concern': 'State the exact CLI regression checks',
                    'evidence_refs': ['README.md'], 'requested_change': 'Name both CLI outcomes without broadening scope',
                    'acceptance_test': 'Run the success and empty-name checks', 'blocking': True}}]
            if stage == 'glm_revise':
                report['responses'] = [{{'concern_id': 'preserve-scope', 'response': 'Keep both original CLI outcomes',
                    'evidence_refs': ['README.md'], 'change': 'Keep the regression plan explicit',
                    'acceptance_test': 'Run the success and empty-name checks'}}]
            if stage == 'astra_finalize':
                report['decisions'] = [{{'concern_id': 'preserve-scope', 'decision': 'The plan retains the original checks',
                    'rationale': 'Both outcomes and the standard-library boundary remain',
                    'acceptance_test': 'Run the success and empty-name checks', 'resolved': True}}]
            feedback_rows = [row for row in data.get('requirement_trace_rows', []) if row.get('source_quote') == {message!r}]
            if stage in ('astra_discovery', 'glm_revise') and feedback_rows:
                report['contract']['technical_approach'].append('Explicitly retain both CLI regression outcomes in the corrected implementation')
                traced = {{row['requirement_id'] for row in report['requirement_trace']}}
                report['requirement_trace'].extend(_trace((row['requirement_id'], 'Python standard library only'))[0]
                                                  for row in feedback_rows if row['requirement_id'] not in traced)
                if stage == 'astra_discovery':
                    if not repairing and not Path({str(rejected)!r}).exists():
                        Path({str(rejected)!r}).write_text('original citation rejected')
                        report['code_refs'] = ['missing-source-file.py']
            if repairing and stage == 'astra_discovery':
                report = json.loads(Path(data['original']['output']).read_text())
                report['code_refs'] = ['README.md']
        """)
        marker = '    output = Path(sys.argv[sys.argv.index("-o") + 1])'
        self.assertIn(marker, provider.read_text())
        provider.write_text(provider.read_text().replace(marker, textwrap.indent(amendment, "    ") + "\n" + marker))
        env = {**env, "LIVE_FIXTURE_CLARITY": "clear"}
        run = taskrun.TaskRun.start(
            workspace,
            BRIEF,
            options=(*FIXTURE_OPTIONS, "--adaptive-planning", "--plan-reviewer-reasoning-effort", "high"),
            env=env,
            timeout=300,
        )
        original = run.advance_until_input()
        self.assertEqual("approve_plan", original["needs"]["kind"], original["needs"])
        self.assertEqual(1, calls.read_text().splitlines().count("astra_challenge"))
        self.assertEqual(1, calls.read_text().splitlines().count("astra_finalize"))
        token = original["needs"]["token"]
        run.approve_plan(token)
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
            "correct-approved-plan",
            "--kind",
            "feedback",
            "--text",
            message,
            "--json",
        ]
        submitted = subprocess.run(command, env={**os.environ, **env}, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, submitted.returncode, submitted.stdout + submitted.stderr)
        self.assertEqual(token, json.loads(submitted.stdout)["receipt"]["observed_goal_token"])
        held = run.advance()
        self.assertEqual("PAUSED_INTERVENTION", held["status"])
        resumed = run.resume_paused()
        revised = run.advance_until_input() if resumed["needs"]["kind"] == "continue" else resumed
        stages = calls.read_text().splitlines()
        self.assertEqual(
            "approve_plan",
            revised["needs"]["kind"],
            (revised["status"], revised["needs"], revised.get("recoveries"), stages),
        )
        self.assertEqual(2, stages.count("astra_challenge"), stages)
        self.assertEqual(2, stages.count("astra_finalize"), stages)
        self.assertEqual(1, stages.count("astra_discovery_report_repair"), stages)
        self.assertNotIn("investigate_stuck", stages)
        self.assertNotIn("terra", stages)
        self.assertEqual(4, revised["planning_depth"]["review_limit"])
        self.assertNotEqual(token, revised["needs"]["token"])
        self.assertFalse((workspace / "greet.py").exists())
        with self.assertRaises(taskrun.TaskRunError):
            run.approve_plan(token)
