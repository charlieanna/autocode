"""Shared blocked receipts are denial-only and clear only on original proof."""

import ast
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_metadata_custody as custody
import autocode_model_listing_failure as listing
import autocode_operational_block as block
import autocode_operational_block_view as block_view
import autocode_recovery_dispatch as dispatch
import autocode_run_actions as actions
import autocode_run_view as run_view
import autocode_util as util
import autocode_verification_failure as verification

from . import test_metadata_custody as custody_fixtures
from . import test_recovery_view as view_fixtures


class OperationalBlockTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        self.run = self.workspace / ".autocode/runs/task"
        self.state = view_fixtures.RecoveryViewTests().state(
            "RUNNING", workspace=str(self.workspace), run_dir=str(self.run), task_id="task", active_seconds=9
        )
        self.runtime = SimpleNamespace(write_json=Mock())
        self.failure = custody.CustodyUncertain()

    def retain(self, error=None):
        self.assertTrue(block.record(self.state, error or self.failure, run_dir=self.run, workspace=self.workspace))
        return block.receipt(self.state)

    def test_domain_receipt_projection_cannot_import_runtime_recovery(self):
        source = Path(block_view.__file__)
        tree = ast.parse(source.read_text())
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
            if alias.name.startswith("autocode_")
        }
        imported.update(
            node.module.lstrip(".").split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module and node.module.lstrip(".").startswith("autocode_")
        )
        self.assertEqual(set(), imported - {"autocode_run_state"})
        for domain in (listing, verification):
            self.assertIs(domain.operational_block, block_view)

    def test_legacy_untyped_errors_and_busy_never_acquire_historical_type_from_prose(self):
        original = copy.deepcopy(self.state)
        for failure in (
            RuntimeError(custody.BLOCKED),
            custody.ProcessError(custody.BLOCKED),
            util.Paused(block.STATUS, custody.BLOCKED),
            custody.ProcessError(custody.BUSY),
        ):
            with self.subTest(failure=type(failure).__name__):
                self.assertFalse(block.record(self.state, failure, run_dir=self.run, workspace=self.workspace))
                self.assertIsNone(block.error(failure))
                self.assertEqual(original, self.state)

    def test_typed_wrappers_keep_the_original_binding_without_mutable_aliases(self):
        binding = {"original": ["metadata-only"]}
        failure = custody.CustodyUncertain(binding)
        wrapper = util.Paused("PAUSED_TRANSPORT_UNVERIFIED", "sanitized wrapper")
        wrapper.__cause__ = failure
        held = self.retain(wrapper)
        binding["original"].append("changed")
        self.assertEqual({"original": ["metadata-only"]}, held["receipt"]["evidence"]["custody_binding"])
        self.assertEqual("metadata_check", held["receipt"]["evidence"]["operation"])
        self.assertEqual(0, held["runner_calls"])
        self.assertNotIn("provider_request_started", held["receipt"]["evidence"], "earlier model work is not denied")

    def test_typed_refusal_never_enters_the_model_or_retry_recovery_chain(self):
        recover = Mock(return_value=True)
        with patch.object(dispatch, "RECOVERY_CHAIN", (("would_replay", recover),)):
            self.assertIsNone(dispatch.recover(self.runtime, self.state, self.run, self.workspace, self.failure))
        recover.assert_not_called()

    def test_public_views_never_probe_healthy_or_recorded_custody(self):
        for held in (False, True):
            with self.subTest(held=held):
                state = copy.deepcopy(self.state)
                if held:
                    block.record(state, self.failure, run_dir=self.run, workspace=self.workspace)
                original = copy.deepcopy(state)
                with (
                    patch.object(custody, "status", side_effect=AssertionError("No global status probe")) as status,
                    patch.object(custody, "recover", side_effect=AssertionError("No recovery from a view")) as recovery,
                ):
                    run_view.view(state)
                status.assert_not_called()
                recovery.assert_not_called()
                self.assertEqual(original, state)

    def test_public_status_keeps_family_control_bindings_private(self):
        private = {
            "root": "/private/runner-custody-root-sentinel",
            "group_nonce": "f00df00df00df00df00df00df00df00d",
            "member_nonce": "baadbaadbaadbaadbaadbaadbaadbaad",
        }
        binding = {
            "schema": 1,
            "scope": "metadata_check",
            "root": private["root"],
            "pending": {
                "group_nonce": private["group_nonce"],
                "attempts": [{"nonce": private["member_nonce"], "result_file": "private-result-sentinel.json"}],
            },
        }
        self.retain(custody.CustodyUncertain(binding))
        public = json.dumps(run_view.view(self.state, workspace=self.workspace, run_dir=self.run))
        for sentinel in (*private.values(), "private-result-sentinel.json", "custody_binding", "group_nonce"):
            self.assertNotIn(sentinel, public)
        self.assertIn(str(self.workspace), public)
        self.assertIn(str(self.run), public)
        self.assertEqual(binding, block.receipt(self.state)["receipt"]["evidence"]["custody_binding"])

    def test_unknown_custody_cannot_be_cleared_by_resume_or_other_gates(self):
        self.retain()
        original = copy.deepcopy(self.state)
        with self.assertRaises(block.BlockedOperation):
            actions.hold_operational_block(
                self.runtime,
                SimpleNamespace(resume_paused=True, approve_goal=True, grant_recovery=99),
                None,
                self.state,
                self.run / "state.json",
                self.run,
                self.workspace,
                None,
            )
        self.assertEqual(original, self.state)
        self.runtime.write_json.assert_not_called()

    def test_a_pending_person_stop_is_consumed_before_the_advancement_hold(self):
        self.retain()
        self.runtime.consume_interventions = Mock(return_value=True)
        with (
            patch.object(actions.stop, "pending_stop", return_value={"kind": "stop"}),
            patch.object(custody, "recover", side_effect=AssertionError("A stop is not advancement")),
        ):
            code = actions.hold_operational_block(
                self.runtime,
                SimpleNamespace(),
                None,
                self.state,
                self.run / "state.json",
                self.run,
                self.workspace,
                None,
            )
        self.assertEqual(2, code)
        self.runtime.consume_interventions.assert_called_once_with(self.state, self.run, self.workspace)
        self.assertIsNotNone(block.receipt(self.state), "a person stop does not certify metadata cleanup")

    def test_legacy_blocks_keep_their_existing_advancement_policy(self):
        self.state["resolver"] = {
            "operational_diagnostics": ["legacy"],
            "operational_receipts": {
                "legacy": {
                    "runner_owned": True,
                    "engine": "runner",
                    "decision": {"action": "blocked"},
                    "receipt": {"scope": "model_listing"},
                }
            },
        }
        with patch.object(custody, "recover", side_effect=AssertionError("No metadata authority for another scope")):
            block.guard(self.runtime, self.state, self.run, self.workspace)
        self.assertIsNotNone(block.receipt(self.state))

    @unittest.skipUnless(os.name == "posix", "Linux metadata filesystem and locks")
    def test_clearing_revalidates_exact_real_receipt_and_retains_the_unaccepted_result(self):
        producer = custody_fixtures.MetadataCustodyTests()
        producer.setUp()
        self.addCleanup(producer.doCleanups)
        producer.leave_pending()
        with self.assertRaises(custody.ProcessError) as caught:
            with custody.admission(timeout=0):
                pass
        self.state["active_stage"] = {"output": "retained.json", "finished_at": "saved", "accounted": True}
        original = copy.deepcopy(self.state)
        self.retain(caught.exception)
        denied = copy.deepcopy(block.receipt(self.state))
        with self.assertRaises(block.BlockedOperation):
            block.guard(self.runtime, self.state, self.run, self.workspace)
        binding = denied["receipt"]["evidence"]["custody_binding"]
        member = binding["pending"]["attempts"][0]
        path = Path(binding["root"]) / member["result_file"]
        path.write_text(json.dumps(producer.response(member["nonce"])))
        # Exercise the normal archive boundary before the new invocation.
        with custody.admission(timeout=0):
            pass
        self.assertFalse((path.parent / "pending.json").exists())
        block.guard(self.runtime, self.state, self.run, self.workspace)
        self.assertIsNone(block.receipt(self.state))
        self.assertEqual(denied, self.state["resolver"]["operational_receipts"][denied["receipt"]["id"]])
        for key in ("active_stage", "settings", "goal_contract", "active_seconds", "next_stage", "stages"):
            self.assertEqual(original[key], self.state[key], key)
        self.assertEqual(block.STATUS, self.state["status"], "cleanup is no stage acceptance or implicit resume")
        self.runtime.write_json.assert_called_once()

    def test_shared_reader_preserves_legacy_scope_words_and_ignores_model_rows(self):
        for scope, owner, expected in (
            ("verification_preparation", verification, "AutoCode is blocked on verification preparation"),
            ("model_listing", listing, "AutoCode is blocked on its model listing"),
        ):
            with self.subTest(scope=scope):
                record = {
                    "runner_owned": True,
                    "engine": "runner",
                    "decision": {"action": "blocked"},
                    "receipt": {"id": "one", "scope": scope},
                }
                state = {"resolver": {"operational_diagnostics": ["one"], "operational_receipts": {"one": record}}}
                self.assertIs(record, owner.blocked(state))
                self.assertEqual(expected, block.presentation(state)["title"])
                self.assertEqual(block.presentation(state), owner.presentation(state))
                record["runner_owned"] = False
                self.assertIsNone(block.receipt(state))
                self.assertIsNone(owner.blocked(state))

    def worker(self):
        task = {"id": "member", "source_revision": "source", "milestone_id": "M1"}
        row = {"run_dir": str(self.run / "worker"), "workspace": str(self.workspace / "member"), "task": task}
        batch = {"id": "batch", "contract_hash": "approved", "baseline": {"revision": "source"}, "base_commit": "base"}
        child = copy.deepcopy(self.state)
        child.update(
            workspace=row["workspace"],
            run_dir=row["run_dir"],
            parent_run=str(self.run),
            parent_batch="batch",
            current_task=task,
        )
        block.record(child, self.failure, run_dir=row["run_dir"], workspace=row["workspace"])
        result = {
            "status": block.STATUS,
            "operational_block": block.worker_payload(child, row["run_dir"], row["workspace"]),
        }
        return task, row, batch, child, result

    def test_worker_binding_is_denial_only_and_rejects_each_foreign_identity(self):
        _task, row, batch, child, result = self.worker()
        for mismatch in (
            None,
            "receipt",
            "parent",
            "batch",
            "task",
            "contract",
            "workspace",
            "head",
            "scope",
            "decision",
            "evidence",
            "oversized",
            "schema_bool",
        ):
            with self.subTest(mismatch=mismatch):
                current_child, current_result = copy.deepcopy(child), copy.deepcopy(result)
                head = "base"
                if mismatch == "receipt":
                    current_result["operational_block"]["receipt_sha256"] = "wrong"
                elif mismatch == "parent":
                    current_child["parent_run"] = str(self.run / "foreign")
                elif mismatch == "batch":
                    current_child["parent_batch"] = "other"
                elif mismatch == "task":
                    current_child["current_task"]["id"] = "other"
                elif mismatch == "contract":
                    current_child["goal_contract"]["hash"] = "other"
                elif mismatch == "workspace":
                    current_child["workspace"] = str(self.workspace / "other")
                elif mismatch == "head":
                    head = "other"
                elif mismatch == "scope":
                    block.receipt(current_child)["receipt"]["scope"] = []
                elif mismatch == "decision":
                    block.receipt(current_child)["decision"] = "not a decision"
                elif mismatch == "evidence":
                    block.receipt(current_child)["receipt"]["evidence"] = "unreadable"
                elif mismatch == "oversized":
                    current_result["operational_block"]["run_dir"] += "x" * 16385
                elif mismatch == "schema_bool":
                    current_result["operational_block"]["schema"] = True
                if mismatch is None:
                    denial = block.worker_error(
                        self.state, batch, row, current_result, current_child, run_dir=self.run, source_head=head
                    )
                    self.assertIsInstance(denial, block.BlockedOperation)
                    self.assertIsNone(denial.record["receipt"]["evidence"]["custody_binding"])
                    self.assertNotIn("result", denial.worker)
                else:
                    with self.assertRaises(util.Paused):
                        block.worker_error(
                            self.state, batch, row, current_result, current_child, run_dir=self.run, source_head=head
                        )

    def test_unauthenticated_worker_claim_grants_no_metadata_or_recovery_authority(self):
        _task, row, batch, child, result = self.worker()
        result["operational_block"]["receipt_sha256"] = "forged"
        with self.assertRaises(block.WorkerEvidenceUncertain) as caught:
            block.worker_error(self.state, batch, row, result, child, run_dir=self.run, source_head="base")
        self.retain(caught.exception)
        evidence = block.receipt(self.state)["receipt"]["evidence"]
        self.assertEqual("worker_evidence", evidence["operation"])
        self.assertNotIn("custody_binding", evidence)
        self.assertIs(evidence["worker"]["relay_authenticated"], False)
        with (
            patch.object(
                custody, "recover", side_effect=AssertionError("A relay claim is no custody fact")
            ) as recovery,
            self.assertRaises(block.BlockedOperation),
        ):
            block.guard(self.runtime, self.state, self.run, self.workspace)
        recovery.assert_not_called()


if __name__ == "__main__":
    unittest.main()
