"""The whole run's known spend against the person's cap, for a serial run and for a parallel batch (#721)."""

import json
import tempfile
import unittest
from pathlib import Path

import autocode_cost_watch as watch
import autocode_run_spend as run_spend
import autocode_support as support
import autocode_util as util

FINISHED = "2026-11-24T00:00:00Z"


def stage(usd, **extra):
    return {
        "stage": "builder",
        "role": "builder",
        "iteration": 1,
        "model": "fixture-model",
        "started_at": FINISHED,
        "finished_at": FINISHED,
        "metrics": {"provider_cost_usd": usd},
        **extra,
    }


def running(events):
    """An attempt in flight: no finish time, its spend so far only in its event file."""
    record = {**stage(0), "metrics": {}, "events": str(events)}
    record.pop("finished_at")
    return record


class RunSpendTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.parent_dir = self.root / "run"
        self.parent_dir.mkdir()

    def member(self, milestone, stages, *, running_usd=None):
        directory = self.root / milestone
        workspace = self.root / f"{milestone}-checkout"
        directory.mkdir()
        workspace.mkdir()
        state = {
            "settings": {"limits": {"max_cost_usd": 9}},  # the copy made at launch; the parent's cap governs
            "workspace": str(workspace),
            "parent_run": str(self.parent_dir),
            "parent_batch": "batch-1",
            "stages": stages,
        }
        if running_usd is not None:
            events = directory / "builder-02.jsonl"
            events.write_text(json.dumps({"type": "turn.completed", "usage": {}, "cost_usd": running_usd}) + "\n")
            state["active_stage"] = running(events)
        util.atomic_json(directory / "state.json", state)
        return {"milestone_id": milestone, "run_dir": str(directory), "workspace": str(workspace)}, state

    def parent(self, cap, stages, workers):
        state = {
            "settings": {"limits": {"max_cost_usd": cap}},
            "workspace": str(self.root),
            "stages": stages,
            "orchestration_batch": {"id": "batch-1", "status": "BUILDING", "workers": workers},
        }
        util.atomic_json(self.parent_dir / "state.json", state)
        return state

    def test_a_serial_run_is_its_own_spend(self):
        state = {"settings": {"limits": {"max_cost_usd": 2}}, "stages": [stage(1.5)]}
        self.assertEqual({"spent": 1.5, "cap": 2.0}, run_spend.known(state))
        self.assertIsNone(run_spend.exceeded(state))
        state["stages"].append(stage(0.5))
        self.assertEqual((2.0, 2.0), run_spend.exceeded(state))
        with self.assertRaisesRegex(support.Paused, r"reached the \$2.00 cost cap; the Tester did not launch") as held:
            run_spend.guard(state, "the Tester")
        self.assertEqual("PAUSED_COST_LIMIT", held.exception.status)

    def test_a_member_counts_the_parent_and_every_sibling_under_the_parents_cap(self):
        first, first_state = self.member("M1", [stage(1)])
        second, _ = self.member("M2", [stage(1)], running_usd=0.75)
        # The parent already copied M1's attempt (batch_id): it is counted once, from the member.
        copied = stage(1, batch_id="batch-1", worker_milestone="M1")
        self.parent(20, [stage(6), copied], [first, second])
        # $6 before the batch, $1 for each member's Builder, and the $0.75 M2's running attempt has spent.
        self.assertEqual({"spent": 8.75, "cap": 20.0}, run_spend.known(first_state))
        # Alone, the member saw $1 of a $9 cap; the person has since raised the parent's cap to $20.
        self.assertIsNone(run_spend.exceeded(first_state))
        self.parent(8.75, [stage(6), copied], [first, second])
        self.assertEqual((8.75, 8.75), run_spend.exceeded(first_state))

    def test_the_parent_of_an_open_batch_counts_its_members_before_they_are_copied(self):
        first, _ = self.member("M1", [stage(2)])
        second, _ = self.member("M2", [stage(2)])
        parent = self.parent(9, [stage(6)], [first, second])
        self.assertEqual((10.0, 9.0), run_spend.exceeded(parent))

    def test_an_unreadable_member_falls_back_to_the_attempts_the_parent_copied(self):
        first, first_state = self.member("M1", [stage(1)])
        second = {"milestone_id": "M2", "run_dir": str(self.root / "gone"), "workspace": str(self.root / "gone")}
        copied = stage(3, batch_id="batch-1", worker_milestone="M2")
        self.parent(20, [stage(1), copied], [first, second])
        self.assertEqual(5.0, run_spend.known(first_state)["spent"])

    def test_a_members_running_stage_stops_once_a_sibling_takes_the_run_to_the_cap(self):
        first, first_state = self.member("M1", [])
        second, _ = self.member("M2", [], running_usd=0.5)
        self.parent(9, [stage(8)], [first, second])
        events = self.root / "M1" / "builder-01.jsonl"
        record = first_state["active_stage"] = running(events)
        reached = watch.checker(first_state, record, events)
        self.assertIsNone(reached(), "$8 before the batch and M2's $0.50 so far is under $9")
        events.write_text(json.dumps({"type": "turn.completed", "usage": {}, "cost_usd": 0.6}) + "\n")
        self.assertEqual(
            "Stopped at your $9 cost cap: the run had spent about $9.10 with this stage's work so far", reached()
        )


if __name__ == "__main__":
    unittest.main()
