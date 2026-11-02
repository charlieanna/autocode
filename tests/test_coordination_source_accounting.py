"""Conversation calls reuse the source ledger without spending its baseline twice."""

import copy
import unittest
from unittest.mock import patch

import autocode_coordinate as coordinate
import autocode_coordination_budget as budget
import autocode_coordination_planning_limits as planning_limits
import autocode_coordination_store as store
import autocode_coordination_usage as usage


def view(run, amount, *, complete=True):
    return {
        "run_dir": run,
        "workspace": "/workspace",
        "status": "WAITING_FOR_USER",
        "next_stage": "requirements_gather",
        "cost": {
            "cap_usd": 30,
            "spent_usd": amount,
            "complete": complete,
            "breakdown": {"by_stage": [{"stage": "requirements_gather", "spent_usd": amount, "complete": complete}]},
        },
    }


class SourceAccountingTests(unittest.TestCase):
    def test_planning_reallocation_does_not_stop_early_from_duplicate_source_cost(self):
        saved = {**budget.initial(view("source", 1)), "source_run_dir": "source"}
        document = {"anchor": "source", "budget": saved, "members": [], "phase": "fixing"}
        observed = [view("source", 6), view("member", 2)]
        rows = [
            {
                "run_dir": item["run_dir"],
                "workspace": item["workspace"],
                "view": {**item, "needs": {"kind": "continue"}},
            }
            for item in observed
        ]
        with (
            patch.object(store, "cancelled", return_value=False),
            patch.object(planning_limits.repositories, "fingerprint", return_value="source"),
        ):
            self.assertIsNone(planning_limits.offer(document, rows, observed))

    def test_source_continuation_receives_only_remaining_whole_task_capacity(self):
        saved = {**budget.initial(view("source", 1)), "source_run_dir": "source"}
        saved.pop("planning_allowance")
        self.assertEqual(
            ["--max-cost-usd", "28"],
            budget.envelope(saved, [view("source", 27), view("member", 2)], "source")["options"],
        )
        with self.assertRaisesRegex(store.CoordinationError, "exhausted"):
            budget.envelope(saved, [view("source", 28), view("member", 2)], "source")

    def test_initial_budget_remembers_source_identity_without_changing_original_accounting(self):
        saved = budget.initial(view("source", 1))
        self.assertEqual("source", saved.get("source_run_dir"))
        self.assertEqual(1, saved["initial_spent_usd"])
        self.assertEqual(1, saved["planning_allowance"]["initial"]["spent_usd"])

    def test_live_source_replaces_its_frozen_snapshot_in_total_and_planning_envelopes(self):
        saved = {**budget.initial(view("source", 1)), "source_run_dir": "source"}
        before = copy.deepcopy(saved)
        observed = [view("source", 3), view("member", 2)]
        self.assertEqual(5, budget.accounting(saved, observed)["known_spent_usd"])
        self.assertEqual(7, float(budget.envelope(saved, observed, "source")["options"][1]))
        self.assertEqual(6, float(budget.envelope(saved, observed, "member")["options"][1]))
        self.assertEqual(4, float(budget.envelope(saved, observed)["options"][1]))
        self.assertEqual(before, saved)
        self.assertEqual(3, budget.accounting(saved, [view("member", 2)])["known_spent_usd"])

    def test_source_unknown_cost_and_exhausted_planning_remain_explicit(self):
        saved = {**budget.initial(view("source", 1)), "source_run_dir": "source"}
        unknown = [view("source", 3, complete=False), view("member", 2)]
        self.assertFalse(budget.accounting(saved, unknown)["complete"])
        with self.assertRaisesRegex(store.CoordinationError, "unpriced|incomplete"):
            budget.envelope(saved, unknown, "source")
        with self.assertRaisesRegex(store.CoordinationError, "planning allowance"):
            budget.envelope(saved, [view("source", 7), view("member", 2)], "source")
        saved["cap_usd"] = 0
        self.assertEqual(["--max-cost-usd", "0"], budget.envelope(saved, unknown, "source")["options"])
        self.assertFalse(budget.envelope(saved, unknown, "source")["complete"])

    def test_coordinator_observes_the_source_as_an_active_budget_participant_and_meter_deduplicates_it(self):
        source = view("source", 3)
        member = view("member", 2)
        document = {
            "anchor": "source",
            "budget": {"source_run_dir": "source"},
            "members": [{"target": "repo", "workspace": "/member", "run_dir": "member", "started": True}],
            "joint_attempts": [],
        }
        with patch.object(coordinate, "anchor_view", return_value=source):
            rows, history = coordinate.budget_observations(document, {"repo": member})
        self.assertEqual({"source", "member"}, {row["run_dir"] for row in rows})
        self.assertEqual([member, source], history)
        self.assertEqual(2, len(usage.snapshot("source", [source, *history])["runs"]))
        legacy = {**document, "budget": {}}
        with patch.object(coordinate, "anchor_view") as read:
            _, history = coordinate.budget_observations(legacy, {"repo": member})
        read.assert_not_called()
        self.assertEqual([member], history)


if __name__ == "__main__":
    unittest.main()
