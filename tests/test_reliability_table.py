"""The reliability table is generated, never mixed, and never edited by hand (#694)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"
sys.path.insert(0, str(TOOLS))
import reliability_table

COSTED_ROW = {
    "date": "2026-10-14",
    "profile": "p",
    "master": "abc1234",
    "mode": "live",
    "runs": 3,
    "passed": 2,
    "false_completions": 0,
    "median_minutes": 40,
    "note": "costed",
}

# A whole `cost` block pasted from a status view, as docs/arena.md allows.
PRICED_CASE = {
    "case": "bugfix-iso-weeks",
    "run": "20261014-run-a",
    "estimated_usd": 0.412,
    "reported_usd": None,
    "spent_usd": 0.412,
    "cap_usd": 5.0,
    "unpriced_stages": 0,
    "complete": True,
    "prices_as_of": "2026-10-14",
}


def load(rows):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "sweeps.json"
        path.write_text(json.dumps(rows))
        return reliability_table.load_rows(path)


def tables(text):
    """Each run of consecutive table lines, as lists of lines."""
    found, current = [], []
    for line in text.splitlines() + [""]:
        if line.startswith("|"):
            current.append(line)
        elif current:
            found.append(current)
            current = []
    return found


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

    def test_the_committed_table_and_readme_copy_match_the_generator(self):
        rows = reliability_table.load_rows(REPO / "docs" / "reliability-sweeps.json")
        self.assertEqual(
            reliability_table.markdown(rows),
            (REPO / "docs" / "reliability-table.md").read_text(),
            "regenerate: python tools/reliability_table.py docs/reliability-sweeps.json > docs/reliability-table.md",
        )
        self.assertIn(
            reliability_table.readme_excerpt(rows) + "\n",
            (REPO / "README.md").read_text(),
            "regenerate the README copy: python tools/reliability_table.py docs/reliability-sweeps.json --readme",
        )

    def test_each_mode_has_one_header_and_every_row_fits_it(self):
        seeded = reliability_table.load_rows(REPO / "docs" / "reliability-sweeps.json")
        costed = load([{**COSTED_ROW, "prompts_hash": "a" * 64, "case_costs": [PRICED_CASE]}])
        text = reliability_table.markdown(seeded + costed)
        self.assertNotIn("<function", text)
        for mode in text.split("\n## ")[1:]:
            self.assertEqual(1, sum(line.startswith("| Date |") for line in mode.splitlines()), mode[:40])
        for table in tables(text):
            widths = {line.count("|") - line.count("\\|") for line in table}
            self.assertEqual(1, len(widths), table[0])
        main = next(line for line in text.splitlines() if "| costed |" in line)
        self.assertEqual(
            f"| 2026-10-14 | p | `abc1234` | {'a' * 64} | 3 | 2 | 0 | 40 | 0.412 |  |  |  | costed |",
            main,
        )

    def test_case_costs_render_per_case_and_unknown_is_never_zero(self):
        samples = [
            PRICED_CASE,
            {
                "case": "feature-timesheet",
                "run": None,
                "estimated_usd": 0.00001,
                "reported_usd": 0.2,
                "spent_usd": 0.20001,
                "cap_usd": None,
            },
            {
                "case": "greenfield-todo",
                "run": "run-c",
                "estimated_usd": None,
                "reported_usd": None,
                "spent_usd": None,
                "cap_usd": 2,
            },
        ]
        rows = load([{**COSTED_ROW, "case_costs": samples}])
        self.assertEqual(
            {
                "case": "bugfix-iso-weeks",
                "run": "20261014-run-a",
                "estimated_usd": 0.412,
                "reported_usd": None,
                "spent_usd": 0.412,
                "cap_usd": 5.0,
            },
            rows[0]["case_costs"][0],
        )
        text = reliability_table.markdown(rows)
        section = text.split("### Cost per case", 1)[1]
        self.assertIn("| p | `abc1234` | bugfix-iso-weeks | 20261014-run-a | 0.412 | 0.412 | unknown | 5.0 |", section)
        self.assertIn(
            "| p | `abc1234` | feature-timesheet | not recorded | 0.20001 | 0.00001 | 0.2 | no cap |", section
        )
        self.assertIn("| p | `abc1234` | greenfield-todo | run-c | unknown | unknown | unknown | 2 |", section)
        # The sweep's median spend uses the two known values only.
        self.assertIn("| 40 | 0.306005 |", text)
        unknown_only = reliability_table.markdown(load([{**COSTED_ROW, "case_costs": samples[2:]}]))
        self.assertIn("| 40 | unknown |", unknown_only)
        self.assertNotIn("| 0 |", unknown_only.split("### Cost per case", 1)[1])

    def test_a_bad_case_cost_sample_is_refused(self):
        sample = {
            key: PRICED_CASE[key] for key in ("case", "run", "estimated_usd", "reported_usd", "spent_usd", "cap_usd")
        }
        unnamed = {key: value for key, value in sample.items() if key != "case"}
        unspent = {key: value for key, value in sample.items() if key != "spent_usd"}
        for bad in (
            [{**sample, "spent_usd": True}],
            [{**sample, "cap_usd": False}],
            [{**sample, "spent_usd": -0.01}],
            [{**sample, "spent_usd": float("nan")}],
            [{**sample, "estimated_usd": float("inf")}],
            [{**sample, "reported_usd": "0.5"}],
            [{**sample, "cap_usd": [1]}],
            [unspent],
            [unnamed],
            [{**sample, "case": ""}],
            [{**sample, "case": "   "}],
            [{**sample, "case": 5}],
            [{**sample, "run": 7}],
            [{**sample, "run": ""}],
            ["bugfix-iso-weeks"],
            sample,
            [sample] * 4,
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                load([{**COSTED_ROW, "case_costs": bad}])
        self.assertEqual([sample], load([{**COSTED_ROW, "case_costs": [sample]}])[0]["case_costs"])

    def test_rows_without_case_costs_still_load_without_a_cost_section(self):
        for costs in ({}, {"case_costs": None}, {"case_costs": []}):
            with self.subTest(costs=costs):
                rows = load([{**COSTED_ROW, **costs}])
                self.assertFalse(rows[0]["case_costs"])
                text = reliability_table.markdown(rows)
                self.assertNotIn("### Cost per case", text)
                self.assertIn("| 40 |  |  |  |  | costed |", text)

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
