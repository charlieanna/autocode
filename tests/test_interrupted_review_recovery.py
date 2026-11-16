"""A stopped source-read-only attempt is settled by its core owner."""

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode as runner
import autocode_action_recovery as actions
import autocode_checkpoint_reconciliation as reconciliation
import autocode_interrupted_review_recovery as recovery
import autocode_launch_binding as launch_binding
import autocode_recovery_history as history
import autocode_run_view as run_view
import autocode_source_scope as source_scope
import autocode_stale_handoff as handoff
import autocode_util as util

from tests.test_liveness import inspection, metadata


class InterruptedReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        (self.workspace / "app.py").write_text("value = 1\n")
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run(["git", "-C", str(self.workspace), "add", "app.py"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.workspace),
                "-c",
                "user.name=Tester",
                "-c",
                "user.email=tester@example.test",
                "commit",
                "-qm",
                "seed",
            ],
            check=True,
        )
        self.run = self.root / "run"
        self.run.mkdir()
        self.state = {
            "workspace": str(self.workspace),
            "run_dir": str(self.run),
            "status": "RUNNING",
            "phase": "EXECUTING",
            "next_stage": "review_completion",
            "iteration": 1,
            "settings": {"roles": {}, "limits": {"max_cost_usd": 1}},
            "stages": [],
            "history": [],
            "user_events": [],
            "sessions": {"reviewer": "old-session"},
            "goal_contract": {"hash": "goal", "revision": 1, "body": {}},
            "current_task": {"id": "task"},
        }
        before = source_scope.snapshot(self.workspace, self.state)
        base = self.run / "review-01"
        util.atomic_json(base.with_suffix(".before.json"), before)
        base.with_suffix(".jsonl").write_text('{"type":"note","text":"partial review"}\n')
        base.with_suffix(".json").write_text('{"unaccepted":"response"}')
        self.record = {
            "stage": "review_completion",
            "role": "reviewer",
            "route_role": "reviewer",
            "iteration": 1,
            "output": str(base.with_suffix(".json")),
            "events": str(base.with_suffix(".jsonl")),
            "before_ref": str(base.with_suffix(".before.json")),
            "source_revision": before["revision"],
            "supervision": metadata(),
            "task_id": "task",
            "contract_hash": "goal",
            "contract_revision": 1,
            "started_at": "2026-11-16T10:00:00+00:00",
        }
        handoff.capture_launch_binding(self.state, self.record, before)
        self.state["active_stage"] = self.record
        self.fresh = inspection(self.record["supervision"], owner=False, keeper=False, provider=False)
        self.fresh["receipt"].update(cause="owner_lost", phase="stopped", cleanup_error=None)
        self.path = self.run / "state.json"

    def reconcile(self):
        args = SimpleNamespace(reconcile_only=reconciliation.token(self.state))
        with patch.object(runner.supervision, "observe", return_value=self.fresh):
            return actions.reconcile_checkpoint(
                runner,
                args,
                SimpleNamespace(error=lambda message: self.fail(message)),
                self.state,
                self.path,
                self.run,
                self.workspace,
                runner.run_actions.Decided(),
            )

    def test_controller_loss_is_archived_without_a_person_recovery_gate(self):
        settings = copy.deepcopy(self.state["settings"])
        self.assertEqual(0, self.reconcile())
        self.assertEqual("RUNNING", self.state["status"])
        self.assertNotIn("active_stage", self.state)
        notice = history.project(self.state)[-1]
        self.assertEqual("interrupted_attempt", notice["kind"])
        self.assertFalse(notice["handled"])
        self.assertIn("retained the attempt and its usage", notice["action"])
        self.assertEqual(settings, self.state["settings"])
        self.assertEqual(1, self.state["automatic_recoveries_since_resume"])
        archived = self.state["stages"][-1]
        self.assertTrue(archived["abandoned"])
        self.assertTrue(archived["accounted"])
        self.assertEqual({"unaccepted": "response"}, json.loads(Path(archived["output"]).read_text()))
        self.assertNotIn("reviewer", self.state["sessions"])
        self.assertFalse(recovery.settle(runner, self.state, self.run, self.workspace, self.fresh))
        self.assertEqual(1, self.state["automatic_recoveries_since_resume"])

    def test_current_validation_is_kept_for_completion(self):
        evidence = self.run / "independent-evidence.txt"
        evidence.write_text("independent check passed")
        self.state["validation"] = {
            "source_revision": self.record["source_revision"],
            "criteria_revision": None,
            "contract_hash": "goal",
            "contract_revision": 1,
            "task_id": "task",
            "evidence_hashes": {str(evidence): util.file_hash(evidence)},
        }
        before = copy.deepcopy(self.state["validation"])
        self.reconcile()
        self.assertEqual(before, self.state["validation"])
        self.assertEqual("review_completion", self.state["next_stage"])

    def test_missing_independent_evidence_routes_to_verification(self):
        self.reconcile()
        self.assertEqual("tester", self.state["next_stage"])
        self.assertNotIn("validation", self.state)

    def test_unreadable_or_incomplete_source_receipt_never_authorizes_recovery(self):
        path = Path(self.record["before_ref"])
        before = copy.deepcopy(self.state)
        for contents in (None, "not JSON", '{"files": {}}', "null"):
            with self.subTest(contents=contents):
                if contents is None:
                    path.unlink()
                else:
                    path.write_text(contents)
                self.assertIsNone(recovery.proof(self.state, self.workspace, self.fresh))
                self.assertEqual(before, self.state)

    def test_legacy_binding_never_invents_identity_from_missing_or_corrupt_snapshot(self):
        path = self.run / "legacy.before.json"
        for contents in (None, "not JSON", "null", '{"files": {}}'):
            with self.subTest(contents=contents):
                if contents is not None:
                    path.write_text(contents)
                record = {"before_ref": str(path)}
                self.assertIsNone(launch_binding.preserved_launch_binding(record))
                self.assertEqual({"before_ref": str(path)}, record)

    def test_decision_is_independent_of_provider_and_review_role(self):
        for stage, planning in (
            ("tester", False),
            ("review_checkpoint", False),
            ("review_completion", False),
            ("draft_plan", True),
        ):
            for engine, model in (
                ("codex", "model-a"),
                ("opencode", "different-provider/model-b"),
                ("claude", "model-c"),
            ):
                with self.subTest(stage=stage, engine=engine):
                    state = copy.deepcopy(self.state)
                    state["settings"].update(engine=engine, roles={"reviewer": {"model": model}})
                    record = state["active_stage"]
                    record.update(stage=stage, planning=planning)
                    before = util.read(Path(record["before_ref"]))
                    handoff.capture_launch_binding(state, record, before)
                    saved = copy.deepcopy(state)
                    self.assertIsNotNone(recovery.proof(state, self.workspace, self.fresh))
                    self.assertEqual(saved, state, "the proof is read-only")

    def test_unknown_cleanup_writers_drift_and_exhaustion_never_authorize_a_retry(self):
        for fault in (
            "cleanup",
            "live",
            "unknown",
            "nonce",
            "writer",
            "repair",
            "finished",
            "settings",
            "task",
            "contract",
            "source",
            "budget",
            "pause",
        ):
            with self.subTest(fault=fault):
                state, fresh = copy.deepcopy(self.state), copy.deepcopy(self.fresh)
                record = state["active_stage"]
                if fault == "cleanup":
                    fresh["receipt"]["cleanup_error"] = "not proven"
                elif fault == "live":
                    fresh["provider"]["alive"] = True
                elif fault == "unknown":
                    fresh["keeper"] = {"checked": False, "alive": None}
                elif fault == "nonce":
                    fresh["receipt"]["nonce"] = "b" * 32
                elif fault == "writer":
                    record["stage"] = "builder"
                elif fault == "repair":
                    record["report_only"] = True
                elif fault == "finished":
                    record["finished_at"] = "saved"
                elif fault == "settings":
                    state["settings"]["roles"] = {"reviewer": {"model": "changed"}}
                elif fault == "task":
                    state["current_task"]["id"] = "changed"
                elif fault == "contract":
                    state["goal_contract"]["hash"] = "changed"
                elif fault == "budget":
                    state["automatic_recoveries_since_resume"] = 3
                elif fault == "pause":
                    state["pause_intent"] = {"requested_at": "now"}
                elif fault == "source":
                    (self.workspace / "app.py").write_text("value = 2\n")
                before = copy.deepcopy(state)
                try:
                    self.assertIsNone(recovery.proof(state, self.workspace, fresh))
                    self.assertEqual(before, state)
                finally:
                    (self.workspace / "app.py").write_text("value = 1\n")

    def test_legacy_uncertain_checkpoint_names_the_same_bound_metadata_action(self):
        self.state["status"] = "PAUSED_UNCERTAIN_STAGE"
        before = copy.deepcopy(self.state)
        view = run_view.view(
            self.state, run_dir=self.run, workspace=self.workspace, liveness=self.fresh, controller={"kind": "boundary"}
        )
        self.assertIn("--reconcile-only", view["next_command"])
        self.assertEqual("continue", view["needs"]["kind"])
        self.assertEqual(before, self.state)
        self.reconcile()
        self.assertEqual("RUNNING", self.state["status"])

    def test_pending_pause_is_kept_without_archiving_or_spending(self):
        (self.run / "pause-requested").write_text("pause")
        before = copy.deepcopy(self.state)
        self.assertFalse(recovery.settle(runner, self.state, self.run, self.workspace, self.fresh))
        self.assertEqual(before, self.state)
