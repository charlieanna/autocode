"""Public acceptance handoff refuses self-suite execution, retaining real runner fixtures."""

import dataclasses
import hashlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_taskrun as taskrun

from tests import test_acceptance_tests as fixture
from tests.test_python_self_suite import SOURCE

WRITER = r"""
def acceptance_tests(data):
    report = _original_acceptance_tests(data)
    case = next(c for c in data['cases'] if c['id'] == 'RUNNER')
    path = Path('test_todo.py')
    path.write_text(CORRECTED_SOURCE if data.get('runner_feedback') and CORRECTED_SOURCE else RUNNER_SOURCE)
    report['cannot_write'] = [r for r in report['cannot_write'] if r['case_id'] != 'RUNNER']
    report['tests'].append({'case_id':'RUNNER', 'file':str(path), 'test_name':case['test_name']})
    return report
"""

DISTINCT = """import subprocess, sys, tempfile, unittest
from pathlib import Path
class Runner(unittest.TestCase):
    def test_runner_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'test_todo.py').write_text('import unittest\\nclass Child(unittest.TestCase):\\n def test_sum(self): self.assertEqual(4, 2+2)\\n')
            result = subprocess.run([sys.executable, '-m', 'unittest', 'test_todo', '-v'], cwd=directory, capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn('test_sum', result.stderr)
"""


@unittest.skipUnless(sys.platform == "linux", "Public supervised fake providers require Linux")
class PythonSelfSuiteCliTests(unittest.TestCase):
    setUp = fixture.AcceptanceTestsCliTests.setUp
    stage_order = staticmethod(fixture.AcceptanceTestsCliTests.stage_order)

    def record(self, run, view, marker=None):
        if destination := os.environ.get("BUILD_AUDIT_ARTIFACTS"):
            path = Path(destination) / self._testMethodName
            path.mkdir(parents=True, exist_ok=True)
            (path / "public-observation.json").write_text(
                json.dumps(
                    {
                        "status": view["status"],
                        "done": view["done"],
                        "next_stage": view["next_stage"],
                        "need": (view.get("needs") or {}).get("kind"),
                        "acceptance_tests": view.get("acceptance_tests"),
                        "stage_order": self.stage_order(run),
                        "import_observer_executed": marker.exists() if marker else None,
                        "archived_fixture_sha256": hashlib.sha256(SOURCE.encode()).hexdigest(),
                    },
                    indent=2,
                )
                + "\n"
            )

    def run_author(self, source, method, corrected_source=None):
        self.scenario = dataclasses.replace(self.scenario, fake_criteria={**fixture.CASES, "RUNNER": method})
        original = fixture.fake_setup

        def setup(*args):
            flags, environment = original(*args)
            provider = self.root / "bin/codex"
            text = provider.read_text().replace("def acceptance_tests(data:", "def _original_acceptance_tests(data:")
            provider.write_text(
                text.replace(
                    "def edit_acceptance_tests_once()",
                    "RUNNER_SOURCE = "
                    + repr(source)
                    + "\nCORRECTED_SOURCE = "
                    + repr(corrected_source)
                    + "\n"
                    + WRITER
                    + "\ndef edit_acceptance_tests_once()",
                )
            )
            return flags, environment

        project = fixture.without_maintenance(fixture.materialize(self.scenario.seed, self.root / "project"))
        with patch.object(fixture, "fake_setup", setup):
            flags, environment = fixture.fake_setup(self.scenario, self.root, self.scenario.dir / "reference")
        environment.update(AUTOCODE_HOME=str(self.root / "registry"), PYTHONDONTWRITEBYTECODE="1")
        environment["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), environment["PATH"]])
        run = taskrun.TaskRun.start(
            project,
            self.scenario.brief,
            options=(*flags, "--joint-planning", "--no-adaptive-planning", "--spec-first"),
            env=environment,
            timeout=240,
            cwd=self.root,
        )
        for number in range(12):
            view = run.advance_until_input()
            need = view.get("needs") or {}
            if need.get("kind") == "approve_plan":
                # Read the public export before approving its exact token under fixture delegation.
                path = self.root / f"reviewed-plan-{number}.md"
                run.export_plan(path)
                self.assertIn(method.split(": ", 1)[1], path.read_text())
                run.approve_plan(need["token"])
            elif (
                need.get("kind") == "answer"
                and "acceptance-tests" not in self.stage_order(run)
                and need["questions"][0]["id"].startswith("requirements-")
            ):
                question = need["questions"][0]
                run.answer(
                    question["id"], question.get("proposed_default") or "plan", resolver_token=need["resolver_token"]
                )
            else:
                return run, project, view
        self.fail("The fixture did not reach its bounded acceptance stop")

    def test_archived_self_suite_never_executes_or_reaches_builder(self):
        marker = self.root / "acceptance-executed"
        # The archived source bytes stay verbatim after this fixture-only import observer.
        source = "from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\n" + SOURCE
        run, _, view = self.run_author(source, "test: test_ac8_regression_suite_runs")
        self.record(run, view, marker)
        self.assertFalse(marker.exists(), "Core must refuse the actual guarded self-suite before importing it")
        self.assertNotIn("builder", self.stage_order(run))
        self.assertFalse(view["done"])
        self.assertNotEqual("answer", (view.get("needs") or {}).get("kind"), json.dumps(view))
        self.assertIn("launches its own Python test suite", json.dumps(view))
        self.assertFalse((view.get("acceptance_tests") or {}).get("results"))

    def test_distinct_same_basename_runner_fixture_is_qualified_before_builder(self):
        run, _, view = self.run_author(DISTINCT, "guard: test_runner_fixture")
        self.record(run, view)
        self.assertEqual("passes", view["acceptance_tests"]["results"]["RUNNER"], view)
        order = self.stage_order(run)
        self.assertIn("builder", order, order)
        self.assertLess(order.index("acceptance-tests"), order.index("builder"))
        # Later stock reviewers do not cover this synthetic extra criterion; admission is the assertion.

    def test_bounded_rewrite_keeps_the_exact_named_guard_then_qualifies_distinct_fixture(self):
        marker = self.root / "rejected-author-executed"
        broken = (
            "import os, subprocess, sys, unittest\nfrom pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\n"
            "class Runner(unittest.TestCase):\n def test_runner_fixture(self):\n"
            "  if os.environ.get('INNER'): return\n"
            "  subprocess.run([sys.executable,'-m','unittest','test_todo'], env={**os.environ,'INNER':'1'})\n"
        )
        run, _, view = self.run_author(broken, "guard: test_runner_fixture", corrected_source=DISTINCT)
        self.record(run, view, marker)
        self.assertFalse(marker.exists())
        self.assertEqual("passes", view["acceptance_tests"]["results"]["RUNNER"], view)
        order = self.stage_order(run)
        self.assertEqual(2, order.count("acceptance-tests"), order)
        self.assertLess(order.index("acceptance-tests"), order.index("builder"))


if __name__ == "__main__":
    unittest.main()
