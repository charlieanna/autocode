"""Public completion requires current runner-owned process-recovery observations."""
from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

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

    def start(self, scenario_id, *, planning=(), solution="reference", env=None, reword=()):
        scenario = catalog.load(scenario_id)
        for old, new in reword:  # a held-out paraphrase of the brief, told to AutoCode and the scripted planner alike
            self.assertIn(old, scenario.brief)
            scenario = dataclasses.replace(scenario, brief=scenario.brief.replace(old, new))
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
        # Three lifecycle phases, then three contenders: both briefs state atomicity under contention.
        self.assertEqual(6, len({worker["pid"] for worker in actual["owned_workers"]}))
        self.assertTrue(all(worker["reaped"] for worker in actual["owned_workers"]))
        exits = [row["worker"]["exit_code"] for row in actual["phases"]]
        self.assertEqual([-9, 0, 0] if protocol.startswith("lease_") else [0, -9, 0], exits)
        self.assertEqual([0, 0, 0], [worker["exit_code"] for worker in actual["contention"]["workers"]])
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

    def test_failed_lifecycle_observation_reaches_the_builder_and_its_correction_completes(self):
        # #451: the runner's failing observation is a finding the repair loop acts on, not a report
        # repair. The scripted reviewers rework it and the second build applies the honest reference.
        scenario = catalog.load("ladder-18-durable-lease-queue")
        scenario, run, view = self.start("ladder-18-durable-lease-queue", solution="broken/process-local-tokens",
                                         planning=("--no-adaptive-planning",),
                                         env={"SCENARIO_FAKE_REWORK_SOLUTION": str(scenario.dir / "reference")})
        self.assertEqual("TASK_COMPLETE", view["status"], self.details(view))
        self.assertTrue(verdict.evaluate(scenario, run.workspace).passed)
        state = json.loads((run.run_dir / "state.json").read_text())
        builders = [row for row in state["stages"] if row.get("stage") == "terra" and not row.get("rejected")]
        self.assertGreaterEqual(len(builders), 2, [row.get("stage") for row in state["stages"]])
        runner = [row for row in state["findings_ledger"] if row["source"] == "runner"]
        self.assertEqual(1, len(runner), state["findings_ledger"])
        self.assertIn("Restart reused a lease token", runner[0]["finding"])
        self.assertEqual("resolved", runner[0]["status"])
        # The correction task the second Builder ran was assigned this finding.
        self.assertEqual(builders[-1]["task_id"], runner[0]["assigned_task"], runner[0])
        self.assertNotEqual(builders[0]["task_id"], builders[-1]["task_id"])
        handoffs = [path.read_text() for path in run.run_dir.rglob("*") if path.is_file()
                    and path.suffix in (".md", ".txt", ".json") and "actionable_findings" in path.read_text(errors="ignore")]
        self.assertTrue(any("Restart reused a lease token" in text for text in handoffs))
        self.assertFalse(any(row.get("stage") == "sol" and row.get("report_only") for row in state["stages"]))
        receipt = view["evidence"]["check_replay"]["risk_acceptance"]
        self.assertEqual("PASS", receipt["verdict"])
        self.assertEqual(1, view["efficiency"]["delivery"]["verified_deliveries"])

    def test_non_atomic_claims_cannot_complete_when_the_brief_promises_contention(self):
        # #451: the claim mutant passes its own tests and every sequential lifecycle phase; only
        # racing interpreters lease one job twice. The finding stays open while the Builder repeats it.
        scenario, run, view = self.start("ladder-18-durable-lease-queue", solution="broken/non-atomic-claim",
                                         planning=("--no-adaptive-planning",))
        self.assert_mutant(scenario, run, view, "Concurrent claims leased one job twice", 2)
        runner = [row for row in view["evidence"]["findings"] if row["source"] == "runner"]
        self.assertEqual(["open"], [row["status"] for row in runner], view["evidence"]["findings"])
        self.assertIn("Concurrent claims leased one job twice", runner[0]["finding"])
        self.assertIn("Released together", runner[0]["finding"])

    def test_reworded_constructor_still_proves_lifecycle_and_an_uncorrected_mutant_stops_on_it(self):
        # #451: with LeaseQueue(db_path) the token mutant used to reach TASK_COMPLETE with no lifecycle
        # record. The scripted Builder reapplies the same mutant for the rework, so the finding stays open.
        scenario, run, view = self.start("ladder-18-durable-lease-queue", solution="broken/process-local-tokens",
                                         planning=("--no-adaptive-planning",),
                                         reword=[("LeaseQueue(path)", "LeaseQueue(db_path)")])
        self.assertNotEqual("TASK_COMPLETE", view["status"], self.details(view))
        self.assertFalse(verdict.evaluate(scenario, run.workspace).passed)
        state = json.loads((run.run_dir / "state.json").read_text())
        observation = state["goal_contract"]["body"]["risk_acceptance"]["manifest"]["observations"][0]
        self.assertEqual(("LeaseQueue", "LeaseQueue"), (observation["declaration"]["constructor"],
                                                        observation["target"]["class_name"]))
        runner = [row for row in view["evidence"]["findings"] if row["source"] == "runner"]
        self.assertEqual(["open"], [row["status"] for row in runner], view["evidence"]["findings"])
        self.assertIn("Restart reused a lease token", runner[0]["finding"])
        self.assertEqual(0, view["efficiency"]["delivery"]["verified_deliveries"])

    def test_held_out_paraphrase_of_the_honest_queue_brief_completes_with_proof(self):
        # #451: rewording one promise used to mark the declaration unsupported, so the plan could
        # never be approved and the honest reference never ran.
        scenario, run, view = self.start("ladder-18-durable-lease-queue", planning=("--no-adaptive-planning",),
                                         reword=[("LeaseQueue(path)", "LeaseQueue(db_path)"),
                                                 ("token is fresh and opaque on every claim",
                                                  "every claim issues a new unguessable token")])
        self.assert_reference(scenario, run, view, "lease_queue_lifecycle_v1")
        self.assertEqual([], view["evidence"]["unverified_risk_claims"])

    def test_unsupported_lifecycle_wording_stops_before_builder_naming_the_missing_fact(self):
        scenario, run, view = self.start("ladder-18-durable-lease-queue", planning=("--no-adaptive-planning",),
                                         reword=[("token is fresh and opaque on every claim",
                                                  "tokens identify claims")])
        self.assertFalse(view["done"], self.details(view))
        self.assertIn("does not state: the token is fresh and opaque on every claim", json.dumps(self.details(view)))
        self.assertEqual((scenario.seed / "leasequeue/__init__.py").read_bytes(),
                         (run.workspace / "leasequeue/__init__.py").read_bytes())

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
