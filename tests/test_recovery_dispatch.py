"""The one recovery chain every dispatch path shares (#1071).

Pins the chain's order and membership, and the two parity repairs the issue names: a
truncated-review pause now recovers automatically on resume, and the serial dispatch
path applies capacity recovery.
"""

import json
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import autocode as runner
import autocode_action_recovery as action_recovery
import autocode_recovery_dispatch as recovery_dispatch
import autocode_stage_recovery as stage_recovery
import autocode_support as s

from . import test_assignment_scenarios, test_goals, test_truncated_review_recovery

# Written out independently of RECOVERY_CHAIN, so a dropped, added or moved row fails here (#3035).
CHAIN_ORDER = (
    "verification_preparation",
    "job_failure",
    "network",
    "startup",
    "route",
    "truncated_review",
    "reasoning_exhaustion",
    "capacity",
    "timeout",
    "external_directory_denial",
    "no_progress",
)

# Where each row's recoverer lives: six in their own modules, four on the runner.
MODULE_ROWS = {
    "verification_preparation": (recovery_dispatch.verification_failure_recovery, "recover"),
    "job_failure": (recovery_dispatch.job_failure, "recover"),
    "network": (recovery_dispatch.network_retry, "recover"),
    "startup": (recovery_dispatch.provider_recovery, "recover_startup"),
    "route": (recovery_dispatch.route_recovery, "recover"),
    "reasoning_exhaustion": (recovery_dispatch.output_cap_recovery, "recover"),
    "no_progress": (recovery_dispatch.resolver_recovery, "recover_no_progress"),
}

RUNTIME_ROWS = {
    "truncated_review": "automatically_recover_truncated_review",
    "capacity": "automatically_recover_capacity_stage",
    "timeout": "automatically_recover_timed_out_stage",
    "external_directory_denial": "automatically_recover_external_directory_denial",
}


class ChainOrderTests(unittest.TestCase):
    def chain(self, calls, fired):
        """Run the chain with every row's recoverer recorded; only ``fired`` recovers."""

        def note(row):
            return lambda *args: calls.append(row) or row == fired

        runtime = SimpleNamespace(**{name: note(row) for row, name in RUNTIME_ROWS.items()})
        with ExitStack() as stack:
            for row, (module, name) in MODULE_ROWS.items():
                stack.enter_context(patch.object(module, name, side_effect=note(row)))
            return recovery_dispatch.recover(runtime, {}, Path("/run"), Path("/work"), s.Paused("PAUSED_TEST", "x"))

    def test_every_row_is_recorded(self):
        self.assertEqual(list(CHAIN_ORDER), [name for name, _ in recovery_dispatch.RECOVERY_CHAIN])
        self.assertEqual(set(CHAIN_ORDER), set(MODULE_ROWS) | set(RUNTIME_ROWS))
        self.assertEqual(len(CHAIN_ORDER), len(MODULE_ROWS) + len(RUNTIME_ROWS))

    def test_the_chain_is_the_live_loop_order(self):
        calls = []
        self.assertIsNone(self.chain(calls, fired=None))
        self.assertEqual(list(CHAIN_ORDER), calls)

    def test_the_first_recovery_that_fires_wins(self):
        for index, row in enumerate(CHAIN_ORDER):
            with self.subTest(row=row):
                calls = []
                self.assertEqual(row, self.chain(calls, fired=row))
                self.assertEqual(list(CHAIN_ORDER[: index + 1]), calls)


class SerialDispatchCapacityTests(unittest.TestCase):
    """The assignment fixture's serial dispatch path, without re-running its tests."""

    setUp = test_goals.GoalTests.setUp
    tearDown = test_assignment_scenarios.AssignmentScenarios.tearDown
    seed = test_assignment_scenarios.AssignmentScenarios.seed
    approve = test_assignment_scenarios.AssignmentScenarios.approve
    greeting_contract = test_assignment_scenarios.AssignmentScenarios.greeting_contract

    def test_the_serial_dispatch_path_applies_capacity_recovery(self):
        self.seed()
        self.greeting_contract()
        self.state["settings"]["orchestration"]["enabled"] = False
        self.state.update(status="RUNNING", phase="EXECUTING", next_stage="builder")
        base = self.run / "iterations/001/terra-01"
        base.parent.mkdir(parents=True, exist_ok=True)
        s.atomic_json(base.with_suffix(".before.json"), s.snapshot(self.root))
        base.with_suffix(".jsonl").write_text('{"type":"turn.failed","error":{"message":"model is at capacity"}}\n')
        self.state["active_stage"] = {
            "role": "builder",
            "stage": "builder",
            "iteration": 1,
            "duration_seconds": 3,
            "output": str(base.with_suffix(".json")),
            "events": str(base.with_suffix(".jsonl")),
            "before_ref": str(base.with_suffix(".before.json")),
            "exit_code": 1,
            "processes": [],
        }
        with (
            patch.object(runner, "run_role", side_effect=s.Paused("PAUSED_PROVIDER_CAPACITY", "model is at capacity")),
            patch.object(runner.time, "sleep"),  # capacity recovery backs off
        ):
            result = runner.autopilot.dispatch_unit(runner, self.state, "builder", self.root, self.run)
        self.assertEqual(runner.orchestrator.SKIP, result)
        self.assertEqual(1, self.state["recovery_context"]["retry_number"])
        self.assertEqual("RUNNING", self.state["status"])
        self.assertNotIn("active_stage", self.state)


