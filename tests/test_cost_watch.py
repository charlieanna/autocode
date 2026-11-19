"""The cost cap is read while a stage runs, from the run's known spend and the stage's own usage so far.

Live VS Code test, 2026-11-18: the cap was checked only between stages, and a run capped at $6 paused
at $8.30 after its Builder alone spent about $2.40.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import autocode_cost_watch as watch

PRIOR = {
    "stage": "tester",
    "role": "tester",
    "model": "fixture-model",
    "metrics": {"provider_cost_usd": 0.6},
    "finished_at": "2026-11-18T00:00:00Z",
}


class CostWatchTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.events = Path(directory.name) / "builder-01.jsonl"
        self.record = {"stage": "builder", "role": "builder", "model": "fixture-model", "events": str(self.events)}

    def state(self, cap):
        return {"settings": {"limits": {"max_cost_usd": cap}}, "stages": [dict(PRIOR)]}

    def spend(self, usd):
        row = {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}, "cost_usd": usd}
        self.events.write_text(json.dumps(row) + "\n")

    def test_the_stage_is_stopped_once_its_spend_so_far_takes_the_run_to_the_cap(self):
        reached = watch.checker(self.state(1), self.record, self.events)
        self.assertIsNone(reached(), "no usage written yet")
        self.spend(0.3)
        self.assertIsNone(reached(), "$0.60 before the stage and $0.30 in it is under the $1 cap")
        self.spend(0.5)
        self.assertEqual(
            "Stopped at your $1 cost cap: the run had spent about $1.10 with this stage's work so far", reached()
        )

    def test_a_run_without_a_cap_is_never_watched(self):
        for cap in (None, 0):
            with self.subTest(cap=cap):
                self.assertIsNone(watch.checker(self.state(cap), self.record, self.events))

    def test_an_unpriced_stage_cannot_trip_the_cap(self):
        self.events.write_text(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10**9}}) + "\n")
        self.assertIsNone(watch.checker(self.state(1), self.record, self.events)())


if __name__ == "__main__":
    unittest.main()
