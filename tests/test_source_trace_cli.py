"""Spelling a numeric value in prose must not buy another planning call."""

import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
if str(SCENARIOS) not in sys.path:
    sys.path.insert(0, str(SCENARIOS))
from harness import catalog
from harness.driver import fake_setup
from harness.project import materialize, without_maintenance


class ZeroTraceCliTests(unittest.TestCase):
    def test_zero_prose_reaches_reviewed_plan_without_repair_and_reopens(self):
        with tempfile.TemporaryDirectory(prefix="zero-trace-cli-") as temporary:
            root = Path(temporary)
            scenario = catalog.load("greenfield-greeting-cli")
            project = without_maintenance(materialize(scenario.seed, root / "project"))
            flags, env = fake_setup(scenario, root, scenario.reference)
            env.update(AUTOCODE_HOME=str(root / "registry"), PYTHONDONTWRITEBYTECODE="1")
            paths = env["PATH"].split(os.pathsep)
            env["PATH"] = os.pathsep.join([paths[0], str(Path(sys.executable).parent), *paths[1:]])
            # Script only a model's prose choice, leaving its citations and the
            # actual request untouched. No task state or acceptance gate is edited.
            executable = root / "bin" / "codex"
            executable.rename(executable.with_name("ordinary_fake.py"))
            executable.write_text(
                f"#!{sys.executable}\n"
                + textwrap.dedent(r"""
                    import re, runpy
                    from pathlib import Path
                    fixture = runpy.run_path(str(Path(__file__).with_name('ordinary_fake.py')))
                    original = fixture['_report']
                    def report(value, output):
                        body = value.get('contract') or {}
                        def prose(text):
                            return re.sub(r'\b0\b', 'zero', text)
                        for key in ('required_behaviors', 'constraints', 'technical_approach'):
                            if key in body:
                                body[key] = [prose(text) for text in body[key]]
                        criteria = body.get('acceptance_criteria') or []
                        if isinstance(criteria, list):
                            for row in criteria:
                                for key in ('criterion', 'verification_method'):
                                    if key in row:
                                        row[key] = prose(row[key])
                        for row in value.get('requirement_trace') or []:
                            if row.get('disposition') == 'covered':
                                row['evidence'] = 'C1'
                        original(value, output)
                    fixture['main'].__globals__['_report'] = report
                    raise SystemExit(fixture['main']())
                    """).lstrip()
            )
            executable.chmod(0o755)
            self.assertIn("exits 0", scenario.brief)
            run = taskrun.TaskRun.start(
                project,
                scenario.brief,
                options=(*flags, "--no-adaptive-planning"),
                env=env,
                timeout=180,
                cwd=root,
            )
            view = run.advance_until_input()
            self.assertEqual("approve_plan", view["needs"]["kind"], view["stop_reason"])
            attempts = view["usage"]["accounting"]["attempts"]
            self.assertEqual(1, sum(row["stage"] == "astra_finalize" for row in attempts))
            self.assertFalse(any(row.get("report_only") for row in attempts), attempts)
            self.assertFalse(view["approvals"])
            draft = next(row for row in attempts if row["stage"] == "astra_discovery")
            plan = json.loads(Path(draft["output"]).read_text())["contract"]
            self.assertTrue(any("exits zero" in row for row in plan["required_behaviors"]))
            self.assertTrue(
                any("exits 0" in source["quote"] for row in plan["acceptance_criteria"] for source in row["sources"])
            )
            reopened = taskrun.TaskRun(project, run.run_dir, env=env, timeout=180, cwd=root).status()
            self.assertEqual(view["needs"], reopened["needs"])
            self.assertEqual(attempts, reopened["usage"]["accounting"]["attempts"])
