"""#1071: real CLI capacity recovery and an independent negative product control."""

import argparse
import json
import os
import tempfile
import unittest
from pathlib import Path

from scenarios import run


class CapacityScenarioTests(unittest.TestCase):
    def scenario(self):
        return run.catalog.load("capacity-recovery")

    def run_fake(self, solution, *, hybrid=False):
        retained = os.environ.get("CAPACITY_SCENARIO_EVIDENCE")
        if retained:
            out = Path(retained)
        else:
            temporary = tempfile.TemporaryDirectory(prefix="capacity-scenario-")
            self.addCleanup(temporary.cleanup)
            out = Path(temporary.name)
        args = argparse.Namespace(
            fake=True,
            hybrid=hybrid,
            profile=None,
            fake_solution=solution,
            out=out,
            autocode=None,
            max_steps=None,
            timeout_minutes=5,
        )
        result = run.run_one(self.scenario(), args)
        print(f"capacity-recovery/{solution}: {result['verdict']}, {result['wall_seconds']}s; {result['evidence']}")
        return result

    def test_oracle_distinguishes_seed_reference_and_broken_solution(self):
        rows = run.self_test(self.scenario())
        self.assertEqual({"seed", "reference", "broken/accepts-empty-name"}, {name for name, _, _ in rows})
        self.assertTrue(all(ok for _, ok, _ in rows), rows)

    def test_a_correct_product_without_capacity_evidence_cannot_pass(self):
        scenario = self.scenario()
        with tempfile.TemporaryDirectory(prefix="capacity-no-fault-") as directory:
            project = run.materialize(scenario.seed, Path(directory) / "project", scenario.reference)
            result = run.verdict.evaluate(scenario, project, {})
        self.assertFalse(result.passed)
        self.assertEqual(["capacity_evidence_retained"], [check.name for check in result.checks if not check.ok])

    def test_live_delivery_without_the_scripted_fault_is_not_capacity_qualification(self):
        scenario = self.scenario()
        for solution, expected in (
            ("reference", run.verdict.NOT_EXERCISED),
            ("broken/accepts-empty-name", run.verdict.FALSE_COMPLETE),
        ):
            with (
                self.subTest(solution=solution),
                tempfile.TemporaryDirectory(prefix="capacity-live-control-") as directory,
            ):
                project = run.materialize(scenario.seed, Path(directory) / "project", scenario.dir / solution)
                record = {"provider_evidence": "live", "status": "TASK_COMPLETE", "view": {"done": True}}
                oracle = run.verdict.evaluate(scenario, project, record)
                diagnosis = run.verdict.diagnose(scenario, project, record)
                self.assertEqual(run.verdict.NOT_EXERCISED, diagnosis["verdict"])
                outcome, summary = run.verdict.judge("TASK_COMPLETE", oracle, scenario.expected)
                outcome, _ = run.verdict.exercised(
                    outcome, summary, scenario.requires_stages, scenario.requires_stages, diagnosis["reason"]
                )
                self.assertEqual(expected, outcome)

    def test_missing_source_identity_cannot_count_as_bound_capacity_evidence(self):
        scenario = self.scenario()
        with tempfile.TemporaryDirectory(prefix="capacity-source-control-") as directory:
            root = Path(directory).resolve()
            project = run.materialize(scenario.seed, root / "project", scenario.reference)
            run_dir = root / "run"
            archive = run_dir / "iterations/001/archived-requirements-01-control"
            archive.mkdir(parents=True)
            events = archive / "requirements-01.jsonl"
            events.write_text(
                json.dumps({"type": "thread.started", "thread_id": "failed-session"})
                + "\n"
                + json.dumps({"type": "turn.failed", "error": {"code": "model_overloaded"}})
                + "\n"
            )
            snapshot = archive / "requirements-01.after.json"
            context = {
                "stage": "astra_discovery",
                "next_stage": "astra_discovery",
                "retry_number": 1,
                "changed_files": [],
                "events": str(events),
                "source_snapshot": str(snapshot),
            }
            failed = {
                "stage": "astra_discovery",
                "model": "same-model",
                "rejected": True,
                "interrupted": True,
                "exit_code": 1,
                "events": str(events),
                "output": str(archive / "requirements-01.json"),
            }
            succeeded = {
                "stage": "astra_discovery",
                "model": "same-model",
                "rejected": False,
                "interrupted": False,
                "exit_code": 0,
            }
            rows = [
                {"stage": "astra_discovery", "model": "same-model", "session": "failed-session", "outcome": "capacity"},
                {
                    "stage": "astra_discovery",
                    "model": "same-model",
                    "session": "new-session",
                    "outcome": "continue",
                    "recovery_context": context,
                },
            ]
            record = {
                "provider_evidence": "fake",
                "status": "TASK_COMPLETE",
                "run_dir": str(run_dir),
                "view": {
                    "done": True,
                    "usage": {"accounting": {"attempts": [failed, succeeded]}},
                    "evidence": {"check_replay": {"verdict": "PASS"}},
                },
            }
            for revision in (None, ""):
                with self.subTest(revision=revision):
                    if revision is not None:
                        context["source_revision"] = failed["source_revision"] = revision
                    snapshot.write_text(json.dumps({} if revision is None else {"revision": revision}))
                    (root / "capacity-trace.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
                    result = run.verdict.evaluate(scenario, project, record)
                    self.assertEqual(
                        ["one_bounded_recovery_of_the_same_stage"],
                        [check.name for check in result.checks if not check.ok],
                    )

            # A hybrid call must bind the actual retry handoff and output to
            # the successful public accounting row, not just a side label.
            context["source_revision"] = failed["source_revision"] = "revision"
            snapshot.write_text(json.dumps({"revision": "revision"}))
            succeeded["output"] = str(run_dir / "iterations/001/requirements-02.json")
            injected = {
                "stage": "astra_discovery",
                "side": "scripted",
                "model": "same-model",
                "report": str(run_dir / "iterations/001/requirements-01.json"),
            }
            retry = {
                "stage": "astra_discovery",
                "side": "live",
                "model": "same-model",
                "report": succeeded["output"],
                "recovery_context": context,
            }
            hybrid_trace = root / "hybrid/trace.jsonl"
            hybrid_trace.parent.mkdir()
            for field, value, failed_check in (
                ("report", succeeded["output"], None),
                ("report", "unrelated.json", "one_capacity_failure_then_fresh_request"),
                ("side", "scripted", "capacity_evidence_retained"),
            ):
                with self.subTest(field=field, value=value):
                    served = [injected, {**retry, field: value}]
                    hybrid_trace.write_text("".join(json.dumps(row) + "\n" for row in served))
                    result = run.verdict.evaluate(scenario, project, record)
                    self.assertEqual(
                        [] if failed_check is None else [failed_check],
                        [check.name for check in result.checks if not check.ok],
                    )

            later = {**succeeded, "output": str(run_dir / "iterations/001/requirements-03.json")}
            attempts = record["view"]["usage"]["accounting"]["attempts"]
            attempts.append(later)
            record["answers"] = [{"id": "Q1", "answer": "Use the conventional usage line"}]
            continuation = {**retry, "report": later["output"], "recovery_context": None}
            for broken in (False, True):
                with self.subTest(later_interrupted=broken):
                    later["interrupted"] = broken
                    hybrid_trace.write_text("".join(json.dumps(row) + "\n" for row in [injected, retry, continuation]))
                    result = run.verdict.evaluate(scenario, project, record)
                    self.assertEqual(
                        ["capacity_evidence_retained"] if broken else [],
                        [check.name for check in result.checks if not check.ok],
                    )

    def test_hybrid_capacity_retry_reaches_the_live_side_after_one_scripted_failure(self):
        result = self.run_fake("reference", hybrid=True)
        self.assertEqual(run.verdict.PASS, result["verdict"], result["summary"])
        self.assertEqual(["astra_discovery"], result["hybrid"]["scripted_stage_names"])
        self.assertEqual(1, result["hybrid"]["live_stage_names"].count("astra_discovery"))

    def test_serial_capacity_failure_recovers_once_and_completes(self):
        result = self.run_fake("reference")
        self.assertEqual(run.verdict.PASS, result["verdict"], result["summary"])
        self.assertEqual("TASK_COMPLETE", result["runner_status"])
        self.assertEqual(2, result["metrics"]["by_stage"]["astra_discovery"]["count"])
        self.assertEqual([], result["answers"], "Capacity must recover without an operator answer")

    def test_capacity_recovery_does_not_hide_a_wrong_delivery(self):
        result = self.run_fake("broken/accepts-empty-name")
        self.assertEqual(run.verdict.FALSE_COMPLETE, result["verdict"], result["summary"])
        failed = {check["name"] for check in result["checks"] if not check["ok"]}
        self.assertEqual({"empty-name.exit", "empty-name.stdout", "empty-name.stderr"}, failed)


if __name__ == "__main__":
    unittest.main()
