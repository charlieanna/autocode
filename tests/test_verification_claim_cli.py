"""Actual provider handoffs resolve bound claim references without changing stages."""

import json
import subprocess
import sys
import unittest

from . import test_stage_repair_cli as fixtures

CRITERION = (
    "With the local greeting CLI, Ada receives exactly Hello, Ada followed by a newline and successful exit; "
    "an empty or whitespace-only name receives a nonzero exit and no greeting. "
    "Valid and invalid flows stay distinct and are independently exercised. "
) * 4


class VerificationClaimCLI(unittest.TestCase):
    fixture_for = fixtures.StageRepairCLI.fixture_for

    def test_long_approved_claim_is_resolved_through_the_public_build_and_verification_flow(self):
        from autocode_taskrun import TaskRun

        fixture = self.fixture_for("verification-claims")
        goals = fixture.root / "fixture-bin" / "goal_fixtures.py"
        source = goals.read_text()
        self.assertEqual(1, source.count('"Contract holds"'))
        goals.write_text(source.replace('"Contract holds"', json.dumps(CRITERION)))
        run = TaskRun.start(
            fixture.project,
            "Build a greeting tool",
            options=fixture.new_run_engine_args,
            command=fixture.entry,
            env=fixture.env,
            cwd=fixture.root,
            timeout=240,
        )
        view = run.status()
        need = view["needs"]
        self.assertEqual("answer", need["kind"], view.get("stop_reason"))
        run.answer(need["questions"][0]["id"], "CLI", resolver_token=need["resolver_token"])
        view = run.advance_until_input()
        self.assertEqual("approve_plan", view["needs"]["kind"], view.get("stop_reason"))
        run.show_goal()
        run.approve_plan(view["displayed_plan"]["token"])
        view = run.advance_until_input()
        self.assertEqual("TASK_COMPLETE", view["status"], view.get("stop_reason"))
        trace = [json.loads(line) for line in (fixture.root / "stage-repair-trace.jsonl").read_text().splitlines()]
        for stage in ("terra", "sol", "astra_review"):
            rows = [row for row in trace if row["stage"] == stage]
            self.assertEqual(1, len(rows), rows)
            self.assertFalse(rows[0]["repair"], rows)
            self.assertGreater(rows[0]["claim_references"], 0)
            self.assertGreaterEqual(rows[0]["verification_obligations"], rows[0]["claim_references"])
        self.assertEqual("PASS", view["evidence"]["check_replay"]["verdict"])
        for name, expected, output in (("Ada", 0, "Hello, Ada\n"), ("", 2, ""), ("   ", 2, "")):
            with self.subTest(name=name):
                result = subprocess.run(
                    [sys.executable, str(fixture.project / "greet.py"), name],
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=10,
                )
                self.assertEqual((expected, output), (result.returncode, result.stdout))
