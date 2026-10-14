"""The Claude cloud-session guide's batch runner (examples/claude-provider/batch.py).

Claude Code is a built-in provider (tools/providers/claude.py, #717; its tests are test_claude_stream,
test_claude_provider and test_claude_provider_cli). This directory keeps the cloud-session guide: batch.py
starts each run through the scenario harness with the claude-tiers profile and restarts only what a
container restart killed.
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "claude-provider"
batch_spec = importlib.util.spec_from_file_location("batch", EXAMPLE / "batch.py")
batch = importlib.util.module_from_spec(batch_spec)
batch_spec.loader.exec_module(batch)


class BatchTests(unittest.TestCase):
    """batch.py restarts only what a container restart killed: runs without a result.json."""

    def test_only_the_runs_still_missing_are_started_again(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            for name, verdict in (
                ("20260930T0759Z-bugfix-trivial-claude-tiers-a1", "PASS"),
                ("20260930T0800Z-bugfix-trivial-claude-tiers-b2", None),  # killed mid-run
                ("20260930T0801Z-review-then-fix-claude-tiers-c3", "FALSE_COMPLETE"),
            ):
                (out / name).mkdir()
                if verdict:
                    (out / name / "result.json").write_text(
                        json.dumps({"verdict": verdict, "checks": [{"ok": True}], "wall_seconds": 60})
                    )
            ids = ["bugfix-trivial", "review-then-fix", "discuss-cache-choice"]
            self.assertEqual(
                ["bugfix-trivial", "review-then-fix", "discuss-cache-choice", "discuss-cache-choice"],
                batch.missing(ids, out, 2),
            )
            summary = batch.status(out)
            self.assertIn("finished 2 (1 FALSE_COMPLETE, 1 PASS), running 1", summary)
            self.assertIn("bugfix-trivial                 PASS 1/1 60s $0.00 | running", summary)

    def test_hybrid_runs_are_counted_and_shown_apart_from_natural_ones(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            for name in (
                "20261005T0759Z-feature-stock-refusals-claude-tiers-a1",
                "20261005T0800Z-feature-stock-refusals-claude-tiers-hybrid-b2",
                "20261005T0801Z-feature-stock-refusals-fake-hybrid-c3",
                "20261005T0802Z-feature-stock-refusals-fake-d4",
            ):
                (out / name).mkdir()
                (out / name / "result.json").write_text(
                    json.dumps({"verdict": "PASS", "checks": [], "wall_seconds": 1})
                )
            ids = ["feature-stock-refusals"]
            self.assertEqual(["feature-stock-refusals"], batch.missing(ids, out, 2))
            self.assertEqual(["feature-stock-refusals"] * 2, batch.missing(ids, out, 3, "claude-tiers-hybrid"))
            self.assertEqual([], batch.missing(ids, out, 1, "fake-hybrid"))
            self.assertEqual(["feature-stock-refusals"], batch.missing(ids, out, 2, "fake"))
            hybrid = batch.mode(type("Args", (), {"fake": False, "hybrid": True})())
            self.assertEqual("claude-tiers-hybrid", hybrid)
            summary = batch.status(out)
            self.assertIn("feature-stock-refusals [claude-tiers-hybrid] PASS", summary)
            self.assertIn("feature-stock-refusals         PASS", summary)

    def test_launch_runs_the_harness_directly(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            (out / "logs").mkdir()
            args = type("Args", (), {"out": out, "timeout_minutes": 45, "fake": False, "hybrid": False})()
            with mock.patch.object(batch.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
                self.assertEqual(0, batch.launch("bugfix-trivial", args, 1))
            command = run.call_args.args[0]
            self.assertEqual(
                [sys.executable, str(EXAMPLE.parents[1] / "scenarios" / "run.py"), "run", "bugfix-trivial"], command[:4]
            )
            self.assertEqual(["--profile", "claude-tiers", "--i-authorize-live-model-spend"], command[-3:])
            self.assertEqual(["--timeout-minutes", "45"], command[6:8])

    def test_the_qualification_list_names_real_scenarios(self):
        ids = batch.scenarios(EXAMPLE / "qualification.txt")
        self.assertEqual(17, len(ids))
        catalog = EXAMPLE.parents[1] / "scenarios" / "catalog"
        self.assertEqual([], [scenario for scenario in ids if not (catalog / scenario / "scenario.toml").is_file()])

    def test_the_stress_list_names_real_scenarios_the_fake_can_run(self):
        """The #455 stress shortlist is read the way batch.py reads it. A renamed scenario, a duplicate or one
        with no reference solution for `--fake` (it would be SKIPPED, not run) cannot silently thin the campaign."""
        repo = EXAMPLE.parents[1]
        catalog = repo / "scenarios" / "catalog"
        path = repo / "scenarios" / "stress.txt"
        ids = batch.scenarios(path)
        self.assertEqual(23, len(ids))
        self.assertEqual(len(ids), len(set(ids)), "a scenario is listed once")
        self.assertEqual([], [scenario for scenario in ids if not (catalog / scenario / "scenario.toml").is_file()])
        self.assertEqual([], [scenario for scenario in ids if not (catalog / scenario / "reference").is_dir()])


if __name__ == "__main__":
    unittest.main()
