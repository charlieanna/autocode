"""A ticket heading reaches exact plan approval without a model report repair."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
if str(SCENARIOS) not in sys.path:
    sys.path.insert(0, str(SCENARIOS))
from harness import catalog
from harness.driver import fake_setup
from harness.project import materialize, without_maintenance


class TicketTitleCliTests(unittest.TestCase):
    def test_cited_ticket_title_reaches_reviewed_plan_without_report_repair_and_reopens(self):
        with tempfile.TemporaryDirectory(prefix="ticket-title-cli-") as temporary:
            root = Path(temporary)
            scenario = catalog.load("greenfield-greeting-cli")
            project = without_maintenance(materialize(scenario.seed, root / "project"))
            flags, env = fake_setup(scenario, root, scenario.dir / "reference")
            env.update(AUTOCODE_HOME=str(root / "registry"), PYTHONDONTWRITEBYTECODE="1")
            paths = env["PATH"].split(os.pathsep)
            env["PATH"] = os.pathsep.join([paths[0], str(Path(sys.executable).parent), *paths[1:]])
            title = "APP-12398: Missing greeting CLI"
            run = taskrun.TaskRun.start(
                project,
                title + "\n\n" + scenario.brief,
                options=(*flags, "--no-adaptive-planning"),
                env=env,
                timeout=180,
                cwd=root,
            )
            view = run.advance_until_input()
            self.assertEqual("approve_plan", view["needs"]["kind"], view["stop_reason"])
            attempts = view["usage"]["accounting"]["attempts"]
            self.assertTrue(any(row["stage"] == "astra_finalize" for row in attempts))
            self.assertFalse(any(row.get("report_only") for row in attempts))
            self.assertFalse(view["approvals"])
            self.assertIn(title, view["task"])
            # Reopen through the public TaskRun, retaining the exact plan and ledger.
            reopened = taskrun.TaskRun(project, run.run_dir, env=env, timeout=180, cwd=root).status()
            self.assertEqual(view["needs"], reopened["needs"])
            self.assertEqual(attempts, reopened["usage"]["accounting"]["attempts"])

