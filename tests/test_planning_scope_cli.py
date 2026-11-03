"""Late Planner scope is observable through the public CLI before review or building."""

import os
import sys
import textwrap
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

from .test_agent_step import fixture_workspace
from .test_taskrun import BRIEF, FIXTURE_OPTIONS


@unittest.skipUnless(sys.platform == "linux", "fake providers require supported Linux")
class PlanningScopeCliTests(unittest.TestCase):
    def start(self, *, grounded):
        workspace, env = fixture_workspace(self)
        provider = Path(env["PATH"].split(os.pathsep)[0]) / "codex"
        env["PATH"] = f"{provider.parent}{os.pathsep}{Path(sys.executable).parent}{os.pathsep}{env['PATH']}"
        elsewhere = "Also record greetings in greet-history."
        quote = elsewhere if grounded else "Charge credit cards in billing."
        marker = '    output = Path(sys.argv[sys.argv.index("-o") + 1])'
        addition = textwrap.dedent(f"""
            if stage == 'astra_discovery':
                report['workspace_scope'] = {{'spans_repositories': True, 'external_parts': [{{
                    'target': 'greet-history', 'work': 'Record greetings',
                    'source_quote': {quote!r}, 'evidence': ''}}]}}
        """)
        source = provider.read_text()
        self.assertEqual(1, source.count(marker))
        provider.write_text(source.replace(marker, textwrap.indent(addition, "    ") + "\n" + marker))
        run = taskrun.TaskRun.start(
            workspace,
            f"{BRIEF} {elsewhere}",
            options=(*FIXTURE_OPTIONS, "--joint-planning", "--no-adaptive-planning"),
            env=env,
            timeout=180,
        )
        return run, env, run.advance_until_input()

    def test_planner_discovery_reopens_at_scope_before_review_and_preserves_approval(self):
        run, env, view = self.start(grounded=True)
        self.assertEqual("answer", view["needs"]["kind"], view["needs"])
        [question] = view["needs"]["questions"]
        self.assertTrue(question["id"].startswith("workspace-scope-"))
        self.assertEqual(["greet-history"], [part["target"] for part in view["scope"]["external_parts"]])
        attempts = view["usage"]["accounting"]["attempts"]
        self.assertEqual(["requirements_gather", "astra_discovery"], [row["stage"] for row in attempts])
        self.assertFalse(any(row["report_only"] for row in attempts))
        reopened = taskrun.TaskRun(run.workspace, run.run_dir, env=env).status()
        self.assertEqual(view["needs"], reopened["needs"])
        self.assertEqual(attempts, reopened["usage"]["accounting"]["attempts"])
        self.assertFalse((run.workspace / "greet.py").exists())

        run.answer(question["id"], "This repository only", resolver_token=view["needs"]["resolver_token"])
        planned = run.advance_until_input()
        self.assertEqual("approve_plan", planned["needs"]["kind"], planned["needs"])
        self.assertEqual("this_repository", planned["scope"]["decision"])
        self.assertIn(
            "Not covered: changes in greet-history: Record greetings", planned["displayed_plan"]["scope_exclusions"]
        )
        self.assertNotIn("terra", [row["stage"] for row in planned["usage"]["accounting"]["attempts"]])

    def test_ungrounded_planner_discovery_does_not_expand_scope(self):
        run, _, view = self.start(grounded=False)
        self.assertEqual("approve_plan", view["needs"]["kind"], view["needs"])
        self.assertEqual([], view["scope"]["external_parts"])
        self.assertEqual(["greet-history"], view["scope"]["dropped"])
        self.assertFalse(any(row["report_only"] for row in view["usage"]["accounting"]["attempts"]))
        self.assertFalse((run.workspace / "greet.py").exists())