class ReportPausePropagationTests(unittest.TestCase):
    """A completion-gate pause while applying a report pauses under its own status, with the attempt set aside (#1851).

    Not a rejected output (no failure record, no Investigator), but not left active either: an active attempt made
    every resume re-apply the same report against the same gate."""

    setUp = test_goals.GoalTests.setUp
    tearDown = test_assignment_scenarios.AssignmentScenarios.tearDown
    seed = test_assignment_scenarios.AssignmentScenarios.seed
    approve = test_assignment_scenarios.AssignmentScenarios.approve
    greeting_contract = test_assignment_scenarios.AssignmentScenarios.greeting_contract

    def test_a_completion_gate_pause_sets_the_report_aside_under_its_own_status(self):
        self.seed()
        self.greeting_contract()
        self.state["settings"]["orchestration"]["enabled"] = False
        self.state.update(status="RUNNING", phase="EXECUTING", next_stage="builder")
        record = {
            "stage": "builder",
            "role": "builder",
            "iteration": 1,
            "exit_code": 0,
            "processes": [],
            "output": str(self.run / "iterations/001/terra-01.json"),
            "events": str(self.run / "iterations/001/terra-01.jsonl"),
        }
        gate = s.Paused("PAUSED_COMPLETION_GATE", "Artifact review requires current passing independent evidence first")
        with (
            patch.object(runner, "run_role", return_value=({}, record)),
            patch.object(runner, "account_stage"),
            patch.object(runner, "commit_stage_result", side_effect=gate),
        ):
            with self.assertRaises(s.Paused) as caught:
                runner.autopilot.dispatch_unit(runner, self.state, "builder", self.root, self.run)
        self.assertEqual("PAUSED_COMPLETION_GATE", caught.exception.status)
        self.assertEqual(("PAUSED_COMPLETION_GATE", str(gate)), (self.state["status"], self.state["stop_reason"]))
        self.assertNotIn("failure_history", self.state, "the gate is not a failed output")
        self.assertNotIn("stuck_investigation", self.state, "no Investigator")
        self.assertNotIn("active_stage", self.state, "a resume must not re-apply the same report")
        self.assertEqual("builder", self.state["next_stage"], "a resume starts a fresh attempt of the stage")
        aside = self.state["stages"][-1]
        self.assertEqual(("builder", str(gate)), (aside["stage"], aside["rejection_reason"]))
        self.assertIn("archived-", aside["output"])


class ResumeParityTests(unittest.TestCase):
    """The truncated-review fixture, without re-running its tests."""

    setUp = test_truncated_review_recovery.TruncatedReviewRecoveryTests.setUp
    attempt = test_truncated_review_recovery.TruncatedReviewRecoveryTests.attempt
    state = test_truncated_review_recovery.TruncatedReviewRecoveryTests.state

    def resume_args(self):
        return SimpleNamespace(
            abandon_stage=None,
            resume_paused=True,
            accept_source_edit=False,
            grant_recovery=None,
            max_milestone_seconds=None,
            retry_report=None,
            retry_failed_stage=False,
            diagnose_failed_stage=False,
        )

    def test_truncated_review_pause_recovers_automatically_on_resume(self):
        state = self.state(self.attempt())
        state.update(status="PAUSED_PROVIDER_UNCERTAIN", stop_reason="Output token limit")
        decided = SimpleNamespace(gate=None, retained_report=False, information_admitted=False)

        def save(path, value):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value))

        with (
            patch.object(
                runner,
                "reconcile_active",
                side_effect=s.Paused("PAUSED_PROVIDER_UNCERTAIN", "Output token limit"),
            ),
            patch.object(stage_recovery.support, "snapshot", return_value=self.snapshot),
            patch.object(stage_recovery.interventions, "pending", return_value=[]),
            patch.object(stage_recovery.records, "write_json", side_effect=save),
        ):
            code = action_recovery.resume(
                runner, self.resume_args(), None, state, self.run / "state.json", self.run, self.root, decided
            )
        self.assertIsNone(code)
        self.assertEqual(("RUNNING", "REPORT_REPAIR"), (state["status"], state["phase"]))
        self.assertIn("pending_report_repair", state)


if __name__ == "__main__":
    unittest.main()
