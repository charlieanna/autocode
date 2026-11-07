"""Exclusive setup observations through a fresh public fake-provider run."""

import datetime as dt
import json
import sys
import unittest
from pathlib import Path

from . import test_subprocess as cli_support


@unittest.skipUnless(sys.platform == "linux", "supervised fake providers require supported Linux")
class PhaseTimingCLI(unittest.TestCase):
    def test_public_ledger_keeps_setup_outside_the_legacy_provider_interval(self):
        flow = cli_support.SubprocessFlow()
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        flow.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        flow.launch(["Build greeting", "--no-chat", "--no-spec-first", "--pause-after-stage"], 2)
        run = next((flow.project / ".autocode/runs").iterdir())
        public = json.loads(flow.launch(["--run-dir", str(run), "--status"], 0).stdout)
        view = public["view"]
        observations = [row for row in view["efficiency"]["observations"] if row["event_id"].startswith("phase:")]
        self.assertTrue(observations, "fresh public runs must retain measured runner setup")
        self.assertEqual(len(observations), len({row["event_id"] for row in observations}))
        attempts = [row for row in view["usage"]["accounting"]["attempts"] if not row.get("runner_owned")]
        self.assertTrue(attempts)
        for attempt in attempts:
            prompt = Path(attempt["output"]).with_suffix(".prompt.md").read_text()
            self.assertNotIn("efficiency_observations", prompt)
            for row in observations:
                self.assertNotIn(row["event_id"], prompt)
        for attempt in attempts:
            rows = [row for row in observations if row["provenance"]["stage"] == attempt["stage"]]
            phases = {row["provenance"]["phase"]: row for row in rows}
            self.assertEqual(
                {
                    "request_assembly",
                    "source_launcher_preparation",
                    "prompt_preparation",
                    "prompt_write",
                    "after_snapshot",
                    "report_loading",
                },
                set(phases),
            )
            start = dt.datetime.fromisoformat(attempt["started_at"])
            finish = dt.datetime.fromisoformat(attempt["finished_at"])
            for phase, row in phases.items():
                self.assertIsNone(row["attempt_id"], "setup must not reattribute a provider attempt")
                self.assertEqual("deterministic_setup", row["category"])
                self.assertEqual("completed", row["provenance"]["outcome"])
                self.assertEqual(1, row["provenance"]["iteration"])
                self.assertEqual(None if phase == "request_assembly" else 1, row["provenance"]["attempt"])
                self.assertGreaterEqual(row["provenance"]["duration_seconds"], 0)
                left, right = (dt.datetime.fromisoformat(row[key]) for key in ("started_at", "finished_at"))
                self.assertLessEqual(left, right)
                if phase in ("after_snapshot", "report_loading"):
                    self.assertGreaterEqual(left, finish)
                else:
                    self.assertLessEqual(right, start)
        self.assertEqual(0, view["efficiency"]["by_category"]["deterministic_setup"]["attempts"])
        self.assertEqual(len(attempts), view["efficiency"]["by_category"]["planning"]["attempts"])
        self.assertEqual("PAUSED_REQUESTED", view["status"])
        self.assertFalse(view["done"])
