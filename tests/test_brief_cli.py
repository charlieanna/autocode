"""Original brief output is a public completion gate, independently of delivered tests."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import autocode_taskrun as taskrun

# Catalog oracle files import harness.oracle when loaded outside the runner.
SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
if str(SCENARIOS) not in sys.path:
    sys.path.insert(0, str(SCENARIOS))
from harness import catalog, verdict
from harness.driver import fake_setup
from harness.project import materialize, without_maintenance


class BriefCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="brief-cli-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.scenario = catalog.load("greenfield-todo-cli")

    @staticmethod
    def details(view):
        return {key: view.get(key) for key in ("status", "next_stage", "needs", "stop_reason")}

    def start(self, *, planning, solution="reference", env=None):
        project = without_maintenance(materialize(self.scenario.seed, self.root / "project"))
        flags, provider_env = fake_setup(self.scenario, self.root, self.scenario.dir / solution)
        environment = {**provider_env, "AUTOCODE_HOME": str(self.root / "registry"),
                       "PYTHONDONTWRITEBYTECODE": "1", **(env or {})}
        # The fake and the product's python3 checks use this fixture interpreter.
        paths = environment["PATH"].split(os.pathsep)
        environment["PATH"] = os.pathsep.join([paths[0], str(Path(sys.executable).parent), *paths[1:]])
        run = taskrun.TaskRun.start(project, self.scenario.brief,
                                   options=(*flags, *planning), env=environment,
                                   timeout=180, cwd=self.root)
        return run, self.drive(run)

    def drive(self, run):
        """Serve this fixture's delegated plan approval through the task-run API."""
        for _ in range(12):
            view = run.advance_until_input()
            if view["done"]:
                return view
            need = view.get("needs") or {}
            if need.get("kind") == "approve_plan":
                run.approve_plan(need["token"])
            elif need.get("kind") == "review":
                for criterion in need["criteria"]:
                    run.approve_review(criterion, need["token"])
            else:
                return view
        self.fail("The scripted task did not stop within twelve public approval gates")

    def reattach(self, run):
        return taskrun.TaskRun(run.workspace, run.run_dir, options=run.options,
                               env=run.env, timeout=180, cwd=self.root)

    def assert_reference(self, run, view):
        self.assertEqual("TASK_COMPLETE", view["status"], self.details(view))
        checked = verdict.evaluate(self.scenario, run.workspace)
        self.assertTrue(checked.passed, checked.summary)
        self.assertEqual(10, len(checked.checks))
        replay = view["evidence"]["check_replay"]
        self.assertEqual("PASS", replay["verdict"])
        receipt = replay["brief_acceptance"]
        self.assertIsInstance(receipt, dict)
        self.assertEqual("PASS", receipt["verdict"])
        self.assertTrue(receipt["checks"], "Completion needs an independently executed brief case")
        self.assertTrue(Path(receipt["summary"]).is_file())
        self.assertEqual(view["evidence"]["validator_source_revision"], receipt["source_revision"])
        for check in receipt["checks"]:
            observed = json.loads(Path(check["output"]).read_text())
            self.assertEqual("PASS", observed["verdict"])
            self.assertEqual(check["observation_hash"], observed["observation_hash"])
            self.assertEqual([0] * len(observed["steps"]), [step["exit_code"] for step in observed["steps"]])
        self.assertEqual(1, view["efficiency"]["delivery"]["verified_deliveries"])
        return receipt

    def assert_mutant(self, run, view):
        self.assertNotEqual("TASK_COMPLETE", view["status"], self.details(view))
        self.assertFalse(view["done"], self.details(view))
        delivered = subprocess.run([sys.executable, "-m", "unittest", "test_todo.py"],
                                   cwd=run.workspace, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                                   capture_output=True, text=True, timeout=30)
        self.assertEqual(0, delivered.returncode, (delivered.stdout + delivered.stderr)[-800:])
        self.assertIn("Ran 3 tests", delivered.stderr)
        checked = verdict.evaluate(self.scenario, run.workspace)
        self.assertEqual(10, len(checked.checks))
        self.assertEqual({"add_then_list", "complete_marks_done", "ids_stable_across_restarts"},
                         {check.name for check in checked.checks if not check.ok}, checked.summary)
        # Read owned runner evidence, without changing a checkpoint or provider result.
        failures = [json.loads(path.read_text()) for path in
                    run.run_dir.glob("check-replay/**/brief-acceptance/*/summary.json")]
        self.assertTrue(failures, "The runtime must retain its independently executed rejection")
        format_failures = []
        for receipt in failures:
            if receipt["verdict"] != "FAIL":
                continue
            for check in receipt["checks"]:
                observed = json.loads(Path(check["output"]).read_text())
                if "original brief format" in observed.get("reason", ""):
                    format_failures.append(observed)
        self.assertTrue(format_failures, "The failure must be actual CLI output, not an unrelated gate")
        self.assertTrue(all(step["exit_code"] == 0 for observed in format_failures for step in observed["steps"]))

    def check_mutant_restart(self, planning):
        run, view = self.start(planning=planning, solution="broken/unbracketed-status")
        self.assert_mutant(run, view)
        again = self.reattach(run)
        self.assertFalse(again.status()["done"])
        try:
            again.resume_paused()
        except taskrun.TaskRunError as error:
            # A bounded retry may already be exhausted; it still cannot claim completion.
            self.assertIsNotNone(error.process, str(error))
        restarted = self.drive(again)
        self.assert_mutant(again, restarted)

    def test_fixed_reference_has_current_brief_receipt_and_tampering_invalidates_completion(self):
        run, view = self.start(planning=("--no-adaptive-planning",))
        receipt = self.assert_reference(run, view)
        again = self.reattach(run)
        summary = Path(receipt["summary"])
        original = summary.read_bytes()
        summary.write_bytes(original + b"\n")
        self.assertEqual(0, again.status()["efficiency"]["delivery"]["verified_deliveries"])
        summary.write_bytes(original)
        self.assertEqual(1, again.status()["efficiency"]["delivery"]["verified_deliveries"])

    def test_v2_reference_has_current_brief_receipt_and_source_change_invalidates_completion(self):
        run, view = self.start(planning=("--planning-v2", "--no-adaptive-planning"))
        self.assert_reference(run, view)
        again = self.reattach(run)
        source = run.workspace / "todo.py"
        original = source.read_bytes()
        source.write_bytes((self.scenario.dir / "broken/unbracketed-status/todo.py").read_bytes())
        self.assertEqual(0, again.status()["efficiency"]["delivery"]["verified_deliveries"])
        source.write_bytes(original)
        self.assertEqual(1, again.status()["efficiency"]["delivery"]["verified_deliveries"])

    def test_adaptive_early_review_reference_has_current_brief_receipt(self):
        run, view = self.start(planning=("--adaptive-planning",))
        self.assert_reference(run, view)

    def test_fixed_selfconsistent_unbracketed_mutant_cannot_complete_after_restart(self):
        self.check_mutant_restart(("--no-adaptive-planning",))

    def test_v2_selfconsistent_unbracketed_mutant_cannot_complete_after_restart(self):
        self.check_mutant_restart(("--planning-v2", "--no-adaptive-planning"))

    def test_omitted_reviewer_observations_cannot_reach_build_or_completion(self):
        run, view = self.start(planning=("--no-adaptive-planning",),
                               env={"SCENARIO_FAKE_BRIEF_OMIT": "1"})
        self.assertFalse(view["done"], self.details(view))
        self.assertFalse((run.workspace / "todo.py").exists(), "Missing source-linked proof must stop before the Builder")
        self.assertEqual(0, view["efficiency"]["delivery"]["verified_deliveries"])
        self.assertFalse(self.reattach(run).status()["done"])


if __name__ == "__main__":
    unittest.main()
