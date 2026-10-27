"""Shared-cap recovery through the public coordinator CLI; no provider traffic."""

import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import autocode_client_actions as actions
import autocode_coordinate as coordinate
import autocode_coordination_budget as budget
import autocode_coordination_limits as limits
import autocode_coordination_store as store


class SharedBudgetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        environment = patch.dict(os.environ, {"AUTOCODE_HOME": str(self.root / "catalog")})
        environment.start()
        self.addCleanup(environment.stop)
        self.anchor = self.root / "project/.autocode/runs/anchor"
        self.members = [
            {
                "target": name,
                "workspace": str(self.root / name),
                "run_dir": str(self.root / name / ".autocode/runs/fix"),
                "base": "original",
                "started": True,
            }
            for name in ("checkout", "history")
        ]
        own, other = self.members
        place = f"--workspace {own['workspace']} --run-dir {own['run_dir']}"
        row = {
            "id": "max_cost_usd",
            "kind": "integer",
            "placeholder": "N",
            "flag": "--max-cost-usd",
            "arg_index": 2,
            "label": "Cost cap (whole USD)",
            "current": 2.2,
            "used": 2.0,
            "minimum": 3,
            "maximum": actions.MAX_SAFE_INTEGER,
            "command": f"AUTOCODE_EXPECTED_RECOVERY_TOKEN=checkpoint autocode {place} "
            "--resume-paused --max-cost-usd N --expected-recovery-token -",
        }
        self.views = {
            "checkout": {
                "workspace": own["workspace"],
                "run_dir": own["run_dir"],
                "status": "WAITING_FOR_USER",
                "done": False,
                "needs": {"kind": "answer", "command_inputs": [row]},
                "recovery": {"cause": "PAUSED_COST_LIMIT", "token": "checkpoint"},
                "cost": {"cap_usd": 2.2, "spent_usd": 2.0, "complete": True},
                "routes": {"terra": "chosen-builder", "sol": "independent-tester"},
            },
            "history": {
                "workspace": other["workspace"],
                "run_dir": other["run_dir"],
                "status": "AWAITING_GOAL_APPROVAL",
                "done": False,
                "needs": {"kind": "approve_plan"},
                "cost": {"spent_usd": 7.0, "complete": False},
            },
        }
        self.document = {
            "version": 1,
            "id": "shared-budget-fixture",
            "anchor": str(self.anchor),
            "phase": "fixing",
            "task": "Fix the total",
            "initial_scope": [],
            "choices": {},
            "members": self.members,
            "joint_attempts": [],
            "budget": {"cap_usd": 10, "initial_spent_usd": 0.5},
        }
        store.save(self.document)
        observed = patch.object(coordinate, "observe", side_effect=lambda *_a, **_k: copy.deepcopy(self.views))
        observed.start()
        self.addCleanup(observed.stop)
        self.run = Mock()
        self.run.env = {}
        self.run.status.return_value = copy.deepcopy(self.views["checkout"])
        self.run.resume_paused.side_effect = self.resumed
        handles = patch.object(coordinate, "handle", return_value=self.run)
        handles.start()
        self.addCleanup(handles.stop)

    def resumed(self):
        options = list(self.run.options)
        self.views["checkout"]["cost"]["cap_usd"] = float(options[options.index("--max-cost-usd") + 1])
        self.views["checkout"].update(status="RUNNING", needs={"kind": "continue"}, recovery=None)

    def cli(self, cap, token):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = coordinate.cli(
                ["budget", "--run-dir", str(self.anchor), "--max-cost-usd", str(cap), "--token", token, "--json"]
            )
        return code, json.loads(output.getvalue())

    def offered(self):
        return coordinate.status(self.anchor)["needs"]

    def test_public_cli_revises_shared_cap_and_fences_exactly_one_member_resume(self):
        before = copy.deepcopy(self.views)
        offer = self.offered()
        self.assertEqual(
            ("budget", 9.5, False, 10.5),
            (offer["kind"], offer["known_spent_usd"], offer["complete"], offer["minimum_cap_usd"]),
        )
        code, result = self.cli(15, offer["token"])
        self.assertEqual(0, code, result)
        saved = store.read(self.anchor)
        self.assertEqual(15, saved["budget"]["cap_usd"])
        self.assertEqual(0.5, saved["budget"]["initial_spent_usd"])
        self.assertEqual(9.5, saved["budget"]["revisions"][0]["known_spent_usd"])
        self.assertFalse(saved["budget"]["revisions"][0]["accounting_complete"])
        self.run.resume_paused.assert_called_once_with()
        self.assertEqual(("--max-cost-usd", "7.5", "--expected-recovery-token", "-"), self.run.options)
        self.assertEqual({actions.TOKEN_ENV: "checkpoint"}, self.run.env)
        self.assertEqual(before["history"], self.views["history"])
        self.assertEqual(before["checkout"]["routes"], self.views["checkout"]["routes"])
        self.assertEqual(before["checkout"]["cost"]["spent_usd"], self.views["checkout"]["cost"]["spent_usd"])
        self.assertIsNone(saved.get("pending"))
        self.assertNotEqual("budget", result["needs"]["kind"])
        self.assertEqual(2, self.cli(20, offer["token"])[0])
        self.run.resume_paused.assert_called_once()

    def test_status_and_invalid_caps_never_mutate_or_dispatch(self):
        before = store.read(self.anchor)
        offer = self.offered()
        self.assertEqual(before, store.read(self.anchor))
        for cap in (0, -1, 10, 10.49, "nan", "inf", 1e30):
            with self.subTest(cap=cap):
                self.assertEqual(2, self.cli(cap, offer["token"])[0])
                self.assertEqual(before, store.read(self.anchor))
                self.run.resume_paused.assert_not_called()

    def test_stale_accounting_recovery_token_and_revision_are_refused(self):
        for change in ("spend", "token", "revision", "unknown_attempt"):
            with self.subTest(change=change):
                baseline = copy.deepcopy(self.views)
                store.save(copy.deepcopy(self.document))
                offer = self.offered()
                if change == "spend":
                    self.views["history"]["cost"]["spent_usd"] += 0.1
                elif change == "token":
                    self.views["checkout"]["recovery"]["token"] = "new-checkpoint"
                elif change == "unknown_attempt":
                    self.views["history"]["cost"]["unpriced_stages"] = 2
                else:
                    saved = store.read(self.anchor)
                    store.save(saved)
                self.assertEqual(2, self.cli(15, offer["token"])[0])
                self.assertEqual(10, store.read(self.anchor)["budget"]["cap_usd"])
                self.run.resume_paused.assert_not_called()
                self.views = baseline

    def test_active_or_cancelled_coordination_cannot_take_a_budget_offer(self):
        offer = self.offered()
        self.views["history"]["liveness"] = {"kind": "supervised"}
        self.assertNotEqual("budget", self.offered()["kind"])
        self.assertEqual(2, self.cli(15, offer["token"])[0])
        del self.views["history"]["liveness"]
        coordinate.cancel(self.anchor)
        self.assertEqual(2, self.cli(15, offer["token"])[0])
        self.assertEqual(10, store.read(self.anchor)["budget"]["cap_usd"])
        self.run.resume_paused.assert_not_called()

    def test_failed_resume_retains_intent_and_reopening_never_replays_it(self):
        offer = self.offered()
        self.run.resume_paused.side_effect = RuntimeError("lost response")
        self.assertEqual(2, self.cli(15, offer["token"])[0])
        saved = store.read(self.anchor)
        self.assertEqual("budget_resume", saved["pending"]["operation"])
        self.assertEqual(15, saved["budget"]["cap_usd"])
        self.assertEqual("reconcile", self.offered()["kind"])
        with self.assertRaisesRegex(store.CoordinationError, "not replayed"):
            coordinate.advance(self.anchor)
        self.assertEqual(2, self.cli(20, offer["token"])[0])
        self.run.resume_paused.assert_called_once()

    def test_prior_joint_and_failed_verifier_spend_are_retained(self):
        saved = store.read(self.anchor)
        saved["joint_attempts"] = [
            {
                "started": True,
                "run_dir": str(self.root / "old-joint"),
                "workspace": str(self.root),
                "verification": {
                    "record": "old-failed-verification",
                    "evidence_report": {
                        "document": {
                            "attempts": [{"reported_cost_usd": 0.3}, {"historical_estimated_cost_usd": 0.2}, {}]
                        }
                    },
                },
            }
        ]
        store.save(saved)
        self.run.status.return_value = {
            "run_dir": str(self.root / "old-joint"),
            "done": True,
            "cost": {"spent_usd": 0.5, "complete": True},
        }
        offer = self.offered()
        self.assertEqual((10.5, 11.5, False), (offer["known_spent_usd"], offer["minimum_cap_usd"], offer["complete"]))
        self.assertEqual(0, self.cli(15, offer["token"])[0])
        self.assertEqual("6.5", self.run.options[1])
        self.assertEqual(saved["joint_attempts"], store.read(self.anchor)["joint_attempts"])

    def test_exhausted_shared_budget_without_child_pause_changes_no_task(self):
        self.views["checkout"].update(status="RUNNING", needs={"kind": "continue"}, recovery=None)
        self.views["history"]["cost"]["spent_usd"] = 8
        offer = self.offered()
        self.assertEqual(("budget", None, 10.5), (offer["kind"], offer["target"], offer["known_spent_usd"]))
        self.assertEqual(0, self.cli(15, offer["token"])[0])
        self.run.resume_paused.assert_not_called()
        self.assertEqual("advance", self.offered()["kind"])

    def test_unlimited_boolean_and_nonfinite_recovery_amounts_are_refused(self):
        for cap in (True, False, 0, float("nan"), float("inf")):
            with self.subTest(cap=cap), self.assertRaises(store.CoordinationError):
                limits.amount(cap, 1)

    def test_unrelated_uncertain_gate_does_not_grant_budget_recovery(self):
        offer = self.offered()
        self.views["history"].update(status="PAUSED_OWNERSHIP", needs={"kind": "inspect"})
        self.assertNotEqual("budget", self.offered()["kind"])
        self.assertEqual(2, self.cli(15, offer["token"])[0])
        self.run.resume_paused.assert_not_called()

    def test_existing_shared_allowance_can_be_reasserted_without_resetting_spend(self):
        saved = store.read(self.anchor)
        saved["budget"]["cap_usd"] = 15
        store.save(saved)
        offer = self.offered()
        self.assertEqual(15, offer["minimum_cap_usd"])
        self.assertEqual(0, self.cli(15, offer["token"])[0])
        self.assertEqual(9.5, store.read(self.anchor)["budget"]["revisions"][0]["known_spent_usd"])

    def test_unpriced_anchor_and_legacy_coverage_remain_explicitly_incomplete(self):
        priced = [{"cost": {"spent_usd": 1, "complete": True}}]
        anchor = budget.initial({"cost": {"spent_usd": 0.5, "cap_usd": 10, "complete": False}})
        self.assertFalse(budget.accounting(anchor, priced)["complete"])
        anchor["initial_complete"] = True
        self.assertTrue(budget.accounting(anchor, priced)["complete"])
        del anchor["initial_complete"]
        self.assertFalse(budget.accounting(anchor, priced)["complete"])

    def test_a_peers_unanswered_requirements_question_is_retained_not_answered(self):
        self.views["history"].update(status="WAITING_FOR_USER", needs={"kind": "answer", "questions": ["Q1"]})
        before = copy.deepcopy(self.views["history"])
        offer = self.offered()
        self.assertEqual("budget", offer["kind"])
        self.assertEqual(0, self.cli(15, offer["token"])[0])
        self.assertEqual(before, self.views["history"])


if __name__ == "__main__":
    unittest.main()
