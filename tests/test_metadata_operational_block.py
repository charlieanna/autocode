"""Actual custody refusals stay machinery blocks through public Core boundaries."""

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_builder_worker as worker
import autocode_dispatch as dispatch
import autocode_metadata_custody as custody
import autocode_native_calls as native_calls
import autocode_result_application as result_application
import autocode_run_setup as setup
import autocode_run_view as run_view
import autocode_status_summary as summary
import autocode_util as util

from . import test_metadata_custody as custody_fixtures
from . import test_recovery_view as view_fixtures


class MetadataOperationalBlockTests(unittest.TestCase):
    def setUp(self):
        # Existing journal fixture: no helper/backend process or new API is
        # needed to reproduce the original plain-ProcessError classification.
        producer = custody_fixtures.MetadataCustodyTests()
        producer.setUp()
        self.addCleanup(producer.doCleanups)
        producer.leave_pending()
        try:
            with custody.admission(timeout=0):
                self.fail("Unconfirmed metadata must refuse another command")
        except custody.ProcessError as error:
            self.failure = error
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        self.run = self.workspace / ".autocode/runs/task"
        self.run.mkdir(parents=True)
        guard = patch("subprocess.Popen", side_effect=AssertionError("No process may launch in this regression"))
        self.launch = guard.start()
        self.addCleanup(guard.stop)

    def state(self, stage="builder"):
        return view_fixtures.RecoveryViewTests().state(
            "RUNNING",
            workspace=str(self.workspace),
            run_dir=str(self.run),
            task="Keep the selected task",
            task_id="task",
            next_stage=stage,
            active_seconds=7,
            history=[],
        )

    def boundary(self, state, *, execute=None, load_error=False):
        def raise_failure(*args, **kwargs):
            raise self.failure

        self.writes = []
        self.operational_request = Mock()
        self.recovery_advice = Mock(return_value="Unexpected resume advice")
        owner = Mock()
        owner.__enter__ = Mock(return_value=owner)
        owner.__exit__ = Mock(return_value=False)
        owner.result = lambda code, **kwargs: code
        runtime = SimpleNamespace(
            stop_policy=SimpleNamespace(applied_stop=lambda value: False),
            util=SimpleNamespace(
                runner_identity=lambda: {},
                runner_changed=lambda *args: None,
                JSONCommitUncertain=util.JSONCommitUncertain,
            ),
            write_json=lambda path, value: self.writes.append(copy.deepcopy(value)),
            run_actions=SimpleNamespace(handle=lambda *args: None),
            build_loop=SimpleNamespace(run=execute or raise_failure),
            support=SimpleNamespace(Paused=util.Paused),
            now=util.now,
            resolver_runtime=SimpleNamespace(record_operational_exhaustion=self.operational_request),
            pause_command=SimpleNamespace(recovery=self.recovery_advice),
        )
        with (
            patch.object(setup, "load_locked", side_effect=raise_failure if load_error else None, return_value=state),
            patch.object(setup.keep_awake, "hold"),
            patch.object(setup.controller_owner, "Owner", return_value=owner),
            patch.object(setup.worker_binding, "bind"),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            try:
                return setup.run_locked(
                    runtime, SimpleNamespace(), None, state, self.run / "state.json", self.run, self.workspace
                )
            except (custody.ProcessError, AttributeError, KeyError):
                # The old startup boundary leaked the untyped error. Keep the
                # assertion behavioral when the original code is exercised.
                return 2

    def assert_blocked(self, state, status="PAUSED_PROCESS_CLEANUP"):
        public = run_view.view(state, workspace=self.workspace, run_dir=self.run)
        self.assertEqual(status, public["status"])
        self.assertIsNone(public["needs"])
        self.assertIsNone(public["next_command"])
        self.assertEqual("blocked", public["recovery"]["category"])
        self.assertEqual(["inspect"], [row["kind"] for row in public["recovery"]["actions"]])
        self.assertEqual("blocked", summary.project(public)["state"])
        self.assertNotIn("resume", public["chat"]["notice"].lower())
        self.assertNotIn("choose", public["chat"]["notice"].lower())
        self.assertIsNone(public["recovery"]["outcome"])
        self.launch.assert_not_called()

    def test_public_controller_block_is_shared_by_every_stage(self):
        for stage in ("requirements_gather", "draft_plan", "builder", "tester", "review_completion", "resolve"):
            with self.subTest(stage=stage):
                state = self.state(stage)
                original = copy.deepcopy(state)
                self.assertEqual(2, self.boundary(state))
                self.assert_blocked(state)
                for name in ("settings", "goal_contract", "active_seconds", "stages", "history", "next_stage"):
                    self.assertEqual(original[name], state[name], name)
                self.operational_request.assert_not_called()
                self.recovery_advice.assert_not_called()

    def test_loading_a_saved_run_preserves_the_same_typed_block(self):
        state = self.state()
        self.assertEqual(2, self.boundary(state, load_error=True))
        self.assert_blocked(state)

    def test_completed_report_does_not_become_rejected_output(self):
        with self.assertRaises(custody.ProcessError) as caught:
            result_application.raise_if_not_rejected(self.failure)
        self.assertIs(self.failure, caught.exception)
        state = self.state("review_completion")
        state["active_stage"] = {"finished_at": "saved", "output": "retained.json", "accounted": True}
        original = copy.deepcopy(state["active_stage"])
        self.boundary(state)
        self.assertEqual(original, state["active_stage"])
        self.assertNotIn("pending_report_repair", state)
        self.assert_blocked(state)

    def test_native_report_rejection_preserves_the_typed_error_and_bytes(self):
        calls = object.__new__(native_calls.PlanningCalls)
        calls.sink = SimpleNamespace(write=Mock())
        calls.run_dir = self.run
        calls.repairable = frozenset()
        for launched in (False, True):
            with self.subTest(launched=launched):
                state = self.state()
                record = {"output": str(self.run / "retained.json"), "stage": "builder"}
                original = copy.deepcopy(record)
                try:
                    if launched:
                        calls.reject_launch(state, record, self.failure, None, {})
                    else:
                        calls.reject(state, record, self.failure, closed=False)
                except (custody.ProcessError, util.Paused):
                    pass
                self.assertEqual(original, record)
                calls.sink.write.assert_not_called()

    def test_worker_serializes_the_runner_denial_instead_of_a_generic_pause(self):
        child = self.state()
        with patch.object(
            worker.runner, "write_json", side_effect=lambda path, value: path.write_text(json.dumps(value))
        ):
            worker.save_pause(self.run, child, self.workspace, self.failure)
        self.assert_blocked(child)
        result = json.loads((self.run / "result.json").read_text())
        self.assertIn("operational_block", result)
        self.assertNotIn(
            "custody_binding", result["operational_block"], "the result carries a bound reference, not authority"
        )

    def parent_denial(self, *, tamper=False):
        parent = self.state("orchestrator")
        child_dir = self.run / "worker"
        child_dir.mkdir()
        child_workspace = self.workspace / "member"
        child_workspace.mkdir()
        task = {"id": "member-task", "source_revision": "assigned-source", "milestone_id": "M1"}
        row = {"run_dir": str(child_dir), "workspace": str(child_workspace), "task": task, "milestone_id": "M1"}
        batch = {
            "id": "batch",
            "contract_hash": "approved",
            "baseline": {"revision": "assigned-source"},
            "base_commit": "base",
            "workers": [row],
        }
        child = self.state()
        child.update(
            workspace=str(child_workspace),
            run_dir=str(child_dir),
            current_task=task,
            parent_run=str(self.run),
            parent_batch="batch",
            milestone_progress={"approved:M1": {"status": "must-not-be-accepted"}},
        )
        with patch.object(
            worker.runner, "write_json", side_effect=lambda path, value: path.write_text(json.dumps(value))
        ):
            worker.save_pause(child_dir, child, child_workspace, self.failure)
        if tamper:
            result_path = child_dir / "result.json"
            result = json.loads(result_path.read_text())
            result.setdefault(
                "operational_block",
                {
                    "schema": 1,
                    "receipt_id": "fixture-untrusted-claim",
                    "receipt_sha256": "forged",
                    "run_dir": str(child_dir),
                    "workspace": str(child_workspace),
                },
            )
            if tamper is True:
                result["operational_block"]["receipt_sha256"] = "forged"
            result_path.write_text(json.dumps(result))
        if tamper == "unreadable":
            (child_dir / "state.json").write_text("{not-json")
        elif tamper in ("accounting", "accounting_missing"):
            saved = json.loads((child_dir / "state.json").read_text())
            saved["stages"] = [None] if tamper == "accounting" else [{"accounted": True}]
            (child_dir / "state.json").write_text(json.dumps(saved))
        source = self.workspace / "source.txt"
        source.write_text("retained parent source")
        sibling = self.run / "finished-sibling"
        sibling.mkdir()
        sibling_state = {
            "stages": [
                {
                    "accounted": True,
                    "iteration": 1,
                    "stage": "builder",
                    "started_at": "saved-attempt",
                    "duration_seconds": 13,
                }
            ]
        }
        (sibling / "state.json").write_text(json.dumps(sibling_state))
        batch["workers"].append({"run_dir": str(sibling), "milestone_id": "M2"})

        with (
            patch.object(dispatch.autocode_status, "persist"),
            patch.object(dispatch, "worker_alive", return_value=False),
            patch.object(dispatch, "git", return_value=b""),
            patch.object(
                dispatch.source_scope,
                "snapshot",
                return_value={"head": "base"},
                side_effect=OSError("fixture source is unavailable") if tamper == "source" else None,
            ),
        ):
            self.boundary(parent, execute=lambda *args: dispatch.collect(parent, self.workspace, self.run, batch))
        self.assertEqual("retained parent source", source.read_text())
        self.assertEqual(20, parent["active_seconds"])
        self.assertNotIn("implementation", parent)
        self.assertNotIn("approved:M1", parent.get("milestone_progress", {}))
        self.assertEqual(1, len(batch["accounted_attempts"]))
        return parent

    def test_parent_authenticates_the_worker_denial_before_generic_failure(self):
        parent = self.parent_denial()
        self.assert_blocked(parent)

    def test_forged_worker_relay_is_an_actionless_parent_evidence_block(self):
        parent = self.parent_denial(tamper=True)
        self.assert_blocked(parent, "PAUSED_ORCHESTRATOR_WORKER")
        public = run_view.view(parent)
        self.assertIn("could not authenticate", public["recovery"]["what_happened"])
        self.assertNotIn("earlier model-free check", public["recovery"]["what_happened"])

    def test_unreadable_worker_state_cannot_turn_a_relay_into_a_person_pause(self):
        parent = self.parent_denial(tamper="unreadable")
        self.assert_blocked(parent, "PAUSED_ORCHESTRATOR_WORKER")

    def test_unreadable_worker_source_cannot_turn_a_relay_into_a_person_pause(self):
        parent = self.parent_denial(tamper="source")
        self.assert_blocked(parent, "PAUSED_ORCHESTRATOR_WORKER")

    def test_malformed_claimed_worker_accounting_keeps_other_usage_and_blocks(self):
        parent = self.parent_denial(tamper="accounting")
        self.assert_blocked(parent, "PAUSED_ORCHESTRATOR_WORKER")

    def test_incomplete_claimed_worker_accounting_is_never_assumed_zero(self):
        parent = self.parent_denial(tamper="accounting_missing")
        self.assert_blocked(parent, "PAUSED_ORCHESTRATOR_WORKER")


if __name__ == "__main__":
    unittest.main()
