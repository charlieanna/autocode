"""A selected parallel Builder retry adopts saved operator deadlines and budgets."""

import json
import unittest
from pathlib import Path

import autocode_milestones as milestones

from tests import test_member_stop_settings as flow


class BuilderRetryLimitsTests(unittest.TestCase):
    _flow = flow.MemberStopSettingsTests
    setUp, launch, saved = _flow.setUp, _flow.launch, _flow.saved
    new_run_engine_args = _flow.new_run_engine_args
    _fixture, fixture, paused = _flow._fixture, _flow.fixture, _flow.paused
    resume_with, workers = _flow.resume_with, _flow.workers
    del _flow

    def checkpoints(self, parent):
        return {mid: Path(row["run_dir"]) / "state.json" for mid, row in self.workers(parent).items()}

    def test_a_selected_retry_uses_separately_saved_tool_and_stage_limits(self):
        run, parent = self.paused("AUTOCODE_BUILDER_FAIL", members="M2")
        goal = parent["goal_contract"]
        paths = self.checkpoints(parent)
        sibling = paths["M1"].read_bytes()
        previous = json.loads(paths["M2"].read_text())
        self.resume_with(run, "--max-tool-seconds", "5400", "--max-stage-seconds", "7200")
        self.assertEqual(previous, json.loads(paths["M2"].read_text()), "A settings save cannot change a child alone")
        # Invalid selections never adopt limits into any child or launch work.
        self.resume_with(run, "--retry-builder", "M1")
        self.assertEqual(previous, json.loads(paths["M2"].read_text()))
        self.assertEqual(sibling, paths["M1"].read_bytes())
        del self.env["AUTOCODE_BUILDER_FAIL"]
        self.resume_with(run, "--retry-builder", "M2", "--pause-after-stage")
        parent = self.saved()[1]
        child = json.loads(paths["M2"].read_text())
        attempts = [record for record in child["stages"] if record.get("stage") == "terra"]
        self.assertEqual(
            [(1800, 3600), (5400, 7200)], [(r["tool_timeout_seconds"], r["stage_timeout_seconds"]) for r in attempts]
        )
        self.assertEqual(previous["settings"]["roles"], child["settings"]["roles"])
        self.assertEqual("user_explicit", child["settings"]["budget_origins"]["tool_timeout_seconds"])
        self.assertEqual(sibling, paths["M1"].read_bytes())
        self.assertEqual(goal, parent["goal_contract"])

    def test_an_exhausted_member_uses_the_saved_milestone_raise_without_resetting_spend(self):
        run, parent = self.paused("AUTOCODE_BUILDER_FAIL", members="M2")
        goal = parent["goal_contract"]
        paths = self.checkpoints(parent)
        sibling = paths["M1"].read_bytes()
        child = json.loads(paths["M2"].read_text())
        # Fault-inject elapsed accounting only in this disposable fake run.
        # No source, approval, route, response or retry authority is fabricated.
        milestones.progress(child)["seconds"] = 5400
        paths["M2"].write_text(json.dumps(child))
        self.resume_with(run, "--max-milestone-seconds", "21600")
        self.assertEqual(child, json.loads(paths["M2"].read_text()))
        del self.env["AUTOCODE_BUILDER_FAIL"]
        self.resume_with(run, "--retry-builder", "M2", expected=0)
        parent = self.saved()[1]
        continued = json.loads(paths["M2"].read_text())
        self.assertEqual("TASK_COMPLETE", parent["status"])
        self.assertEqual(21600, continued["settings"]["milestone_checkpoints"]["max_seconds"])
        self.assertGreaterEqual(milestones.progress(continued)["seconds"], 5400)
        self.assertEqual(child["settings"]["roles"], continued["settings"]["roles"])
        self.assertEqual(sibling, paths["M1"].read_bytes())
        self.assertEqual(goal, parent["goal_contract"])
