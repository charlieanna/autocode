"""Task-wide display accounting uses every distinct public ledger, never private state."""

import copy
import importlib
import unittest
from unittest.mock import patch

import autocode_coordinate as coordinate


def ledger(run, cost, tokens, *, complete=True, inherited=0):
    return {
        "run_dir": run,
        "cost": {"spent_usd": cost, "inherited_usd": inherited, "complete": complete},
        "usage": {
            "accounting": {
                "tokens": {
                    "input_tokens": {"value": tokens if complete else None, "known": tokens, "complete": complete},
                    "output_tokens": {"value": 0 if complete else None, "known": 0, "complete": complete},
                }
            }
        },
    }


class SnapshotTests(unittest.TestCase):
    def snapshot(self, *args):
        return importlib.import_module("autocode_coordination_usage").snapshot(*args)

    def test_distinct_source_members_and_retained_joint_ledgers_are_included_once(self):
        source = ledger("/source", 1, 100)
        member = ledger("/member", 2, 200)
        failed_joint = ledger("/old-joint", 3, 300, complete=False)
        current_joint = ledger("/joint", 4, 400)
        result = self.snapshot("/source", [source, member, failed_joint, current_joint, member], ["/new-member"])
        self.assertEqual(
            ["/source", "/member", "/old-joint", "/joint", "/new-member"], [row["run_dir"] for row in result["runs"]]
        )
        self.assertEqual(10, sum(row["cost"]["spent_usd"] or 0 for row in result["runs"]))
        self.assertFalse(result["runs"][2]["cost"]["complete"])
        self.assertEqual(0, result["runs"][-1]["cost"]["spent_usd"])
        self.assertEqual("/source", result["anchor"])

    def test_unknown_cost_and_tokens_remain_unknown_and_inherited_spend_is_preserved(self):
        row = ledger("/member", None, 0, complete=False, inherited=2)
        result = self.snapshot("/source", [row], [])["runs"][0]
        self.assertIsNone(result["cost"]["spent_usd"])
        self.assertEqual(2, result["cost"]["inherited_usd"])
        self.assertFalse(result["usage"]["accounting"]["tokens"]["input_tokens"]["complete"])

    def test_snapshot_is_detached_and_omits_prompts_attempt_logs_and_unrelated_fields(self):
        row = ledger("/source", 1, 100)
        row.update(task="private task text", chat={"log": ["large text"]})
        original = copy.deepcopy(row)
        result = self.snapshot("/source", [row], [])
        self.assertEqual({"run_dir", "cost", "usage"}, set(result["runs"][0]))
        result["runs"][0]["usage"]["accounting"]["tokens"]["input_tokens"]["known"] = 999
        self.assertEqual(original, row)


class StatusAccountingTests(unittest.TestCase):
    def test_uninitialized_coordinator_exposes_source_ledger(self):
        source = {**ledger("/source", 1, 100), "coordination": {"phase": "discovering"}}
        with (
            patch.object(coordinate.store, "read", return_value=None),
            patch.object(coordinate.store, "cancelled", return_value=False),
            patch.object(coordinate, "anchor_view", return_value=source),
        ):
            result = coordinate.status("/source")
        self.assertEqual(1, result["accounting"]["runs"][0]["cost"]["spent_usd"])

    def test_saved_coordinator_exposes_latest_anchor_all_history_and_pending_members(self):
        source = ledger("/source", 5, 500)
        member = ledger("/member", 2, 200)
        old_joint = ledger("/old-joint", 3, 300)
        document = {
            "id": "accounting-fixture",
            "anchor": "/source",
            "revision": 1,
            "phase": "planning",
            "joint_attempts": [],
            "members": [{"target": "new", "workspace": "/workspace", "run_dir": "/new", "base": "base"}],
        }
        with (
            patch.object(coordinate.store, "read", return_value=document),
            patch.object(coordinate, "observe", return_value={"member": member}),
            patch.object(coordinate, "next_step", return_value={"kind": "continue"}),
            patch.object(coordinate, "anchor_view", return_value=source),
            patch.object(coordinate, "budget_observations", return_value=([], [member, old_joint])),
        ):
            result = coordinate.status("/source")
        rows = result["accounting"]["runs"]
        self.assertEqual(["/source", "/member", "/old-joint", "/new"], [row["run_dir"] for row in rows])
        self.assertEqual(10, sum(row["cost"]["spent_usd"] or 0 for row in rows))
