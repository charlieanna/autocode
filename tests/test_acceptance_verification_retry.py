"""A completed acceptance writer is not paid again for a local proof failure."""

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import autocode_acceptance_tests as acceptance
import autocode_acceptance_tests_job as job
import autocode_acceptance_verification_retry as retry
import autocode_command_receipt as receipts
import autocode_contract_identity as identity
import autocode_run_view as run_view
import autocode_source_scope as source_scope
import autocode_stuck_job as stuck
import autocode_util as util

from tests.test_stuck_job import state_for


class AcceptanceVerificationRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.workspace, self.run = root / "project", root / "run"
        self.workspace.mkdir()
        self.run.mkdir()
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.workspace),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.test",
                "commit",
                "--allow-empty",
                "-qm",
                "base",
            ],
            check=True,
        )
        (self.workspace / "tests").mkdir()
        (self.workspace / "tests/test_feature.py").write_text("# independently written test\n")
        body = {
            "open_blocking_questions": [],
            "acceptance_criteria": [{"id": "AC1", "criterion": "answer", "verification_method": "test: test_answer"}],
        }
        contract = {"task_id": "T1", "revision": 1, "body": body}
        contract["hash"] = util.digest(contract)
        approval = {"kind": "goal_approval", "actor": "user_cli", "token": identity.token(contract)}
        contract.update(approval_status="approved", approval_event=approval)
        self.state = {
            "workspace": str(self.workspace),
            "status": "RUNNING",
            "settings": {"spec_first": True},
            "goal_contract": contract,
            "user_events": [approval],
            "current_task": {"id": "T1"},
            "next_stage": "builder",
            "stages": [],
            "history": [],
        }
        acceptance.route(self.state)
        self.value = {
            "summary": "Test the answer",
            "tests": [{"case_id": "AC1", "file": "tests/test_feature.py", "test_name": "test_answer"}],
            "already_passing": [],
            "cannot_write": [],
            "tests_run": [],
        }
        self.record = {
            "stage": acceptance.STAGE,
            "contract_hash": contract["hash"],
            "task_id": "T1",
            "accounted": True,
            "exit_code": 0,
            "source_revision": source_scope.snapshot(self.workspace, self.state)["revision"],
            "changed_files": ["tests/test_feature.py"],
            "engine": "opencode",
            "output_mode": "opencode_events",
        }
        for key in retry.ARTIFACTS:
            path = self.run / (key + ".json")
            util.atomic_json(path, self.value if key == "output" else job.SCHEMA if key == "schema" else {})
            self.record[key] = str(path)
        event = lambda kind, **part: {
            "type": kind,
            "sessionID": "fixture",
            "part": {"id": kind, "messageID": "reply", **part},
        }
        Path(self.record["events"]).write_text(
            "\n".join(
                json.dumps(row)
                for row in [
                    event("text", text=json.dumps(self.value)),
                    event(
                        "step_finish", reason="stop", tokens={"input": 1, "output": 1, "cache": {"read": 0, "write": 0}}
                    ),
                ]
            )
        )
        self.record["metrics"] = {
            "request_context": {
                "accounting": {
                    "event_file": {
                        "path": self.record["events"],
                        "sha256": util.file_hash(self.record["events"]),
                    }
                }
            }
        }
        self.state["stages"] = [self.record]
        self.state["history"] = [self.record]
        self.result = {
            "exit_code": 0,
            "results": {
                "passed": ["tests.test_feature.Feature.test_answer"],
                "failed": [],
                "skipped": [],
                "total": 1,
                "complete": True,
            },
        }
        self.reference = Mock(
            side_effect=[
                {"error": "Cannot classify passing tests against the original reference: execution missing"},
                {"verdicts": {"AC1": "fails"}},
            ]
        )
        self.candidate = Mock(return_value=self.result)
        self.runtime = SimpleNamespace(
            load_stage_report=Mock(return_value=self.value),
            write_json=util.atomic_json,
            commit_boundary_candidate=self.commit,
            run_role=Mock(side_effect=AssertionError("model replay")),
        )

    def commit(self, state, candidate, run_dir, workspace):
        state.clear()
        state.update(candidate)
        util.atomic_json(run_dir / "state.json", state)

    def apply(self, stage, state, value, record, workspace, **kwargs):
        job.apply(
            state,
            value,
            record,
            workspace,
            run_tests=self.candidate,
            run_reference=self.reference,
            evidence_dir=self.run / "checks",
        )

    def start_retry(self):
        self.apply(acceptance.STAGE, self.state, self.value, self.record, self.workspace)
        self.assertEqual("RUNNING", self.state["status"])
        self.assertIn(retry.KEY, self.state[acceptance.KEY])
        self.assertFalse(acceptance.receipts(self.state))

    def dispatch(self):
        return retry.dispatch(self.runtime, self.state, acceptance.STAGE, self.workspace, self.run, apply=self.apply)

    def test_local_retry_reaches_builder_without_second_writer_or_accounting(self):
        self.start_retry()
        self.dispatch()
        self.assertEqual("builder", self.state["next_stage"])
        self.assertEqual(1, len(self.state["stages"]))
        self.assertEqual(1, len(self.state["history"]))
        self.runtime.run_role.assert_not_called()
        self.assertEqual(2, self.candidate.call_count)
        self.assertEqual(2, self.reference.call_count)
        self.assertTrue(acceptance.receipts(self.state))
        self.assertNotIn(retry.KEY, self.state[acceptance.KEY])
        archived = self.state[acceptance.KEY]["verification_retries"][0]
        self.assertEqual(1, archived["attempts"])
        self.assertEqual(1, acceptance.verification_attempts(self.state))
        self.assertEqual(1, len(archived["failures"]))

    def test_repeated_failure_exhausts_without_model_replay_or_allowance_reset(self):
        self.reference.side_effect = None
        self.reference.return_value = {"error": "original-reference execution missing"}
        self.start_retry()
        for _ in range(2):
            with self.assertRaises(util.Paused) as stopped:
                self.dispatch()
            self.assertEqual("PAUSED_INVALID_OUTPUT", stopped.exception.status)
        self.assertEqual(2, self.reference.call_count)
        self.assertEqual(1, self.state[acceptance.KEY][retry.KEY]["attempts"])
        self.assertEqual(2, len(self.state[acceptance.KEY][retry.KEY]["failures"]))
        self.assertFalse(acceptance.receipts(self.state))
        self.runtime.run_role.assert_not_called()

    def test_resumed_local_failure_reaches_investigator_without_another_model_record(self):
        self.state.update(
            version=3, settings={**state_for(workspace=str(self.workspace))["settings"], "spec_first": True}
        )
        self.reference.side_effect = None
        self.reference.return_value = {"error": "original-reference execution missing"}
        self.start_retry()
        dispatched = []

        class ReachedInvestigator(Exception):
            pass

        def dispatch(state, stage):
            dispatched.append(stage)
            if stage == stuck.STAGE:
                raise ReachedInvestigator
            return self.dispatch()

        with self.assertRaises(ReachedInvestigator):
            try:
                stuck.drive(
                    self.state,
                    dispatch,
                    active=lambda state: state["status"] == "RUNNING",
                    skip=object(),
                    paused=util.Paused,
                    investigate=True,
                )
            except util.Paused as error:
                self.fail(f"A newly attempted local check paused without investigation: {error}")
        self.assertEqual([acceptance.STAGE, stuck.STAGE], dispatched)
        self.assertEqual(1, len(self.state["stages"]))
        self.assertEqual(1, self.state[acceptance.KEY][retry.KEY]["attempts"])

        # Merely resuming the exhausted check is still an old pause, not a fresh attempt.
        self.state["next_stage"] = acceptance.STAGE
        dispatched.clear()
        with self.assertRaises(util.Paused):
            stuck.drive(
                self.state,
                dispatch,
                active=lambda state: state["status"] == "RUNNING",
                skip=object(),
                paused=util.Paused,
                investigate=True,
            )
        self.assertEqual([acceptance.STAGE], dispatched)

    def test_stale_source_report_plan_and_task_never_execute(self):
        self.start_retry()
        self.reference.side_effect = None
        self.reference.return_value = {"verdicts": {"AC1": "fails"}}
        original = copy.deepcopy(self.state)
        for fault in ("source", "report", "events", "plan", "task", "uncertain", "pins"):
            with self.subTest(fault=fault):
                self.state = copy.deepcopy(original)
                target = None
                if fault == "source":
                    target = self.workspace / "extra.py"
                elif fault in ("report", "events"):
                    target = Path(self.record["output" if fault == "report" else "events"])
                elif fault == "plan":
                    self.state["goal_contract"]["approval_status"] = "draft"
                elif fault == "task":
                    self.state["current_task"]["id"] = "different"
                elif fault == "pins":
                    self.state[acceptance.KEY][retry.KEY]["pins"] = {}
                else:
                    self.state["uncertain_artifacts"] = ["uncollected"]
                previous = target.read_bytes() if target and target.exists() else None
                if target:
                    target.write_text("changed")
                try:
                    with self.assertRaises(util.Paused):
                        self.dispatch()
                finally:
                    if target:
                        target.write_bytes(previous) if previous is not None else target.unlink()
        self.assertEqual(1, self.reference.call_count)

    def test_uncertain_cleanup_propagates_and_spent_retry_survives(self):
        self.start_retry()
        self.reference.side_effect = receipts.OwnershipUncertain("uncertain command")
        with self.assertRaises(receipts.OwnershipUncertain):
            self.dispatch()
        self.assertEqual(1, self.state[acceptance.KEY][retry.KEY]["attempts"])
        self.assertFalse(acceptance.receipts(self.state))

    def test_bound_investigator_grant_allows_one_check_retry_and_is_never_replenished(self):
        self.reference.side_effect = None
        self.reference.return_value = {"error": "original-reference execution missing"}
        self.start_retry()
        with self.assertRaises(util.Paused):
            self.dispatch()
        self.state["stuck_investigation"] = {
            "identity": "diagnosed-once",
            "in_force": True,
            "stage": acceptance.STAGE,
            "status": "PAUSED_INVALID_OUTPUT",
            **retry.recovery_binding.binding(self.state, acceptance.STAGE),
        }
        for _ in range(2):
            with self.assertRaises(util.Paused):
                self.dispatch()
        pending = self.state[acceptance.KEY][retry.KEY]
        self.assertEqual(2, pending["attempts"])
        self.assertEqual(["diagnosed-once"], pending["investigations"])
        self.assertEqual(3, self.reference.call_count)
        self.runtime.run_role.assert_not_called()

    def test_stale_investigator_advice_cannot_renew_retry(self):
        self.start_retry()
        self.state[acceptance.KEY][retry.KEY]["attempts"] = 1
        self.state["stuck_investigation"] = {
            "identity": "old-diagnosis",
            "in_force": True,
            "stage": acceptance.STAGE,
            "status": "PAUSED_INVALID_OUTPUT",
            "execution_context": "old-inputs",
        }
        with self.assertRaises(util.Paused):
            self.dispatch()
        self.assertEqual(1, self.reference.call_count)

    def test_invalid_saved_attempt_count_never_runs_checks(self):
        self.start_retry()
        for count in (None, "1", True, -1):
            with self.subTest(count=count):
                self.state[acceptance.KEY][retry.KEY]["attempts"] = count
                with self.assertRaises(util.Paused):
                    self.dispatch()
        self.assertEqual(1, self.reference.call_count)

    def test_legacy_resume_uses_pinned_terminal_report_and_keeps_bound(self):
        self.state.update(
            status="PAUSED_ACCEPTANCE_TESTS",
            stop_reason="The runner could not establish the approved pre-build proof: Cannot classify passing tests against the original reference: missing execution",
        )
        self.assertTrue(retry.retain_legacy(self.state, self.workspace))
        self.assertFalse(retry.retain_legacy(self.state, self.workspace))
        self.assertEqual(0, self.state[acceptance.KEY][retry.KEY]["attempts"])
        self.reference.side_effect = None
        self.reference.return_value = {"verdicts": {"AC1": "fails"}}
        self.dispatch()
        self.assertEqual("builder", self.state["next_stage"])

    def test_legacy_modified_events_or_behavioral_pause_are_not_recovered(self):
        self.state.update(status="PAUSED_ACCEPTANCE_TESTS", stop_reason="AC1 already works")
        self.assertFalse(retry.retain_legacy(self.state, self.workspace))
        self.state["stop_reason"] = (
            "The runner could not establish the approved pre-build proof: Cannot classify passing tests against the original reference: missing execution"
        )
        Path(self.record["events"]).write_text("tampered")
        self.assertFalse(retry.retain_legacy(self.state, self.workspace))

    def test_saved_pause_names_resume_and_explains_retained_verification(self):
        self.state.update(status="PAUSED_ACCEPTANCE_TESTS", stop_reason="original-reference execution missing")
        need = run_view.needs(self.state)
        self.assertEqual("--resume-paused", need["action"])
        self.assertIn("retained report", need["guidance"])
