"""Explicit cancelled-coordination recovery preserves work and budget history."""

import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import autocode_coordinate as coordinate
import autocode_coordination_budget as budget
import autocode_coordination_store as store
import autocode_util as util


class CoordinationResumeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.enterContext(patch.dict(os.environ, {"AUTOCODE_HOME": str(self.root / "control")}))
        self.anchor = self.root / "anchor/.autocode/runs/chat"
        self.member = {
            "target": "api",
            "workspace": str(self.root / "api"),
            "run_dir": str(self.root / "api/.autocode/runs/fix"),
            "base": "pinned",
            "started": True,
            "branch": "fixture",
            "repository": ["fixture", "api"],
        }
        self.view = {
            **{k: self.member[k] for k in ("workspace", "run_dir")},
            "status": "PAUSED_INTERVENTION",
            "needs": {"kind": "resume"},
            "recovery": {"cause": "PAUSED_INTERVENTION", "token": "child-token"},
            "next_stage": "glm_revise",
            "turn": 1,
            "iteration": 1,
            "cost": {"spent_usd": 4, "complete": True},
            "usage": {"accounting": {"attempts": [{"identity": "retained", "active": False}]}},
            "scope": {"identity": "same"},
            "routes": {"glm": "chosen"},
        }
        self.document = {
            "version": 1,
            "id": "cancel-recovery",
            "anchor": str(self.anchor),
            "phase": "fixing",
            "task": "Fix history",
            "initial_scope": [],
            "choices": {},
            "members": [self.member],
            "joint_attempts": [],
            "source_catalog": {"snapshot": "same"},
            "budget": {
                "cap_usd": 12,
                "initial_spent_usd": 1,
                "initial_complete": True,
                "planning_allowance": {"cap_usd": 3.6, "initial": {"spent_usd": 1, "complete": True}},
            },
        }
        store.save(self.document)
        self.cancel = store.root(self.anchor) / "cancel-request.json"
        util.atomic_json(self.cancel, {"requested_at": "original-cancellation"})
        self.enterContext(
            patch.object(coordinate, "observe", side_effect=lambda *a, **k: {"api": copy.deepcopy(self.view)})
        )
        self.enterContext(patch.object(coordinate.repos, "source_snapshot", return_value={"snapshot": "same"}))
        self.fingerprint = self.enterContext(patch.object(coordinate.repos, "fingerprint", return_value="source-one"))
        self.enterContext(patch.object(coordinate, "resolve_request", return_value=None))
        self.enterContext(
            patch.object(
                coordinate,
                "anchor_view",
                return_value={"workspace": str(self.anchor.parents[2]), "run_dir": str(self.anchor)},
            )
        )
        self.worker = Mock()
        self.enterContext(patch.object(coordinate, "handle", return_value=self.worker))

    def offered(self):
        return coordinate.status(self.anchor)["needs"]

    def cli(self, token, cap):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = coordinate.cli(
                ["resume", "--run-dir", str(self.anchor), "--token", token, "--max-cost-usd", str(cap), "--json"]
            )
        return code, json.loads(out.getvalue())

    def test_explicit_uncapped_recovery_retains_work_and_launches_nothing(self):
        before = store.read(self.anchor)
        original_cancel = self.cancel.read_bytes()
        offer = self.offered()
        self.assertEqual("coordination_resume", offer["kind"])
        self.assertTrue(offer["allows_no_cap"])
        self.assertEqual(12, offer["current_cap_usd"])
        self.assertEqual(before, store.read(self.anchor), "Status is read-only")
        code, result = self.cli(offer["token"], 0)
        self.assertEqual(0, code, result)
        after = store.read(self.anchor)
        self.assertFalse(store.cancelled(self.anchor))
        self.assertEqual(original_cancel, self.cancel.read_bytes(), "Keep the cancellation evidence")
        self.assertEqual(0, after["budget"]["cap_usd"])
        self.assertEqual(before["budget"]["planning_allowance"], after["budget"]["planning_allowance"])
        self.assertEqual(before["members"], after["members"])
        self.assertEqual(12, after["budget"]["revisions"][-1]["previous_cap_usd"])
        self.assertEqual(offer["token"], after["resumptions"][-1]["token"])
        self.assertEqual(
            ["--max-cost-usd", "0"], budget.envelope(after["budget"], [self.view], self.member["run_dir"])["options"]
        )
        self.assertIsNone(budget.planning_ceiling(after["budget"], [self.view], self.member["run_dir"]))
        self.worker.advance.assert_not_called()
        self.worker.resume_paused.assert_not_called()
        self.assertEqual(2, self.cli(offer["token"], 0)[0])
        self.assertEqual(after, store.read(self.anchor))

    def clarification(self):
        self.view.update(
            status="WAITING_FOR_USER",
            needs={
                "kind": "answer",
                "resolver_scope": "clarification",
                "resolver_request_id": "question-request",
                "resolver_token": "question-token",
                "questions": [{"id": "Q2", "question": "Where are the exact pinned dependencies?"}],
            },
            controller={"kind": "boundary", "checked": True, "alive": False},
            recovery=None,
        )

    def test_cancel_recovery_preserves_a_settled_unanswered_clarification(self):
        self.clarification()
        before = copy.deepcopy(self.view)
        offer = self.offered()
        self.assertEqual("coordination_resume", offer["kind"])
        code, result = self.cli(offer["token"], 0)
        self.assertEqual(0, code, result)
        self.assertEqual("input", result["needs"]["kind"])
        self.assertEqual(before, result["needs"]["view"])
        self.worker.advance.assert_not_called()
        self.worker.resume_paused.assert_not_called()
        self.worker.answer.assert_not_called()

    def test_question_recovery_requires_a_complete_settled_clarification(self):
        self.clarification()
        baseline = copy.deepcopy(self.view)
        for field, value in (
            ("status", "RUNNING"),
            ("controller", {"kind": "running", "checked": True, "alive": True}),
            ("controller", {"kind": "unknown", "checked": False, "alive": None}),
            ("liveness", {"kind": "supervised"}),
            ("usage", {"accounting": {"attempts": [{"active": True}]}}),
        ):
            with self.subTest(field=field, value=value):
                self.view = copy.deepcopy(baseline)
                self.view[field] = value
                self.assertEqual("cancelled", self.offered()["kind"])
        for field, value in (
            ("resolver_scope", "recovery"),
            ("resolver_request_id", ""),
            ("resolver_token", ""),
            ("questions", []),
            ("questions", [{"id": "Q2", "question": ""}]),
        ):
            with self.subTest(field=field):
                self.view = copy.deepcopy(baseline)
                self.view["needs"][field] = value
                self.assertEqual("cancelled", self.offered()["kind"])

    def test_changed_question_refuses_the_previous_cancel_recovery_token(self):
        self.clarification()
        offer = self.offered()
        self.assertEqual("coordination_resume", offer["kind"])
        self.view["needs"]["questions"][0]["question"] = "Which pinned version is required?"
        before = store.read(self.anchor)
        self.assertEqual(2, self.cli(offer["token"], 0)[0])
        self.assertEqual(before, store.read(self.anchor))
        self.assertTrue(store.cancelled(self.anchor))

    def test_preserving_a_finite_cap_does_not_replenish_planning(self):
        offer = self.offered()
        self.assertEqual("coordination_resume", offer["kind"])
        code, result = self.cli(offer["token"], 12)
        self.assertEqual(0, code, result)
        saved = store.read(self.anchor)
        self.assertEqual(12, saved["budget"]["cap_usd"])
        with self.assertRaisesRegex(store.CoordinationError, "planning allowance"):
            budget.envelope(saved["budget"], [self.view], self.member["run_dir"])

    def test_public_null_cap_recovers_unknown_spend_only_with_explicit_zero(self):
        saved = copy.deepcopy(self.document)
        saved["budget"] = budget.initial({"cost": {"cap_usd": None, "spent_usd": 1, "complete": False}})
        store.save(saved)
        self.view["cost"]["complete"] = False
        offer = self.offered()
        self.assertEqual("coordination_resume", offer["kind"])
        self.assertFalse(offer["complete"])
        self.assertEqual(5, offer["known_spent_usd"])
        self.assertEqual(0, offer["current_cap_usd"])
        self.assertEqual(2, self.cli(offer["token"], 12)[0])
        self.assertEqual("coordination_resume", self.offered()["kind"])

        code, result = self.cli(offer["token"], 0)
        self.assertEqual(0, code, result)
        member = next(row for row in result["accounting"]["runs"] if row["run_dir"] == self.member["run_dir"])
        self.assertEqual(4, member["cost"]["spent_usd"])
        self.assertFalse(member["cost"]["complete"])
        self.worker.advance.assert_not_called()
        self.worker.resume_paused.assert_not_called()

    def test_uncapped_recovery_preserves_unknown_cost_and_a_reconciled_retry_hold(self):
        saved = store.read(self.anchor)
        saved["budget"]["cap_usd"] = 0
        store.save(saved)
        self.view.update(
            status="PAUSED_STAGE_ABANDONED",
            needs={"kind": "retry_job", "attempt_id": "001/tester-01", "job_retry_token": "exact-retry"},
            recovery={"cause": "PAUSED_STAGE_ABANDONED"},
            cost={"spent_usd": 4, "complete": False},
            liveness={role: {"alive": False} for role in ("owner", "keeper", "provider")},
        )
        before = copy.deepcopy(self.view)
        offer = self.offered()
        self.assertEqual("coordination_resume", offer["kind"])
        self.assertFalse(offer["complete"])
        self.assertEqual(5, offer["known_spent_usd"])
        self.assertEqual(2, self.cli(offer["token"], 12)[0], "Unknown spend cannot authorize a new finite cap")
        self.assertTrue(store.cancelled(self.anchor))
        code, result = self.cli(offer["token"], 0)
        self.assertEqual(0, code, result)
        self.assertFalse(store.cancelled(self.anchor))
        self.assertEqual(before, self.view, "Coordinator recovery must leave the child retry and pause untouched")
        revision = store.read(self.anchor)["budget"]["revisions"][-1]
        self.assertFalse(revision["accounting_complete"])
        self.assertEqual(5, revision["known_spent_usd"])
        self.worker.advance.assert_not_called()
        self.worker.resume_paused.assert_not_called()

    def test_reconciled_retry_recovery_requires_positive_process_cleanup_evidence(self):
        self.view.update(
            status="PAUSED_STAGE_ABANDONED",
            needs={"kind": "retry_job", "attempt_id": "001/tester-01", "job_retry_token": "exact-retry"},
            recovery={"cause": "PAUSED_STAGE_ABANDONED"},
            liveness={role: {"alive": False} for role in ("owner", "keeper", "provider")},
        )
        self.assertEqual("coordination_resume", self.offered()["kind"])
        for role in ("owner", "keeper", "provider"):
            for alive in (True, None):
                with self.subTest(role=role, alive=alive):
                    self.view["liveness"][role]["alive"] = alive
                    self.assertEqual("cancelled", self.offered()["kind"])
                    self.view["liveness"][role]["alive"] = False
        for field in ("attempt_id", "job_retry_token"):
            with self.subTest(missing=field):
                value = self.view["needs"].pop(field)
                self.assertEqual("cancelled", self.offered()["kind"])
                self.view["needs"][field] = value
        self.view["recovery"]["cause"] = "PAUSED_PROCESS_CLEANUP"
        self.assertEqual("cancelled", self.offered()["kind"])

    def test_archived_retry_gate_remains_settled_after_active_metadata_is_removed(self):
        archive = str(Path(self.member["run_dir"]) / "iterations/001/archived-tester")
        attempt = {
            "attempt_id": "001/tester-01",
            "run_id": self.member["run_dir"],
            "stage": "write_acceptance_tests",
            "events": archive + "/tester.jsonl",
            "active": False,
            "rejected": True,
            "finished_at": "2026-11-02T19:16:16Z",
        }
        self.view.update(
            status="PAUSED_STAGE_ABANDONED",
            needs={
                "kind": "retry_job",
                "attempt_id": "001/tester-01",
                "job_retry_token": "exact-retry",
                "stage": "write_acceptance_tests",
                "archive": archive,
                "write_diagnosis": {"original_identity_verified": True},
            },
            recovery={"cause": "PAUSED_STAGE_ABANDONED"},
            liveness={
                "kind": "unknown",
                "reason": "metadata_missing",
                **{role: {"checked": False, "alive": None} for role in ("owner", "keeper", "provider")},
            },
            usage={"accounting": {"attempts": [attempt]}},
        )
        before = copy.deepcopy(self.view)
        offer = self.offered()
        self.assertEqual("coordination_resume", offer["kind"])
        for field, value in (
            ("active", True),
            ("rejected", False),
            ("finished_at", None),
            ("attempt_id", "other"),
            ("run_id", "/other"),
            ("stage", "terra"),
            ("events", "/outside/tester.jsonl"),
        ):
            with self.subTest(field=field):
                attempt[field] = value
                self.assertEqual("cancelled", self.offered()["kind"])
                attempt[field] = before["usage"]["accounting"]["attempts"][0][field]
        for reason in ("metadata_invalid", "inspection_unavailable"):
            self.view["liveness"]["reason"] = reason
            self.assertEqual("cancelled", self.offered()["kind"])
        self.view["liveness"]["reason"] = "metadata_missing"
        self.view["liveness"]["provider"]["alive"] = True
        self.assertEqual("cancelled", self.offered()["kind"])
        self.view = before
        self.view["needs"]["write_diagnosis"]["original_identity_verified"] = False
        self.assertEqual("cancelled", self.offered()["kind"])

    def test_stale_source_routes_usage_and_new_cancellation_refuse(self):
        baseline = copy.deepcopy(self.view)
        for changed in ("source", "routes", "usage", "cancel"):
            with self.subTest(changed=changed):
                self.view = copy.deepcopy(baseline)
                self.fingerprint.return_value = "source-one"
                util.atomic_json(self.cancel, {"requested_at": "original-cancellation"})
                offer = self.offered()
                self.assertEqual("coordination_resume", offer["kind"])
                if changed == "source":
                    self.fingerprint.return_value = "source-two"
                if changed == "routes":
                    self.view["routes"] = {"glm": "different"}
                if changed == "usage":
                    self.view["cost"]["spent_usd"] += 1
                if changed == "cancel":
                    util.atomic_json(self.cancel, {"requested_at": "new-cancel"})
                before = store.read(self.anchor)
                self.assertEqual(2, self.cli(offer["token"], 0)[0])
                self.assertEqual(before, store.read(self.anchor))
                self.assertTrue(store.cancelled(self.anchor))

    def test_active_uncertain_unpriced_or_pending_work_has_no_resume_offer(self):
        for field, value in (
            ("status", "STAGE_RUNNING"),
            ("needs", {"kind": "reconcile"}),
            ("cost", {"spent_usd": 4, "complete": False}),
        ):
            with self.subTest(field=field):
                old = copy.deepcopy(self.view)
                self.view[field] = value
                self.assertEqual("cancelled", self.offered()["kind"])
                self.view = old
        self.view["usage"]["accounting"]["attempts"][0]["active"] = True
        self.assertEqual("cancelled", self.offered()["kind"])

    def test_another_cancel_after_resumption_is_not_ignored(self):
        offer = self.offered()
        self.assertEqual("coordination_resume", offer["kind"])
        self.assertEqual(0, self.cli(offer["token"], 0)[0])
        util.atomic_json(self.cancel, {"requested_at": "new-cancel"})
        self.assertTrue(store.cancelled(self.anchor))

    def test_explicit_zero_disables_planning_and_clears_inherited_child_cap(self):
        saved = copy.deepcopy(self.document["budget"])
        saved["cap_usd"] = 0
        self.assertIsNone(budget.planning_ceiling(saved, [self.view], self.member["run_dir"]))
        self.assertEqual(["--max-cost-usd", "0"], budget.envelope(saved, [self.view])["options"])

    def test_uncertain_named_recovery_is_not_a_settled_continue(self):
        self.view.update(status="RUNNING", needs={"kind": "continue", "action": "reconcile_only"})
        self.assertEqual("cancelled", self.offered()["kind"])

    def test_invalid_caps_and_changed_plan_never_recover_or_change_budget(self):
        offer = self.offered()
        for amount in (-1, 2, "nan", "inf", 1e30):
            with self.subTest(amount=amount):
                before = store.read(self.anchor)
                self.assertEqual(2, self.cli(offer["token"], amount)[0])
                self.assertEqual(before, store.read(self.anchor))
        self.view["displayed_plan"] = {"token": "new-plan"}
        self.assertEqual(2, self.cli(offer["token"], 0)[0])
        self.assertTrue(store.cancelled(self.anchor))

    def test_cancel_requests_have_distinct_identities_even_in_the_same_clock_tick(self):
        self.worker.status.return_value = self.view
        with patch.object(coordinate.util, "now", return_value="same-tick"):
            coordinate.cancel(self.anchor)
            first = self.cancel.read_bytes()
            coordinate.cancel(self.anchor)
            self.assertNotEqual(first, self.cancel.read_bytes())
