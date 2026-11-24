"""The pinned real-repository sweep driver (examples/claude-provider/real_repo_sweep.py)."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import reliability_table

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "claude-provider" / "real_repo_sweep.py"
spec = importlib.util.spec_from_file_location("real_repo_sweep", EXAMPLE)
sweep = importlib.util.module_from_spec(spec)
sys.modules["real_repo_sweep"] = sweep
spec.loader.exec_module(sweep)
CATALOG = Path(__file__).resolve().parents[1] / "arena" / "catalog.json"
SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios" / "catalog"


def record(case, verdict, minutes, usd, status="TASK_COMPLETE"):
    return {"case": case, "verdict": verdict, "status": status, "oracle": "5/5", "minutes": minutes, "spent_usd": usd}


class Sets(unittest.TestCase):
    def test_every_pinned_case_exists(self):
        catalog = json.loads(CATALOG.read_text())
        arena = {c["id"] for c in (catalog["cases"] if isinstance(catalog, dict) else catalog)}
        for name, cases in sweep.SETS.items():
            for case, spec in cases.items():
                with self.subTest(set=name, case=case):
                    if spec.get("harness") == "scenario":
                        self.assertTrue((SCENARIOS / case / "scenario.toml").exists())
                    else:
                        self.assertIn(case, arena)
                        self.assertTrue(spec["test"])

    def test_route_matches_the_claude_tiers_profile_and_never_fable(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scenarios"))
        from harness import profiles

        profile = profiles.resolve("claude-tiers")
        expected = {profiles.MODEL_FLAGS[role]: model for role, model in profile["models"].items()}
        for flag, model in expected.items():
            self.assertEqual(sweep.MODELS[flag], model)
        options = sweep.arena_options("pytest -q")
        self.assertIn("--option=--provider=claude", options)
        self.assertIn("--option=--test-command=pytest -q", options)
        self.assertFalse([o for o in options if "fable" in o.lower()])
        self.assertTrue(all(m.split("-")[1] in ("opus", "sonnet", "haiku") for m in sweep.MODELS.values()))


class Summarize(unittest.TestCase):
    def test_counts_passes_false_completions_median_and_costs(self):
        records = [
            record("a", "PASS", 4.0, 1.5),
            record("a", "FALSE_COMPLETE", 10.0, 2.0),
            record("b", "STOPPED", 20.0, None, status="PAUSED_INVALID_OUTPUT"),
        ]
        row = sweep.summarize(records, date="2026-11-30", master="abc1234")
        self.assertEqual((row["runs"], row["passed"], row["false_completions"]), (3, 1, 1))
        self.assertEqual(row["median_minutes"], 10.0)
        self.assertIn("$3.50 (some unknown)", row["note"])
        self.assertIn("b STOPPED (PAUSED_INVALID_OUTPUT", row["note"])
        self.assertIsNone(row["case_costs"][2]["spent_usd"])

    def test_row_is_accepted_by_the_reliability_table(self):
        row = sweep.summarize([record("a", "PASS", 4.0, 1.5)], date="2026-11-30", master="abc1234")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sweeps.json"
            path.write_text(json.dumps([row]))
            rows = reliability_table.load_rows(path)
        self.assertIn("`abc1234`", reliability_table.markdown(rows))

    def test_summary_reads_the_records_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "records").mkdir()
            sweep.record_path(out, "a", 1).write_text(json.dumps(record("a", "PASS", 4.0, 1.5)))
            import io
            from contextlib import redirect_stdout

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                sweep.main(["summary", str(out), "--master", "abc1234", "--date", "2026-11-30"])
        self.assertEqual(json.loads(buffer.getvalue())["passed"], 1)

    def test_run_refuses_without_live_spend_authorization(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(SystemExit):
            sweep.main(["run", "starter", "--out", tmp])


class RunArena(unittest.TestCase):
    def test_each_arena_case_is_prepared_in_a_fresh_store(self):
        """arena/prepare.py refuses a non-empty store, so a second Arena case must not reuse the first one's."""
        prepared = []

        def fake_run(command, **kwargs):
            if "arena/prepare.py" in command:
                store = Path(command[command.index("--arena") + 1])
                if store.exists() and any(store.iterdir()):
                    raise sweep.subprocess.CalledProcessError(1, command)
                case = command[command.index("--case") + 1]
                (store / "cases" / case).mkdir(parents=True)
                prepared.append((case, store))
                return mock.Mock(returncode=0)
            store = Path(command[command.index("--arena") + 1])
            workspace = store / "attempts" / "x" / "workspace"
            row = {
                "verdict": "PASS",
                "runner_status": "TASK_COMPLETE",
                "checks": [{"ok": True}],
                "elapsed_seconds": 60,
                "workspace": str(workspace),
            }
            return mock.Mock(returncode=0, stdout=json.dumps(row))

        arena_cases = [c for c, spec in sweep.SETS["starter"].items() if spec.get("harness") != "scenario"]
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(sweep.subprocess, "run", fake_run):
            out = Path(tmp)
            for case in arena_cases:
                for _ in range(2):
                    record = sweep.run_arena(out, case, sweep.SETS["starter"][case], "cohort", 60)
                    self.assertEqual(record["verdict"], "PASS")
        self.assertEqual([case for case, _store in prepared], arena_cases)
        self.assertEqual(len({store for _case, store in prepared}), len(arena_cases))


if __name__ == "__main__":
    unittest.main()
