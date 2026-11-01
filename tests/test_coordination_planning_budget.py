"""Explicit planning reallocations preserve the task cap and all prior work."""

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
import autocode_coordination_store as store


class PlanningBudgetRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.enterContext(patch.dict(os.environ, {"AUTOCODE_HOME": str(self.root / "control")}))
        self.anchor = self.root / "anchor/.autocode/runs/chat"
        self.members = [
            {
                "target": name,
                "workspace": str(self.root / name),
                "run_dir": str(self.root / name / ".autocode/runs/fix"),
                "base": "pinned",
                "started": True,
                "branch": name,
                "repository": ["fixture", name],
            }
            for name in ("api", "history")
        ]
        self.views = {
            row["target"]: {
                "workspace": row["workspace"],
                "run_dir": row["run_dir"],
                "status": "RUNNING",
                "needs": {"kind": "continue"},
                "next_stage": "astra_discovery",
                "turn": 1,
                "iteration": 1,
                "scope": {"identity": "current-scope"},
                "related_profile": {"settings": {"roles": {"glm": "chosen"}}},
                "cost": {"spent_usd": 2, "complete": True},
                "usage": {"accounting": {"attempts": [{"identity": row["target"], "active": False}]}},
            }
            for row in self.members
        }
        self.document = {
            "version": 1,
            "id": "planning-reallocation",
            "anchor": str(self.anchor),
            "phase": "discovering",
            "task": "Fix history",
            "initial_scope": [],
            "choices": {},
            "members": self.members,
            "joint_attempts": [],
            "source_catalog": {"snapshot": "pinned"},
            "budget": {
                "cap_usd": 12,
                "initial_spent_usd": 1,
                "initial_complete": True,
                "planning_allowance": {"cap_usd": 3.6, "initial": {"spent_usd": 1, "complete": True}},
            },
        }
        store.save(self.document)
        self.enterContext(patch.object(coordinate, "observe", side_effect=lambda *_a, **_k: copy.deepcopy(self.views)))
        self.enterContext(patch.object(coordinate.repos, "source_snapshot", return_value={"snapshot": "pinned"}))
        self.enterContext(patch.object(coordinate.repos, "fingerprint", return_value="source-one"))
        self.enterContext(patch.object(coordinate, "resolve_request", return_value=None))
        self.worker = Mock()
        self.enterContext(patch.object(coordinate, "handle", return_value=self.worker))

    def status(self):
        return coordinate.status(self.anchor)

    def cli(self, token, amount):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = coordinate.cli(
                [
                    "planning-budget",
                    "--run-dir",
                    str(self.anchor),
                    "--token",
                    token,
                    "--max-planning-cost-usd",
                    str(amount),
                    "--json",
                ]
            )
        return code, json.loads(output.getvalue())

    def test_offer_and_exact_reallocation_preserve_spend_routes_approval_and_task_cap(self):
        before = store.read(self.anchor)
        views = copy.deepcopy(self.views)
        offer = self.status()["needs"]
        self.assertEqual("planning_budget", offer["kind"])
        self.assertEqual(
            (3.6, 5, 12), (offer["planning_cap_usd"], offer["planning_spent_usd"], offer["maximum_cap_usd"])
        )
        self.assertIsNone(self.status()["next_command"])
        coordinate.advance(self.anchor)
        self.worker.advance.assert_not_called()
        self.assertEqual(before, store.read(self.anchor))
        code, result = self.cli(offer["token"], 9)
        self.assertEqual(0, code, result)
        after = store.read(self.anchor)
        self.assertEqual(12, after["budget"]["cap_usd"])
        allowance = after["budget"]["planning_allowance"]
        self.assertEqual(9, allowance["cap_usd"])
        self.assertEqual(before["budget"]["planning_allowance"]["initial"], allowance["initial"])
        self.assertEqual(3.6, allowance["revisions"][0]["previous_cap_usd"])
        self.assertEqual(5, allowance["revisions"][0]["known_spent_usd"])
        self.assertEqual(offer["token"], allowance["revisions"][0]["token"])
        self.assertEqual(views, self.views)
        self.assertNotIn("approved_contracts", after)
        self.worker.resume_paused.assert_not_called()
        self.worker.advance.assert_not_called()
        self.assertEqual("advance", result["needs"]["kind"])
        self.assertEqual(2, self.cli(offer["token"], 9)[0])
        self.assertEqual(after, store.read(self.anchor), "Duplicate recovery must not grant another allocation")

    def test_invalid_amounts_and_stale_accounting_sources_scope_routes_or_attempts_refuse(self):
        offer = self.status()["needs"]
        self.assertEqual("planning_budget", offer["kind"])
        before = store.read(self.anchor)
        for value in (0, -1, 3.6, 5, 12.01, "nan", "inf"):
            with self.subTest(value=value):
                self.assertEqual(2, self.cli(offer["token"], value)[0])
                self.assertEqual(before, store.read(self.anchor))
        for field in ("cost", "scope", "related_profile", "usage"):
            with self.subTest(field=field):
                saved = copy.deepcopy(self.views)
                if field == "usage":
                    self.views["api"][field]["accounting"]["attempts"].append({"identity": "new-attempt"})
                else:
                    self.views["api"][field]["changed"] = True
                self.assertEqual(2, self.cli(offer["token"], 9)[0])
                self.assertEqual(before, store.read(self.anchor))
                self.views = saved
        with patch.object(coordinate.repos, "source_snapshot", return_value={"snapshot": "changed"}):
            self.assertEqual(2, self.cli(offer["token"], 9)[0])
        self.worker.advance.assert_not_called()

    def test_active_unpriced_uncertain_cancelled_and_pending_work_has_no_reallocation(self):
        baseline = copy.deepcopy(self.views)
        for update in (
            {"liveness": {"kind": "supervised"}},
            {"usage": {"accounting": {"attempts": [{"active": True}]}}},
            {"cost": {"spent_usd": 2, "complete": False}},
            {"needs": {"kind": "inspect"}},
            {"needs": {"kind": "answer", "resolver_scope": "blocker", "questions": [{"id": "Q1"}]}},
        ):
            with self.subTest(update=update):
                self.views = copy.deepcopy(baseline)
                self.views["api"].update(update)
                self.assertNotEqual("planning_budget", self.status()["needs"]["kind"])
        self.views = baseline
        for field, value in (
            ("pending", {"operation": "start", "target": "api"}),
            ("rework", {"targets": ["api"]}),
            ("phase", "cancelled"),
        ):
            with self.subTest(field=field):
                document = copy.deepcopy(self.document)
                document[field] = value
                store.save(document)
                self.assertNotEqual("planning_budget", self.status()["needs"]["kind"])

    def test_exact_unanswered_scope_is_preserved_and_nonplanning_spend_reduces_maximum(self):
        self.views["history"]["needs"] = {
            "kind": "answer",
            "resolver_scope": "clarification",
            "questions": [{"id": "workspace-scope-current-scope"}],
        }
        self.views["history"]["cost"] = {
            "spent_usd": 4,
            "complete": True,
            "breakdown": {
                "by_stage": [
                    {"stage": "astra_discovery", "spent_usd": 2, "complete": True},
                    {"stage": "terra", "spent_usd": 2, "complete": True},
                ]
            },
        }
        before = copy.deepcopy(self.views)
        need = self.status()["needs"]
        self.assertEqual("planning_budget", need["kind"])
        self.assertEqual(10, need["maximum_cap_usd"])
        self.assertEqual(2, self.cli(need["token"], 11)[0])
        self.assertEqual(0, self.cli(need["token"], 9)[0])
        self.assertEqual(before, self.views)

    def test_reopened_allocation_stops_again_and_cannot_be_renewed_by_advance(self):
        offer = self.status()["needs"]
        self.assertEqual("planning_budget", offer["kind"])
        self.assertEqual(0, self.cli(offer["token"], 9)[0])
        self.views["api"]["cost"]["spent_usd"] = 6
        held = self.status()
        self.assertEqual("planning_budget", held["needs"]["kind"])
        self.assertEqual((9, 9), (held["needs"]["planning_cap_usd"], held["needs"]["planning_spent_usd"]))
        saved = store.read(self.anchor)
        coordinate.advance(self.anchor)
        self.assertEqual(saved, store.read(self.anchor))
        self.worker.advance.assert_not_called()
        self.assertEqual(2, self.cli(offer["token"], 12)[0])

    def test_changed_worktree_bytes_or_saved_checkpoint_refuses_but_poll_clock_does_not(self):
        self.views["api"]["observation"] = {"checkpoint_token": "saved-one", "sampled_at": "before"}
        offered = self.status()["needs"]
        self.views["api"]["observation"]["sampled_at"] = "after"
        self.assertEqual(offered["token"], self.status()["needs"]["token"])
        with patch.object(coordinate.repos, "fingerprint", return_value="source-two"):
            self.assertEqual(2, self.cli(offered["token"], 9)[0])
        self.views["api"]["observation"]["checkpoint_token"] = "saved-two"
        self.assertEqual(2, self.cli(offered["token"], 9)[0])


if __name__ == "__main__":
    unittest.main()
