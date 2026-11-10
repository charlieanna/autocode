"""A syntax-only correction preserves the exact public plan-approval boundary."""

import hashlib
import sys
import unittest
from pathlib import Path

from . import test_stage_repair_cli as cli_support


@unittest.skipUnless(sys.platform == "linux", "supervised fake providers require supported Linux")
class SyntaxCLI(unittest.TestCase):
    fixture_for = cli_support.StageRepairCLI.fixture_for
    plan = cli_support.StageRepairCLI.plan
    assert_approval_boundary = cli_support.StageRepairCLI.assert_approval_boundary

    def test_trailing_comma_reaches_exact_plan_gate_without_a_model_repair(self):
        fixture = self.fixture_for("discovery-trailing-comma")
        # Keep Requirements identical on base and candidate so this regression
        # measures only the report-syntax optimization.
        fixture.new_run_engine_args += ("--requirements-triage",)
        _, state, view, trace = self.plan(fixture)
        self.assert_approval_boundary(fixture, state, view, trace)
        self.assertFalse(any(row["repair"] for row in trace), trace)
        self.assertEqual(1, sum(row["stage"] == "draft_plan" for row in trace))
        for report in (row for row in state["stages"] if row["stage"] == "draft_plan"):
            raw = Path(report["response_text"]).read_text()
            self.assertTrue(raw.endswith(",}"))
            self.assertEqual(hashlib.sha256(raw.encode()).hexdigest(), report["syntax_corrections"]["source_sha256"])
        self.assertIn("Greet", fixture.launch(["--run-dir", str(fixture.saved()[0]), "--show-goal"], 0).stdout)
