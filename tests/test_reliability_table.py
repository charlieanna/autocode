"""The reliability table is generated, never mixed, and never edited by hand (#694)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import reliability_table


class ReliabilityTableTests(unittest.TestCase):
    def test_live_and_fake_rows_are_separate_tables(self):
        rows = [
            {
                "date": "2026-10-07",
                "profile": "glm53-openai",
                "master": "4e7e6895",
                "mode": "live",
                "runs": 3,
                "passed": 2,
                "false_completions": 0,
                "note": "a",
            },
            {
                "date": "2026-10-08",
                "profile": "fake",
                "master": "06d6c390",
                "mode": "fake",
                "runs": 64,
                "passed": 62,
                "false_completions": 0,
                "note": "b",
            },
        ]
        text = reliability_table.markdown(rows)
        self.assertIn("## Live models", text)
        self.assertIn("## Fake provider", text)
        live = text.split("## Live models", 1)[1].split("## Fake provider", 1)[0]
        fake = text.split("## Fake provider", 1)[1]
        self.assertIn("glm53-openai", live)
        self.assertNotIn("06d6c390", live)
        self.assertIn("06d6c390", fake)
        self.assertNotIn("glm53-openai", fake)

    def test_each_row_names_its_master_commit(self):
        rows = [
            {
                "date": "2026-10-07",
                "profile": "p",
                "master": "abc1234",
                "mode": "live",
                "runs": 1,
                "passed": 1,
                "false_completions": 0,
                "note": "n",
            }
        ]
        self.assertIn("`abc1234`", reliability_table.markdown(rows))

    def test_a_bad_row_is_refused(self):
        for bad in (
            {
                "date": "d",
                "profile": "p",
                "master": "m",
                "mode": "other",
                "runs": 1,
                "passed": 1,
                "false_completions": 0,
                "note": "n",
            },
            {
                "date": "d",
                "profile": "p",
                "master": "m",
                "mode": "live",
                "runs": 1,
                "passed": 0,
                "false_completions": 2,
                "note": "n",
            },
            {"date": "d", "profile": "p", "master": "m", "mode": "live", "note": "n"},
        ):
            with self.subTest(bad=bad):
                with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
                    json.dump([bad], handle)
                    path = handle.name
                with self.assertRaises(ValueError):
                    reliability_table.load_rows(path)

    def test_first_interaction_timestamps_and_known_sample_medians_are_published(self):
        samples = [
            {
                "run": "case-1",
                "launched_at": "2026-10-10T08:00:00+00:00",
                "first_question_at": "2026-10-10T08:00:12+00:00",
                "first_question_seconds": 12,
                "first_plan_at": "2026-10-10T08:01:00+00:00",
                "first_plan_seconds": 60,
                "first_builder_at": None,
                "first_builder_seconds": None,
            },
            {
                "run": "case-2",
                "launched_at": "2026-10-10T09:00:00+00:00",
                "first_question_at": None,
                "first_question_seconds": None,
                "first_plan_at": "2026-10-10T09:02:00+00:00",
                "first_plan_seconds": 120,
                "first_builder_at": "2026-10-10T09:03:00+00:00",
                "first_builder_seconds": 180,
            },
        ]
        row = {
            "date": "2026-10-10",
            "profile": "p",
            "master": "abc1234",
            "mode": "live",
            "runs": 2,
            "passed": 1,
            "false_completions": 0,
            "median_minutes": None,
            "note": "known samples",
            "interaction_timings": samples,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sweeps.json"
            path.write_text(json.dumps([row]))
            rows = reliability_table.load_rows(path)
        text = reliability_table.markdown(rows)
        self.assertIn("| 12 | 90.0 | 180 |", text)
        for sample in samples:
            self.assertIn(sample["run"], text)
            self.assertIn(sample["launched_at"], text)
            for event in ("question", "plan", "builder"):
                if sample[f"first_{event}_at"] is not None:
                    self.assertIn(sample[f"first_{event}_at"], text)
        historical = reliability_table.markdown([{**row, "interaction_timings": None}])
        self.assertNotIn("### First interaction samples", historical)
        self.assertIn("|  |  |  | known samples |", historical)

    def test_timing_samples_cannot_invent_or_change_elapsed_seconds(self):
        sample = {
            "run": "case",
            "launched_at": "2026-10-10T08:00:00+00:00",
            "first_question_at": None,
            "first_question_seconds": None,
            "first_plan_at": "2026-10-10T08:01:00+00:00",
            "first_plan_seconds": 60,
            "first_builder_at": None,
            "first_builder_seconds": None,
        }
        row = {
            "date": "d",
            "profile": "p",
            "master": "m",
            "mode": "live",
            "runs": 1,
            "passed": 0,
            "false_completions": 0,
            "note": "n",
        }
        for changed in (
            {**sample, "first_plan_seconds": 0},
            {**sample, "first_plan_at": "2026-10-10T08:00:01+00:00", "first_plan_seconds": True},
            {**sample, "first_question_seconds": 0},
            {**sample, "launched_at": "unknown"},
            {**sample, "first_plan_at": "2026-10-10T08:01:00"},
            {**sample, "run": ""},
        ):
            with self.subTest(sample=changed), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "sweeps.json"
                path.write_text(json.dumps([{**row, "interaction_timings": [changed]}]))
                with self.assertRaises(ValueError):
                    reliability_table.load_rows(path)

    def test_the_seeded_sweeps_generate_and_the_readme_omits_fake(self):
        sweeps = Path(__file__).resolve().parents[1] / "docs" / "reliability-sweeps.json"
        rows = reliability_table.load_rows(sweeps)
        self.assertGreaterEqual(len(rows), 3)
        text = reliability_table.markdown(rows)
        self.assertIn("2026-10-07", text)
        self.assertIn("2026-10-04", text)
        self.assertIn("2026-10-01", text)
        excerpt = reliability_table.readme_excerpt(rows)
        self.assertNotIn("## Fake", excerpt)
        self.assertIn("glm53-openai", excerpt)
        self.assertIn("claude-tiers", excerpt)

    def test_the_cli_writes_the_documented_table(self):
        repo = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [
                sys.executable,
                str(repo / "tools" / "reliability_table.py"),
                str(repo / "docs" / "reliability-sweeps.json"),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("Generated by", result.stdout)
        self.assertIn("False completions", result.stdout)


if __name__ == "__main__":
    unittest.main()
