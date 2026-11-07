"""A live run qualifies the routes it proved, and nothing else (#2057 E).

The script reads one finished run's saved state and merges the reached route into the
selector's qualification table, by hand, for a pull request. These tests run it against a
temp table so the shipped one is never touched.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qualify_routes

MIMO = "xiaomi-token-plan-sgp/mimo-v2.6-pro"

TABLE = {
    "as_of": "2026-10-20",
    "basis": "test table",
    "roles": {
        "glm": [
            {
                "model": "zai-coding-plan/glm-5.3",
                "routes": ["zai-coding-plan/glm-5.3", "kilo/z-ai/glm-5.3"],
                "effort": "high",
                "provenance": "provisional",
                "benchmark": {"name": "Arena", "source": "example"},
                "evidence": "A public benchmark admitted it.",
            }
        ],
        "terra": [
            {
                "model": "openai/gpt-6-luna",
                "routes": ["openai/gpt-6-luna"],
                "effort": "medium",
                "provenance": "qualified",
                "evidence": "A shipped default route.",
            }
        ],
    },
}


def state(status="TASK_COMPLETE", kind="live"):
    return {
        "status": status,
        "settings": {
            "evidence_provenance": {"kind": kind},
            "planning_flow": "v2",
            "roles": {
                "glm": {"model": "zai-coding-plan/glm-5.3", "reasoning_effort": "high"},
                "terra": {"model": "openai/gpt-6-sol", "reasoning_effort": "medium"},
                "sol": {"model": "zai-coding-plan/glm-5.3", "reasoning_effort": "medium"},
            },
        },
    }


ATTEMPTS = {
    "attempts": [
        {"role": "glm", "model": "zai-coding-plan/glm-5.3"},
        {"role": "terra", "model": "openai/gpt-6-sol"},
    ]
}


class QualifyRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.table = self.root / "table.json"
        self.table.write_text(json.dumps(TABLE), encoding="utf-8")
        self.run_dir = self.root / "run-1"
        self.run_dir.mkdir()
        (self.run_dir / "state.json").write_text(json.dumps(state()), encoding="utf-8")
        self.patch = mock.patch.object(qualify_routes.usage, "accounting", return_value=ATTEMPTS)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def run_script(self, *extra):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = qualify_routes.main(
                [
                    "--run-dir",
                    str(self.run_dir),
                    "--scenario",
                    "greenfield-greeting-cli",
                    *extra,
                    "--table",
                    str(self.table),
                ]
            )
        return code, output.getvalue()

    def written(self):
        return json.loads(self.table.read_text(encoding="utf-8"))

    def test_a_model_the_table_excludes_is_named_and_not_qualified(self):
        # #2121: the stock glm-mimo-kilo profile pins MiMo as reviewer, so the run reaches it, but
        # the owner ruled MiMo out for plan review; the table's exclusion wins over the evidence.
        table = json.loads(self.table.read_text(encoding="utf-8"))
        table["roles"]["plan_reviewer"] = [
            {
                "model": "openai/gpt-6-sol",
                "routes": ["openai/gpt-6-sol"],
                "effort": "high",
                "provenance": "qualified",
                "evidence": "The first choice.",
            }
        ]
        table["excluded"] = {
            "plan_reviewer": [{"model": MIMO, "evidence": "Owner decision 2026-10-20: never choose MiMo."}]
        }
        self.table.write_text(json.dumps(table), encoding="utf-8")
        settings = state()
        settings["settings"]["roles"]["plan_reviewer"] = {"model": MIMO, "reasoning_effort": "high"}
        (self.run_dir / "state.json").write_text(json.dumps(settings), encoding="utf-8")
        attempts = {"attempts": [{"route_role": "plan_reviewer", "role": "astra", "model": MIMO}]}
        with mock.patch.object(qualify_routes.usage, "accounting", return_value=attempts):
            code, output = self.run_script("--date", "2026-10-25")
        self.assertEqual(0, code, output)
        self.assertEqual(["openai/gpt-6-sol"], [entry["model"] for entry in self.written()["roles"]["plan_reviewer"]])
        self.assertIn(f"Plan Reviewer: not qualified — {MIMO} is excluded from automatic selection", output)
        self.assertIn("Owner decision 2026-10-20", output)

    def test_a_write_keeps_the_table_fields_the_selector_does_not_own(self):
        # #2117: the shipped table carries its policy as a top-level `basis`; a write that rebuilt
        # the document from the parsed as_of/roles deleted it.
        code, output = self.run_script("--date", "2026-10-25")
        self.assertEqual(0, code, output)
        self.assertEqual("test table", self.written()["basis"])

    def test_a_provisional_route_becomes_qualified_with_its_evidence(self):
        code, output = self.run_script("--profile", "glm-mimo-kilo", "--date", "2026-10-25")
        self.assertEqual(0, code, output)
        table = self.written()
        planner = table["roles"]["glm"][0]
        self.assertEqual("qualified", planner["provenance"])
        self.assertEqual("high", planner["effort"])
        self.assertIn("Live 2026-10-25", planner["evidence"])
        self.assertIn("greenfield-greeting-cli on glm-mimo-kilo", planner["evidence"])
        self.assertEqual("2026-10-25", table["as_of"])
        self.assertIn("Planner: zai-coding-plan/glm-5.3 at high", output)

    def test_a_model_the_table_does_not_list_is_added_only_when_the_run_reached_it(self):
        code, output = self.run_script("--date", "2026-10-25")
        self.assertEqual(0, code, output)
        builders = {entry["model"]: entry for entry in self.written()["roles"]["terra"]}
        self.assertEqual({"openai/gpt-6-luna", "openai/gpt-6-sol"}, set(builders))
        self.assertEqual("qualified", builders["openai/gpt-6-sol"]["provenance"])
        self.assertEqual(["openai/gpt-6-sol"], builders["openai/gpt-6-sol"]["routes"])
        # A configured role with no attempt anywhere is named and never qualified.
        self.assertIn("Tester: not qualified", output)
        self.assertNotIn("sol", self.written()["roles"])

    def test_a_fake_run_qualifies_nothing(self):
        (self.run_dir / "state.json").write_text(json.dumps(state(kind="fake")), encoding="utf-8")
        before = self.table.read_text()
        code, _output = self.run_script()
        self.assertEqual(2, code)
        self.assertEqual(before, self.table.read_text())

    def test_an_unfinished_run_qualifies_nothing(self):
        (self.run_dir / "state.json").write_text(json.dumps(state(status="RUNNING")), encoding="utf-8")
        before = self.table.read_text()
        code, _output = self.run_script()
        self.assertEqual(2, code)
        self.assertEqual(before, self.table.read_text())

    def test_dry_run_reports_the_change_and_writes_nothing(self):
        before = self.table.read_text()
        code, output = self.run_script("--dry-run")
        self.assertEqual(0, code, output)
        self.assertIn("--dry-run", output)
        self.assertEqual(before, self.table.read_text())

    def test_a_merge_records_the_run_as_structured_evidence(self):
        # #2059: beside the prose, each run that confirms a route is a record selection can show.
        attempts = {
            "attempts": [
                {
                    "role": "glm",
                    "model": "zai-coding-plan/glm-5.3",
                    "duration_seconds": 600,
                    "reported_cost_usd": 0.25,
                },
                {
                    "role": "glm",
                    "model": "zai-coding-plan/glm-5.3",
                    "duration_seconds": 300,
                    "reported_cost_usd": None,
                    "historical_estimated_cost_usd": 0.15,
                },
                {"role": "glm", "model": "zai-coding-plan/glm-5.3", "duration_seconds": 900, "rejected": True},
                {"role": "terra", "model": "openai/gpt-6-sol", "duration_seconds": None},
            ]
        }
        with mock.patch.object(qualify_routes.usage, "accounting", return_value=attempts):
            code, output = self.run_script("--profile", "glm-mimo-kilo", "--date", "2026-10-25")
        self.assertEqual(0, code, output)
        roles = self.written()["roles"]
        (planner,) = roles["glm"][0]["runs"]
        execution = planner.pop("execution_evidence")
        self.assertEqual((0, 3), (execution["known_attempts"], execution["unknown_attempts"]))
        accounting = planner.pop("attempt_accounting")
        self.assertEqual((3, 1), (accounting["attempts"], accounting["failed_attempts"]))
        self.assertEqual(1800, accounting["duration_seconds"]["value"])
        self.assertEqual(
            {
                "date": "2026-10-25",
                "run": "run-1",
                "scenario": "greenfield-greeting-cli",
                "profile": "glm-mimo-kilo",
                "attempts": 2,  # the rejected attempt proves nothing
                "minutes": 15.0,
                "cost_usd": 0.4,
            },
            planner,
        )
        (builder,) = [entry for entry in roles["terra"] if entry["model"] == "openai/gpt-6-sol"][0]["runs"]
        self.assertIsNone(builder["minutes"])  # unknown stays unknown, never a guess
        self.assertIsNone(builder["cost_usd"])
        # The same run merged again adds no second record.
        with mock.patch.object(qualify_routes.usage, "accounting", return_value=attempts):
            self.run_script("--date", "2026-10-26")
        self.assertEqual(1, len(self.written()["roles"]["glm"][0]["runs"]))

    def test_a_second_run_confirms_instead_of_duplicating(self):
        self.assertEqual(0, self.run_script()[0])
        first = self.written()
        code, output = self.run_script("--date", "2026-10-26")
        self.assertEqual(0, code, output)
        self.assertEqual(len(first["roles"]["glm"]), len(self.written()["roles"]["glm"]))
        self.assertIn("confirmed", output)

    def test_a_stage_that_ran_another_slots_route_qualifies_that_slot_only(self):
        # #2114: `astra` runs the Plan Reviewer and Completion Reviewer stages on their own routes,
        # so those attempts qualify those slots and never the Planner route astra never launched.
        settings = state()
        settings["settings"]["roles"].update(
            {
                "astra": {"model": "xiaomi-token-plan-sgp/mimo-v2.6-pro", "reasoning_effort": "high"},
                "plan_reviewer": {"model": "xiaomi-token-plan-sgp/mimo-v2.6-pro", "reasoning_effort": "high"},
                "completion": {"model": "zai-coding-plan/glm-5.3", "reasoning_effort": "high"},
            }
        )
        (self.run_dir / "state.json").write_text(json.dumps(settings), encoding="utf-8")
        attempts = {
            "attempts": [
                {"role": "astra", "route_role": "plan_reviewer", "model": "xiaomi-token-plan-sgp/mimo-v2.6-pro"},
                {"role": "astra", "route_role": "completion", "model": "zai-coding-plan/glm-5.3"},
                {"role": "glm", "model": "zai-coding-plan/glm-5.3"},
            ]
        }
        with mock.patch.object(qualify_routes.usage, "accounting", return_value=attempts):
            code, output = self.run_script("--date", "2026-10-25")
        self.assertEqual(0, code, output)
        table = self.written()["roles"]
        self.assertEqual("qualified", table["plan_reviewer"][0]["provenance"])
        self.assertEqual("qualified", table["completion"][0]["provenance"])
        self.assertNotIn("astra", table, "the Planner route never launched")
        self.assertIn("Plan Reviewer: xiaomi-token-plan-sgp/mimo-v2.6-pro at high", output)
        self.assertIn("Completion Reviewer: zai-coding-plan/glm-5.3 at high", output)
        self.assertIn("Planner: not qualified", output)

    def test_a_failed_attempt_qualifies_nothing(self):
        # A Plan Reviewer route that went silent, replaced by a fallback on the Tester's route,
        # proves the fallback route only, however the run ended (#2114 review).
        settings = state()
        settings["settings"]["roles"]["plan_reviewer"] = {
            "model": "xiaomi-token-plan-sgp/mimo-v2.6-pro",
            "reasoning_effort": "high",
        }
        (self.run_dir / "state.json").write_text(json.dumps(settings), encoding="utf-8")
        failed = {"role": "astra", "route_role": "plan_reviewer", "model": "xiaomi-token-plan-sgp/mimo-v2.6-pro"}
        attempts = {
            "attempts": [
                {**failed, "timed_out": True},
                {**failed, "rejected": True},
                {**failed, "interrupted": True},
                {**failed, "report_only": True},
                {"role": "astra", "route_role": "sol", "model": "zai-coding-plan/glm-5.3"},
                {"role": "glm", "model": "zai-coding-plan/glm-5.3"},
            ]
        }
        with mock.patch.object(qualify_routes.usage, "accounting", return_value=attempts):
            code, output = self.run_script("--date", "2026-10-25")
        self.assertEqual(0, code, output)
        self.assertNotIn("plan_reviewer", self.written()["roles"])
        self.assertIn("Plan Reviewer: not qualified", output)
        self.assertIn("attempts: none", output.split("Plan Reviewer:")[1].splitlines()[0])

    def test_an_unreadable_state_is_not_evidence(self):
        (self.run_dir / "state.json").unlink()
        code, _output = self.run_script()
        self.assertEqual(2, code)


if __name__ == "__main__":
    unittest.main()
