"""Corrected verification inputs supersede only an idle, unlaunched report repair."""

import copy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import autocode_recovery_history as recovery_history
import autocode_verification_config as configuration
import autocode_verification_repair as retirement

from . import test_report_repair as repair_fixtures


def queue_owned_saved_tester_rejection(run, workspace):
    """Seed an owned CLI fixture with the real rejection/archive/queue producers."""
    runner = repair_fixtures.runner
    state = runner.read_json(run / "state.json")
    previous_status, previous_phase = state["status"], state["phase"]
    contract = state["goal_contract"]
    fixture = SimpleNamespace(run=run, root=workspace)
    record = repair_fixtures.RepairTests.stage_record(
        fixture,
        stage="validator",
        role="tester",
        iteration=state["iteration"],
        contract_hash=contract["hash"],
        contract_revision=contract["revision"],
        task_id=(state.get("current_task") or {}).get("id", ""),
        finished_at=runner.now(),
        schema=str(runner.SCHEMA_DIR / "v2/sol-report.schema.json"),
    )
    record["stage"] = "tester"
    for key in ("before_ref", "after_ref"):
        runner.write_json(Path(record[key]), runner.support.snapshot(workspace))
    try:
        runner.reject_completed_stage(
            state, run, record, ValueError("No independently executed Validator tool event matches receipt")
        )
    except runner.ReportRepairQueued:
        pass
    else:
        raise AssertionError("The completed synthetic Tester rejection was not queued")
    state.update(status=previous_status, phase=previous_phase)
    # The queue counter may advance before a repair launches; no report-only
    # stage exists in this fixture's actual producer ledger.
    state["pending_report_repair"]["attempts"] = 1
    old_proof = run / "obsolete-proof.json"
    runner.write_json(old_proof, {"verdict": "PASS", "synthetic_fixture": True})
    state["regression_proof"] = {"verdict": "PASS", "path": str(old_proof)}
    runner.write_json(run / "state.json", state)
    return state


