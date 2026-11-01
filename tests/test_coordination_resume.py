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

    def test_preserving_a_finite_cap_does_not_replenish_planning(self):
        offer = self.offered()
        self.assertEqual("coordination_resume", offer["kind"])
        code, result = self.cli(offer["token"], 12)
        self.assertEqual(0, code, result)
        saved = store.read(self.anchor)
        self.assertEqual(12, saved["budget"]["cap_usd"])
        with self.assertRaisesRegex(store.CoordinationError, "planning allowance"):
            budget.envelope(saved["budget"], [self.view], self.member["run_dir"])

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
