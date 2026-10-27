"""Empty PASS evidence is repaired at Tester through the real public CLI."""

import json
import unittest

from . import test_stage_repair_cli as fixtures


class ValidationEvidenceCLI(unittest.TestCase):
    fixture_for = fixtures.StageRepairCLI.fixture_for

    def test_empty_pass_evidence_is_repaired_at_tester_before_any_completion_call(self):
        from autocode_taskrun import TaskRun

        fixture = self.fixture_for("criterion-evidence")
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
        self.assertFalse((fixture.project / "greet.py").exists())
        run.show_goal()
        run.approve_plan(view["displayed_plan"]["token"])
        view = run.advance_until_input()
        self.assertEqual("TASK_COMPLETE", view["status"], view.get("stop_reason"))
        trace = [json.loads(line) for line in (fixture.root / "stage-repair-trace.jsonl").read_text().splitlines()]
        testers = [row for row in trace if row["stage"] == "sol"]
        self.assertEqual([False, True], [row["repair"] for row in testers], trace)
        self.assertIn(testers[0]["missing_evidence_id"], testers[1]["error"])
        self.assertIn("evidence", testers[1]["error"])
        self.assertEqual([testers[0]["missing_evidence_id"]], testers[1]["repaired_ids"])
        completion_indices = [i for i, row in enumerate(trace) if row["stage"] == "astra_review"]
        self.assertEqual(1, len(completion_indices), trace)
        self.assertLess(trace.index(testers[1]), completion_indices[0])
        self.assertEqual(1, sum(row["stage"] == "terra" for row in trace), "report repair must not replay Builder")
