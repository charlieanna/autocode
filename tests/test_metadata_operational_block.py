"""Actual custody refusals stay machinery blocks through public Core boundaries."""

import contextlib
import copy
import io
import json
import os
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
import autocode_stop as stop
import autocode_util as util

from . import test_metadata_custody as custody_fixtures
from . import test_recovery_view as view_fixtures
from .test_operational_block import admission_failure


@unittest.skipUnless(os.name == "posix", "Linux metadata filesystem and locks")
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

    def test_admission_runtime_is_actionless_across_all_stages(self):
        self.failure = admission_failure()
        for stage in ("requirements_gather", "draft_plan", "builder", "tester", "review_completion", "resolve"):
            with self.subTest(stage=stage):
                state = self.state(stage)
                original = copy.deepcopy(state)
                self.assertEqual(2, self.boundary(state))
                self.assert_blocked(state, "PAUSED_METADATA_ADMISSION")
                record = dispatch.operational_block.receipt(state)
                self.assertEqual("metadata_admission", record["receipt"]["scope"])
                self.assertNotIn("custody_binding", record["receipt"]["evidence"])
                for name in ("settings", "goal_contract", "active_seconds", "stages", "history", "next_stage"):
                    self.assertEqual(original[name], state[name], name)
                self.operational_request.assert_not_called()
                self.recovery_advice.assert_not_called()

    def test_admission_reports_never_enter_repair_or_provider_recovery(self):
        self.failure = admission_failure()
        with self.assertRaises(custody.ProcessError) as caught:
            result_application.raise_if_not_rejected(self.failure)
        self.assertIs(self.failure, caught.exception)
        state = self.state("review_completion")
        state["active_stage"] = {"finished_at": "saved", "output": "retained.json", "accounted": True}
        original = copy.deepcopy(state["active_stage"])
        self.boundary(state)
        self.assertEqual(original, state["active_stage"])
        self.assertNotIn("pending_report_repair", state)
        self.assert_blocked(state, "PAUSED_METADATA_ADMISSION")

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

    def parent_denial(
        self,
        *,
        tamper=False,
        admission=False,
        extra_failure=None,
        reverse=False,
        malformed_sibling=False,
        stopped=False,
        outer=False,
        live_sibling=False,
    ):
        parent = self.state("orchestrator")
        if stopped:
            parent["applied_interventions"] = [{"id": "saved-stop", "kind": "stop", "actor": "person"}]
            stop.assert_stopped(parent)
        child_dir = self.run / "worker"
        child_dir.mkdir()
        child_workspace = self.workspace / "member"
        child_workspace.mkdir()
        task = {"id": "member-task", "source_revision": "assigned-source", "milestone_id": "M1"}
        row = {
            "run_dir": str(child_dir),
            "workspace": str(child_workspace),
            "task": task,
            "milestone_id": "M1",
            "status": "INTERRUPTED",
        }
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
        elif tamper == "missing_envelope":
            (child_dir / "result.json").unlink()
        elif tamper == "stale_envelope":
            (child_dir / "result.json").write_text(json.dumps({"status": "BUILT", "report": "unaccepted.json"}))
        elif tamper == "malformed_envelope":
            (child_dir / "result.json").write_text("{not-json")
        elif tamper == "foreign":
            saved = json.loads((child_dir / "state.json").read_text())
            saved["parent_batch"] = "foreign"
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
        batch["workers"].append(
            {
                "run_dir": str(sibling),
                "workspace": str(self.workspace),
                "milestone_id": "M2",
                "status": "BUILT",
                "task": {"id": "finished-task"},
            }
        )
        if extra_failure is not None:
            extra_dir = self.run / "other-denial"
            extra_dir.mkdir()
            extra_workspace = self.workspace / "other-member"
            extra_workspace.mkdir()
            extra = copy.deepcopy(row)
            extra.update(run_dir=str(extra_dir), workspace=str(extra_workspace), milestone_id="M3")
            extra["task"].update(id="other-task", milestone_id="M3")
            other = copy.deepcopy(child)
            other.update(run_dir=str(extra_dir), workspace=str(extra_workspace), current_task=extra["task"])
            with patch.object(
                worker.runner, "write_json", side_effect=lambda path, value: path.write_text(json.dumps(value))
            ):
                worker.save_pause(extra_dir, other, extra_workspace, extra_failure)
            batch["workers"].append(extra)
        if malformed_sibling:
            broken = self.run / "unreadable-unclaimed"
            broken.mkdir()
            (broken / "state.json").write_text("{")
            (broken / "result.json").write_text(json.dumps({"status": "BUILT"}))
            batch["workers"].insert(
                0,
                {
                    "run_dir": str(broken),
                    "workspace": str(self.workspace),
                    "milestone_id": "M4",
                    "status": "BUILT",
                    "task": {"id": "unreadable-task"},
                },
            )
        if reverse:
            batch["workers"].reverse()
        if outer:
            batch["status"] = "BUILDING"
            parent["orchestration_batch"] = batch

        def source_snapshot(directory, *args, **kwargs):
            if tamper == "source":
                raise OSError("fixture source is unavailable")
            return batch["baseline"] if outer and directory == self.workspace else {"head": "base"}

        with (
            patch.object(dispatch.autocode_status, "persist"),
            patch.object(
                dispatch, "worker_alive", side_effect=lambda row: live_sibling and row["milestone_id"] == "M2"
            ),
            patch.object(dispatch.processes, "process_table"),
            patch.object(dispatch.s, "assert_no_legacy_process"),
            patch.object(
                dispatch.lifeline, "prepare", side_effect=AssertionError("Unexpected recovery admission")
            ) as prepare,
            patch.object(dispatch, "git", return_value=b""),
            patch.object(dispatch.goals, "execution_guard"),
            patch.object(dispatch, "enforce_cross_model_verification"),
            patch.object(dispatch, "enabled", return_value=True),
            patch.object(
                dispatch.source_scope,
                "snapshot",
                side_effect=source_snapshot,
            ),
        ):
            try:
                self.boundary(
                    parent,
                    execute=(lambda *args: dispatch.dispatch(parent, self.workspace, self.run))
                    if outer
                    else (lambda *args: dispatch.run_workers(parent, self.run, batch))
                    if admission
                    else (lambda *args: dispatch.collect(parent, self.workspace, self.run, batch)),
                )
            except AssertionError:
                self.fail("The persisted worker denial must be observed before recovery Core preparation")
            prepare.assert_not_called()
        self.assertEqual("retained parent source", source.read_text())
        self.assertEqual(20, parent["active_seconds"])
        self.assertNotIn("implementation", parent)
        self.assertNotIn("approved:M1", parent.get("milestone_progress", {}))
        self.assertEqual(1, len(batch["accounted_attempts"]))
        return parent

    def test_parent_authenticates_the_worker_denial_before_generic_failure(self):
        parent = self.parent_denial()
        self.assert_blocked(parent)

    def test_worker_admission_relay_is_denial_only_and_bound(self):
        self.failure = admission_failure()
        parent = self.parent_denial()
        self.assert_blocked(parent, "PAUSED_METADATA_ADMISSION")
        evidence = dispatch.operational_block.receipt(parent)["receipt"]["evidence"]
        self.assertEqual("not_admitted", evidence["admission"])
        self.assertNotIn("custody_binding", evidence)

    def test_complete_worker_denial_blocks_recovery_before_launch(self):
        self.failure = admission_failure()
        parent = self.parent_denial(admission=True)
        self.assert_blocked(parent, "PAUSED_METADATA_ADMISSION")

    def test_complete_custody_envelope_also_blocks_recovery_before_launch(self):
        parent = self.parent_denial(admission=True)
        self.assert_blocked(parent)

    def test_outer_dispatch_observes_all_denials_after_malformed_accounting(self):
        self.failure = admission_failure()
        parent = self.parent_denial(extra_failure=custody.CustodyUncertain(), malformed_sibling=True, outer=True)
        self.assert_blocked(parent, "PAUSED_ORCHESTRATOR_WORKER")
        records = parent["resolver"]["operational_receipts"].values()
        self.assertEqual(
            {"metadata_custody", "metadata_admission", "worker_evidence"}, {row["receipt"]["scope"] for row in records}
        )

    def test_live_sibling_cannot_hide_an_observed_unknown_worker_denial(self):
        parent = self.parent_denial(admission=True, live_sibling=True, reverse=True)
        self.assert_blocked(parent)

    def test_batch_refusals_retain_applied_stop_and_all_usage_without_recovery(self):
        self.failure = admission_failure()
        parent = self.parent_denial(extra_failure=custody.CustodyUncertain(), stopped=True, admission=True)
        self.assertEqual("PAUSED_INTERVENTION", parent["status"])
        self.assertEqual(stop.STOP_REASON, parent["stop_reason"])
        public = run_view.view(parent)
        self.assertEqual("stopped", public["recovery"]["category"])
        self.assertIsNone(public["next_command"])
        self.assertIsNone(public["needs"])
        records = parent["resolver"]["operational_receipts"].values()
        self.assertEqual({"metadata_custody", "metadata_admission"}, {row["receipt"]["scope"] for row in records})

    def test_worker_batch_strongest_denial_is_order_independent(self):
        for admission in (False, True):
            for reverse in (False, True):
                with self.subTest(admission=admission, reverse=reverse), tempfile.TemporaryDirectory() as directory:
                    workspace = Path(directory).resolve()
                    run = workspace / "run"
                    run.mkdir()
                    with patch.object(self, "workspace", workspace), patch.object(self, "run", run):
                        self.failure = admission_failure()
                        parent = self.parent_denial(
                            admission=admission, extra_failure=custody.CustodyUncertain(), reverse=reverse
                        )
                        self.assert_blocked(parent)
                        records = parent["resolver"]["operational_receipts"].values()
                        self.assertEqual(
                            {"metadata_custody", "metadata_admission"}, {row["receipt"]["scope"] for row in records}
                        )

    def test_missing_stale_foreign_admission_envelopes_preserve_usage_without_progress(self):
        for tamper in ("missing_envelope", "stale_envelope", "foreign"):
            for admission in (False, True):
                with self.subTest(tamper=tamper, admission=admission), tempfile.TemporaryDirectory() as directory:
                    workspace = Path(directory).resolve()
                    run = workspace / "run"
                    run.mkdir()
                    with patch.object(self, "workspace", workspace), patch.object(self, "run", run):
                        self.failure = admission_failure()
                        parent = self.parent_denial(tamper=tamper, admission=admission)
                        self.assert_blocked(parent, "PAUSED_ORCHESTRATOR_WORKER")
                        self.assertNotIn(
                            "custody_binding", dispatch.operational_block.receipt(parent)["receipt"]["evidence"]
                        )

    def test_malformed_accounting_keeps_all_later_denials_and_readable_usage(self):
        for admission in (False, True):
            for reverse in (False, True):
                with self.subTest(admission=admission, reverse=reverse), tempfile.TemporaryDirectory() as directory:
                    workspace = Path(directory).resolve()
                    run = workspace / "run"
                    run.mkdir()
                    with patch.object(self, "workspace", workspace), patch.object(self, "run", run):
                        self.failure = admission_failure()
                        parent = self.parent_denial(
                            admission=admission,
                            extra_failure=custody.CustodyUncertain(),
                            reverse=reverse,
                            malformed_sibling=True,
                        )
                        self.assert_blocked(parent, "PAUSED_ORCHESTRATOR_WORKER")
                        records = parent["resolver"]["operational_receipts"].values()
                        self.assertEqual(
                            {"metadata_custody", "metadata_admission", "worker_evidence"},
                            {row["receipt"]["scope"] for row in records},
                        )

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

    def test_missing_worker_envelope_cannot_erase_the_persisted_denial(self):
        parent = self.parent_denial(tamper="missing_envelope")
        self.assert_blocked(parent, "PAUSED_ORCHESTRATOR_WORKER")
        evidence = dispatch.operational_block.receipt(parent, dispatch.operational_block.WORKER_EVIDENCE)
        self.assertNotIn("custody_binding", evidence["receipt"]["evidence"])

    def test_stale_built_envelope_cannot_accept_progress_beside_a_persisted_denial(self):
        parent = self.parent_denial(tamper="stale_envelope")
        self.assert_blocked(parent, "PAUSED_ORCHESTRATOR_WORKER")

    def test_malformed_worker_envelope_cannot_erase_the_persisted_denial(self):
        parent = self.parent_denial(tamper="malformed_envelope")
        self.assert_blocked(parent, "PAUSED_ORCHESTRATOR_WORKER")

    def test_missing_worker_envelope_blocks_recovery_before_any_launch(self):
        parent = self.parent_denial(tamper="missing_envelope", admission=True)
        self.assert_blocked(parent, "PAUSED_ORCHESTRATOR_WORKER")

    def test_stale_built_envelope_blocks_recovery_before_any_launch(self):
        parent = self.parent_denial(tamper="stale_envelope", admission=True)
        self.assert_blocked(parent, "PAUSED_ORCHESTRATOR_WORKER")


if __name__ == "__main__":
    unittest.main()
