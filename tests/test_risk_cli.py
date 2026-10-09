"""Public completion requires current runner-owned process-recovery observations."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
if str(SCENARIOS) not in sys.path:
    sys.path.insert(0, str(SCENARIOS))
from harness import catalog, verdict
from harness.driver import fake_setup
from harness.project import materialize, without_maintenance


class RiskCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="risk-cli-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    @staticmethod
    def details(view):
        return {key: view.get(key) for key in ("status", "next_stage", "needs", "stop_reason")}

    def start(self, scenario_id, *, planning=(), solution="reference", env=None):
        scenario = catalog.load(scenario_id)
        project = without_maintenance(materialize(scenario.seed, self.root / "project"))
        flags, provider_env = fake_setup(scenario, self.root, scenario.dir / solution)
        environment = {**provider_env, "AUTOCODE_HOME": str(self.root / "registry"),
                       "PYTHONDONTWRITEBYTECODE": "1", **(env or {})}
        paths = environment["PATH"].split(os.pathsep)
        environment["PATH"] = os.pathsep.join([paths[0], str(Path(sys.executable).parent), *paths[1:]])
        run = taskrun.TaskRun.start(project, scenario.brief, options=(*flags, *planning),
                                   env=environment, timeout=180, cwd=self.root)
        return scenario, run, self.drive(run)

    def drive(self, run):
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
        self.fail("The scripted lifecycle task exceeded twelve public approval gates")

    def reattach(self, run):
        return taskrun.TaskRun(run.workspace, run.run_dir, options=run.options,
                               env=run.env, timeout=180, cwd=self.root)

    def assert_reference(self, scenario, run, view, protocol):
        self.assertEqual("TASK_COMPLETE", view["status"], self.details(view))
        holdout = verdict.evaluate(scenario, run.workspace)
        self.assertTrue(holdout.passed, holdout.summary)
        receipt = view["evidence"]["check_replay"]["risk_acceptance"]
        self.assertEqual("PASS", receipt["verdict"])
        self.assertEqual(view["evidence"]["validator_source_revision"], receipt["source_revision"])
        self.assertEqual(1, len(receipt["checks"]))
        actual = receipt["checks"][0]["observation"]
        self.assertEqual(protocol, actual["protocol"])
        self.assertEqual(3, len({worker["pid"] for worker in actual["owned_workers"]}))
        self.assertTrue(all(worker["reaped"] for worker in actual["owned_workers"]))
        exits = [row["worker"]["exit_code"] for row in actual["phases"]]
        self.assertEqual([-9, 0, 0] if protocol.startswith("lease_") else [0, -9, 0], exits)
        self.assertEqual(1, view["efficiency"]["delivery"]["verified_deliveries"])
        return receipt

    def assert_mutant(self, scenario, run, view, expected_reason, tests):
        self.assertNotEqual("TASK_COMPLETE", view["status"], self.details(view))
        self.assertFalse(view["done"], self.details(view))
        delivered = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                                   cwd=run.workspace, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                                   capture_output=True, text=True, timeout=30)
        self.assertEqual(0, delivered.returncode, (delivered.stdout + delivered.stderr)[-800:])
        self.assertIn(f"Ran {tests} test", delivered.stderr)
        holdout = verdict.evaluate(scenario, run.workspace)
        self.assertFalse(holdout.passed, holdout.summary)
        failures = [json.loads(path.read_text()) for path in
                    run.run_dir.glob("check-replay/**/risk-acceptance/*/summary.json")]
        observed = [json.loads(Path(check["output"]).read_text())
                    for result in failures if result["verdict"] == "FAIL"
                    for check in result["checks"] if check.get("output")]
        self.assertTrue(any(expected_reason in row.get("error", "") for row in observed), observed)
        self.assertEqual(0, view["efficiency"]["delivery"]["verified_deliveries"])

    def mutant_restart(self, scenario_id, solution, reason, tests):
        scenario, run, view = self.start(scenario_id, solution=solution,
                                       planning=("--no-adaptive-planning",))
        self.assert_mutant(scenario, run, view, reason, tests)
        again = self.reattach(run)
        self.assertFalse(again.status()["done"])
        try:
            again.resume_paused()
        except taskrun.TaskRunError as error:
            self.assertIsNotNone(error.process, str(error))
        self.assert_mutant(scenario, again, self.drive(again), reason, tests)

    def test_adaptive_queue_reference_has_hard_death_proof_and_tamper_invalidates_delivery(self):
        scenario, run, view = self.start("ladder-18-durable-lease-queue", planning=("--adaptive-planning",))
        receipt = self.assert_reference(scenario, run, view, "lease_queue_lifecycle_v1")
        again = self.reattach(run)
        summary = Path(receipt["summary"]); original = summary.read_bytes()
        summary.write_bytes(original + b"\n")
        self.assertEqual(0, again.status()["efficiency"]["delivery"]["verified_deliveries"])
        summary.write_bytes(original)
        self.assertEqual(1, again.status()["efficiency"]["delivery"]["verified_deliveries"])

    def test_v2_outbox_reference_has_hard_death_proof_and_source_edit_invalidates_delivery(self):
        scenario, run, view = self.start("ladder-19-transactional-outbox",
                                       planning=("--planning-v2", "--no-adaptive-planning"))
        self.assert_reference(scenario, run, view, "transactional_outbox_lifecycle_v1")
        again = self.reattach(run)
        source = run.workspace / "outbox" / "__init__.py"; original = source.read_bytes()
        source.write_bytes((scenario.dir / "broken/ack-with-exception-rollback/outbox/__init__.py").read_bytes())
        self.assertEqual(0, again.status()["efficiency"]["delivery"]["verified_deliveries"])
        source.write_bytes(original)
        self.assertEqual(1, again.status()["efficiency"]["delivery"]["verified_deliveries"])

    def test_process_local_lease_tokens_cannot_complete_after_restart(self):
        self.mutant_restart("ladder-18-durable-lease-queue", "broken/process-local-tokens",
                            "Restart reused a lease token", 2)

    def test_exception_only_outbox_rollback_cannot_complete_after_restart(self):
        self.mutant_restart("ladder-19-transactional-outbox", "broken/ack-with-exception-rollback",
                            "Hard-kill lost the unacknowledged event", 3)

    def test_omitted_lifecycle_observation_stops_before_builder(self):
        scenario, run, view = self.start("ladder-18-durable-lease-queue",
                                       planning=("--no-adaptive-planning",),
                                       env={"SCENARIO_FAKE_RISK_OMIT": "1"})
        self.assertFalse(view["done"], self.details(view))
        self.assertEqual((scenario.seed / "leasequeue/__init__.py").read_bytes(),
                         (run.workspace / "leasequeue/__init__.py").read_bytes())
        self.assertEqual(0, view["efficiency"]["delivery"]["verified_deliveries"])
        self.assertFalse(self.reattach(run).status()["done"])


if __name__ == "__main__":
    unittest.main()
