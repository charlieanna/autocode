"""Current-flow no-build stops, with the model's actual unanswered inputs present."""

from __future__ import annotations

import json
import os
import shlex
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

from scenarios.harness import catalog
from scenarios.harness.driver import Driver, fake_setup, questions_answerable
from scenarios.harness.project import git, materialize, without_maintenance

ROOT = Path(__file__).resolve().parents[1]


class HumanStopCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="human-stop-current-cli-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def fixture(self, scenario_id, *, reporter_questions=False):
        scenario = catalog.load(scenario_id)
        project = without_maintenance(materialize(scenario.seed, self.root / "project"))
        flags, environment = fake_setup(scenario, self.root, scenario.reference)
        environment.update(AUTOCODE_HOME=str(self.root / "registry"), PYTHONDONTWRITEBYTECODE="1")
        paths = environment["PATH"].split(os.pathsep)
        environment["PATH"] = os.pathsep.join([paths[0], str(Path(sys.executable).parent), *paths[1:]])
        if reporter_questions:
            provider = self.root / "bin" / "codex"
            provider.write_text(
                "#!/bin/sh\nexec "
                + shlex.join([sys.executable, str(ROOT / "tests/human_stop_provider.py")])
                + ' "$@"\n'
            )
        return scenario, project, flags, environment

    def start(self, scenario, project, flags, environment):
        return taskrun.TaskRun.start(
            project, scenario.brief, options=flags, env=environment, timeout=180, cwd=self.root
        )

    def assert_no_build_offer(self, view):
        self.assertNotEqual("approve_plan", (view.get("needs") or {}).get("kind"), view)
        self.assertIsNone(view.get("displayed_plan"), view)
        self.assertNotIn("--approve-goal", view.get("next_command") or [], view)

    def test_unreproduced_report_with_unanswered_requirements_stays_stopped(self):
        scenario, project, flags, environment = self.fixture("bugfix-not-reproducible", reporter_questions=True)
        before = git(project, "ls-files", "-s")
        run = self.start(scenario, project, flags, environment)
        view = run.advance_until_input()
        self.assertEqual("WAITING_FOR_USER", view["status"], view)
        self.assertIs(view["done"], False, view)
        self.assertEqual("standard", view["related_profile"]["settings"]["implementation_flow"])
        self.assertEqual("answer", view["needs"]["kind"], view)
        note = json.loads((scenario.reference / "docs/bugs/none-cells.json").read_text())
        questions = view["needs"]["questions"]
        self.assertEqual(note["questions"], [row["question"] for row in questions], questions)
        self.assertFalse(questions_answerable(questions, {}), questions)
        self.assert_no_build_offer(view)
        # A cooperative public client must leave this stop intact instead of
        # making up defaults, approving a plan or dispatching Builder.
        driver = Driver(
            project,
            self.root,
            flags,
            environment,
            autocode=list(taskrun.AUTOCODE),
            max_steps=4,
            timeout_seconds=60,
        )
        driver.run_dir = run.run_dir
        self.assertEqual(view["status"], driver.until_stopped()["status"])
        self.assertEqual([], driver.answers)
        self.assertEqual([], driver.approvals)
        self.assertEqual([], driver.steps)
        self.assertEqual({"status"}, set(driver.unrecorded))
        events = list(run.run_dir.glob("iterations/*/*.jsonl"))
        self.assertEqual(["requirements-gather-01.jsonl"], sorted(path.name for path in events))
        self.assertEqual(before, git(project, "ls-files", "-s"))
        self.assertEqual("", git(project, "diff", "--exit-code"))
        self.assertFalse((project / "docs/bugs/none-cells.json").exists())

    def test_a_discussion_reaches_exact_plan_approval_before_anything_is_written(self):
        # Every task goes through the full pipeline (#3010): a question is planned like a build, with no
        # Analyst shortcut, and nothing is written before the person approves that plan.
        scenario, project, flags, environment = self.fixture("discuss-cache-choice")
        before = git(project, "ls-files", "-s")
        run = self.start(scenario, project, flags, environment)
        view = run.advance_until_input()
        self.assertEqual("approve_plan", view["needs"]["kind"], view)
        self.assertIs(view["done"], False, view)
        self.assertEqual("discuss", view["workflow"], view)
        stages = sorted(path.name for path in run.run_dir.glob("iterations/*/*.jsonl"))
        self.assertNotIn("analyst-01.jsonl", stages)
        self.assertIn("discovery-01.jsonl", stages)
        self.assertFalse(any(name.startswith("builder-") for name in stages), stages)
        self.assertEqual(before, git(project, "ls-files", "-s"))
        self.assertEqual("", git(project, "diff", "--exit-code"))
        self.assertFalse((project / "docs/decisions/metadata-cache.json").exists())

    def test_a_reproduced_bug_still_reaches_exact_plan_approval_and_builds(self):
        scenario, project, flags, environment = self.fixture("bugfix-trivial")
        run = self.start(scenario, project, flags, environment)
        view = run.advance_until_input()
        self.assertEqual("approve_plan", view["needs"]["kind"], view)
        plan = run.show_goal()
        self.assertIn("page_count", plan)
        self.assertTrue(view["displayed_plan"], view)
        self.assertFalse(list(run.run_dir.glob("iterations/*/builder-*.jsonl")))
        run.approve_plan(view["needs"]["token"])
        view = run.advance_until_input()
        self.assertEqual("TASK_COMPLETE", view["status"], view)
        self.assertTrue(list(run.run_dir.glob("iterations/*/builder-*.jsonl")))
        checks = scenario.oracle()(project, scenario)
        self.assertTrue(checks)
        self.assertTrue(all(row.ok for row in checks), checks)


if __name__ == "__main__":
    unittest.main()
