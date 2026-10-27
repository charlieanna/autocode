"""A report that narrows an unchanged quote refreshes the real chat handoff."""

import os
import sys
import textwrap
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

from .test_agent_step import fixture_workspace
from .test_taskrun import BRIEF, FIXTURE_OPTIONS


class RequirementRetentionCliTests(unittest.TestCase):
    def test_narrowed_quote_refreshes_understanding_without_losing_the_requirement(self):
        workspace, env = fixture_workspace(self)
        provider = Path(env["PATH"].split(os.pathsep)[0]) / "codex"
        marker = '    output = Path(sys.argv[sys.argv.index("-o") + 1])'
        source = provider.read_text()
        self.assertIn(marker, source)
        amendment = textwrap.dedent(f"""
            if stage == "requirements_gather":
                for row in report["requirements"]:
                    if row["id"] == "R3":
                        row["source_quote"] = (
                            "Python standard library only."
                            if data.get("previous_requirements_handoff") else {BRIEF!r}
                        )
        """)
        provider.write_text(source.replace(marker, textwrap.indent(amendment, "    ") + "\n" + marker))
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env["PATH"]
        run = taskrun.TaskRun.start(
            workspace,
            BRIEF,
            options=(*FIXTURE_OPTIONS, "--spec-first", "--no-adaptive-planning"),
            env=env,
            timeout=180,
        )
        first = run.advance_until_input()
        self.assertEqual("answer", first["needs"]["kind"], first["stop_reason"])
        self.assertTrue(first["needs"]["questions"][0]["id"].startswith("requirements-review-"))
        run.feedback("Keep the same scope and standard-library-only constraint.")
        refreshed = run.advance_until_input()
        self.assertEqual("answer", refreshed["needs"]["kind"], refreshed["stop_reason"])
        question = refreshed["needs"]["questions"][0]
        self.assertTrue(question["id"].startswith("requirements-review-"), question)
        self.assertIn("Python standard library only", question["question"])
        self.assertFalse(refreshed["done"])
        self.assertIsNone(refreshed.get("displayed_plan"), "An understanding refresh grants no plan approval")
