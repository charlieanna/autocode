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

    def test_a_second_run_confirms_instead_of_duplicating(self):
        self.assertEqual(0, self.run_script()[0])
        first = self.written()
        code, output = self.run_script("--date", "2026-10-26")
        self.assertEqual(0, code, output)
        self.assertEqual(len(first["roles"]["glm"]), len(self.written()["roles"]["glm"]))
        self.assertIn("confirmed", output)

    def test_an_unreadable_state_is_not_evidence(self):
        (self.run_dir / "state.json").unlink()
        code, _output = self.run_script()
        self.assertEqual(2, code)


if __name__ == "__main__":
    unittest.main()
