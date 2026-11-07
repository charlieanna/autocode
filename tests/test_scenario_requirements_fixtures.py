"""The catalog fake leaves missing reporter evidence at the Requirements boundary."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_taskrun as taskrun

from scenarios.harness import catalog
from scenarios.harness.driver import Driver, fake_setup, questions_answerable
from scenarios.harness.project import git, materialize, without_maintenance

ROOT = Path(__file__).resolve().parents[1]


class ScenarioRequirementsFixtureTests(unittest.TestCase):
    def report(self, scenario_id):
        scenario = catalog.load(scenario_id)
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "title": scenario.title,
                        "brief": scenario.brief,
                        "reference": str(scenario.reference),
                        "check": scenario.fake_check,
                        "paths": [],
                    }
                )
            )
            spec = importlib.util.spec_from_file_location(
                "requirements_fixture", ROOT / "scenarios/harness/fake_codex.py"
            )
            provider = importlib.util.module_from_spec(spec)
            with patch.dict(os.environ, {"SCENARIO_FAKE_CONFIG": str(config)}):
                spec.loader.exec_module(provider)
            provider.OUTPUT_SCHEMA = {"properties": {"job": {}, "clarity": {}, "workspace_scope": {}}}
            with patch.object(provider, "source_refs", return_value=["task"]):
                return scenario, provider.report_for("requirements_gather", {})

    def test_missing_reproduction_facts_are_questions_without_build_behaviors_or_tests(self):
        scenario, report = self.report("bugfix-not-reproducible")
        note = json.loads((scenario.reference / "docs/bugs/none-cells.json").read_text())
        self.assertEqual(note["finding"], report["summary"])
        self.assertEqual([], report["required_behaviors"])
        self.assertEqual([], report["acceptance_tests"])
        self.assertEqual(note["questions"], [row["question"] for row in report["open_questions"]])
        self.assertFalse(questions_answerable(report["open_questions"], {}))
        for question in report["open_questions"]:
            self.assertEqual("requested_outcome", question["category"])
            self.assertFalse(question["delegable"])
        self.assertEqual("bugfix", report["job"])
        self.assertTrue(report["requirements"], "the original request must remain traceable")

    def test_a_normal_bug_fixture_keeps_its_build_requirements(self):
        scenario, report = self.report("bugfix-trivial")
        self.assertEqual([], report["open_questions"])
        self.assertTrue(report["required_behaviors"])
        self.assertEqual([scenario.fake_check], report["acceptance_tests"])
        self.assertEqual("bugfix", report["job"])


@unittest.skipUnless(sys.platform == "linux", "supervised fake provider requires supported Linux")
class ScenarioRequirementsFixtureCliTests(unittest.TestCase):
    def test_catalog_provider_stops_unanswered_without_a_plan_or_workspace_change(self):
        with tempfile.TemporaryDirectory(prefix="scenario-requirements-cli-") as folder:
            root = Path(folder)
            scenario = catalog.load("bugfix-not-reproducible")
            project = without_maintenance(materialize(scenario.seed, root / "project"))
            flags, environment = fake_setup(scenario, root, scenario.reference)
            environment.update(AUTOCODE_HOME=str(root / "registry"), PYTHONDONTWRITEBYTECODE="1")
            paths = environment["PATH"].split(os.pathsep)
            environment["PATH"] = os.pathsep.join([paths[0], str(Path(sys.executable).parent), *paths[1:]])
            before = git(project, "ls-files", "-s")
            run = taskrun.TaskRun.start(project, scenario.brief, options=flags, env=environment, timeout=120, cwd=root)
            view = run.advance_until_input()
            self.assertEqual("WAITING_FOR_USER", view["status"], view)
            self.assertFalse(view["done"], view)
            self.assertEqual("answer", view["needs"]["kind"], view)
            note = json.loads((scenario.reference / "docs/bugs/none-cells.json").read_text())
            self.assertEqual(note["questions"], [row["question"] for row in view["needs"]["questions"]])
            self.assertFalse(questions_answerable(view["needs"]["questions"], {}))
            self.assertIsNone(view.get("displayed_plan"), view)
            self.assertNotIn("--approve-goal", view.get("next_command") or [], view)
            driver = Driver(
                project, root, flags, environment, autocode=list(taskrun.AUTOCODE), max_steps=2, timeout_seconds=60
            )
            driver.run_dir = run.run_dir
            self.assertEqual("WAITING_FOR_USER", driver.until_stopped()["status"])
            self.assertEqual([], driver.answers)
            self.assertEqual([], driver.approvals)
            self.assertEqual([], driver.steps)
            self.assertEqual(
                ["requirements-gather-01.jsonl"],
                sorted(path.name for path in run.run_dir.glob("iterations/*/*.jsonl")),
            )
            self.assertEqual(before, git(project, "ls-files", "-s"))
            self.assertEqual("", git(project, "diff", "--exit-code"))
            self.assertFalse((project / "docs/bugs/none-cells.json").exists())


if __name__ == "__main__":
    unittest.main()