class VerificationRepairRetirementTests(unittest.TestCase):
    setUp = repair_fixtures.RepairTests.setUp
    stage_record = repair_fixtures.RepairTests.stage_record
    queue = repair_fixtures.RepairTests.queue

    def rejected_tester(self):
        self.state["goal_contract"] = {"hash": "approved", "revision": 6, "approval_status": "approved"}
        self.state["current_task"] = {"id": "T1"}
        self.state["next_stage"] = "tester"
        self.state["settings"]["regression"] = {"test_command": "old suite"}
        self.queue(
            stage="tester",
            role="tester",
            contract_hash="approved",
            contract_revision=6,
            task_id="T1",
            finished_at="2026-11-09T05:01:14+00:00",
            error=ValueError("No independently executed Validator tool event matches receipt"),
        )
        self.state.update(status="PAUSED_INTERVENTION", phase="PAUSED_OR_BLOCKED")
        self.state["regression_proof"] = {"verdict": "PASS", "path": "old-proof.json"}
        return self.state

    def test_material_command_correction_can_be_prepared_without_accepting_the_rejected_report(self):
        state = self.rejected_tester()
        previous = copy.deepcopy(state)
        args = SimpleNamespace(test_command="new suite", resume_paused=True)
        settings = configuration.configure_resume(state, copy.deepcopy(state["settings"]), args)
        self.assertEqual("new suite", settings["regression"]["test_command"])
        self.assertEqual(previous["pending_report_repair"], state["pending_report_repair"])
        self.assertEqual(previous["stages"], state["stages"])
        self.assertEqual(previous["goal_contract"], state["goal_contract"])
        self.assertTrue(state["stages"][-1]["rejected"])
        self.assertFalse(any(row["kind"] == "verification_report_repair_retired" for row in state["user_events"]))

    def prepare(self, state, **options):
        args = SimpleNamespace(**{"test_command": "new suite", "resume_paused": True, **options})
        settings = configuration.configure_resume(state, copy.deepcopy(state["settings"]), args)
        return settings, getattr(args, "_verification_repair_retirement", None)

    def test_commit_preserves_actual_rejection_producer_artifacts_counters_and_approval(self):
        initial = self.rejected_tester()
        for attempts in (0, 1, 2):
            with self.subTest(prelaunch_counter=attempts):
                state = copy.deepcopy(initial)
                # _execute_report_repair increments before run_role can pause;
                # the ordered ledger still contains no launched repair.
                state["pending_report_repair"]["attempts"] = attempts
                before = copy.deepcopy(state)
                files = {path: Path(path).read_bytes() for path in state["pending_report_repair"]["pins"]}
                settings, prepared = self.prepare(state)
                retirement.commit(state, settings, prepared)
                archive = state["report_repair_archive"][-1]
                self.assertEqual(before["pending_report_repair"], archive["repair"])
                self.assertEqual(before["regression_proof"], archive["regression_proof"])
                self.assertNotIn("pending_report_repair", state)
                self.assertNotIn("regression_proof", state)
                for key in ("stages", "history", "active_seconds", "goal_contract", "sessions", "settings"):
                    self.assertEqual(before.get(key), state.get(key), key)
                self.assertEqual(files, {path: Path(path).read_bytes() for path in files})
                row = next(row for row in recovery_history.project(state) if row["kind"] == "report_repair_retired")
                self.assertIn("Fresh checks", row["action"])
                self.assertIn("rejected report", row["action"])
                event = state["user_events"][-1]
                self.assertEqual("verification_report_repair_retired", event["kind"])
                self.assertEqual(retirement.REASON, event["reason"])
                self.assertLess(len(str(event)), 512)

    def test_active_uncertain_wrong_stage_and_unbound_repairs_keep_their_records(self):
        initial = self.rejected_tester()
        mutations = [lambda state, key=key: state.update({key: {"stage": "tester"}}) for key in retirement.BLOCKED]
        mutations += [
            lambda state: state.update(next_stage="review_completion"),
            lambda state: state.update(pending_questions=[{"id": "Q1"}]),
            lambda state: state["goal_contract"].update(hash="another"),
            lambda state: state["current_task"].update(id="another"),
            lambda state: state["pending_report_repair"].update(latest_rejected={}),
            lambda state: state["pending_report_repair"].update(attempts=True),
            lambda state: state["pending_report_repair"].update(attempts=3),
            lambda state: state["stages"][-1].update(rejected=False),
            lambda state: state["stages"].append({**state["stages"][-1], "output": "later", "report_only": True}),
            lambda state: state.update(pause_intent={"held_pause": {"status": "PAUSED_COST_LIMIT"}}),
            lambda state: state.update(applied_interventions=[{"kind": "stop"}]),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                state = copy.deepcopy(initial)
                mutate(state)
                before = copy.deepcopy(state)
                with self.assertRaises(ValueError):
                    self.prepare(state)
                self.assertEqual(before, state)

    def test_same_command_without_resume_and_invalid_options_never_retire(self):
        initial = self.rejected_tester()
        state = copy.deepcopy(initial)
        settings, prepared = self.prepare(state, test_command="old suite", resume_paused=False)
        self.assertIsNone(prepared)
        self.assertEqual(initial, state)
        self.assertEqual(initial["settings"], settings)
        for options in (
            {"resume_paused": False},
            {"test_command": " old suite "},
            {"test_command": " "},
            {"test_report": "not-a-report"},
        ):
            with self.subTest(options=options):
                state = copy.deepcopy(initial)
                before = copy.deepcopy(state)
                with self.assertRaises(ValueError):
                    self.prepare(state, **options)
                self.assertEqual(before, state)

    def test_live_unknown_or_tampered_original_evidence_never_retires(self):
        initial = self.rejected_tester()
        for observed in ({"checked": False, "alive": None}, {"checked": True, "alive": True}):
            with self.subTest(observed=observed):
                state = copy.deepcopy(initial)
                before = copy.deepcopy(state)
                with mock.patch.object(retirement.processes, "recorded_worker_state", return_value=observed):
                    with self.assertRaises(ValueError):
                        self.prepare(state)
                self.assertEqual(before, state)
        output = Path(initial["pending_report_repair"]["original"]["output"])
        output.write_text("changed rejected report")
        before = copy.deepcopy(initial)
        with self.assertRaises(ValueError):
            self.prepare(initial)
        self.assertEqual(before, initial)

    def test_supervised_rejection_requires_every_recorded_owner_to_be_positively_ended(self):
        initial = self.rejected_tester()
        initial["pending_report_repair"]["original"]["supervision"] = {"schema": 1}
        initial["stages"][-1]["supervision"] = {"schema": 1}
        ended = {role: {"checked": True, "alive": False} for role in ("owner", "keeper", "provider")}
        with mock.patch.object(retirement.supervision, "observe", return_value=ended):
            _, prepared = self.prepare(copy.deepcopy(initial))
            self.assertIsNotNone(prepared)
        for role in ended:
            for observation in ({"checked": True, "alive": True}, {"checked": False, "alive": None}):
                with self.subTest(role=role, observation=observation):
                    state = copy.deepcopy(initial)
                    before = copy.deepcopy(state)
                    with mock.patch.object(
                        retirement.supervision, "observe", return_value={**ended, role: observation}
                    ):
                        with self.assertRaises(ValueError):
                            self.prepare(state)
                    self.assertEqual(before, state)

    def test_commit_is_bound_and_never_erases_repair_on_a_refused_settings_write(self):
        state = self.rejected_tester()
        settings, prepared = self.prepare(state)
        for mutate in (
            lambda state, settings: state["pending_report_repair"].update(attempts=1),
            lambda state, settings: settings["regression"].update(test_command="another suite"),
        ):
            with self.subTest(mutate=mutate):
                current, selected = copy.deepcopy(state), copy.deepcopy(settings)
                mutate(current, selected)
                before = copy.deepcopy(current)
                with self.assertRaises(ValueError):
                    retirement.commit(current, selected, prepared)
                self.assertEqual(before, current)


if __name__ == "__main__":
    unittest.main()
