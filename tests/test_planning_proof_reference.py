"""Planning uses the runner's reference, not an inferred current-workspace baseline."""

import copy
import json
import unittest
from pathlib import Path

import autocode_report_repair_context as repair
from units import autoplanner


class ProofReferenceTests(unittest.TestCase):
    def state(self):
        return {
            "task": "Add an optional start argument; preserve existing tests.",
            "workspace": "/fixture",
            "base_commit": "2" * 40,
            "settings": {"engine": "codex", "joint_planning": True, "roles": {"glm": {}, "plan_reviewer": {}}},
            "turns": [{"say": "Add start", "previous": {"status": "TASK_COMPLETE", "workflow": "bugfix"}}],
        }

    def test_review_and_revision_name_the_saved_original_repository_reference(self):
        state = self.state()
        before = copy.deepcopy(state)
        for stage in ("astra_challenge", "glm_revise", "astra_finalize"):
            with self.subTest(stage=stage):
                prompt, _ = autoplanner.context(state, stage, Path("/fixture/state.json"))
                packet = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
                self.assertEqual("2" * 40, packet["regression_reference"]["repository_base_commit"])
                self.assertEqual("saved_run_start", packet["regression_reference"]["scope"])
                self.assertIn("Passing in the current workspace alone", prompt)
                self.assertIn("does not reset this reference", prompt)
                self.assertIn("fresh runner proof", prompt)
        self.assertEqual(before, state)

    def test_report_repairs_receive_the_same_reference_without_rewriting_evidence(self):
        state = self.state()
        before = copy.deepcopy(state)
        source = {"format": "json", "content": {"summary": "retained"}}
        for stage in ("astra_discovery", "glm_revise", "astra_finalize"):
            with self.subTest(stage=stage):
                instruction, packet = repair.prompt_parts(
                    state, {"original": {"stage": stage}}, source, source, ("", {})
                )
                reference = packet["header"]["regression_reference"]
                self.assertEqual("2" * 40, reference["repository_base_commit"])
                self.assertIn("reviewed-change or operator patch", reference["preparation"])
                self.assertIn("Passing in the current workspace alone", repair.baseline_instruction(stage))
        self.assertEqual(before, state)

    def test_missing_or_malformed_saved_reference_stays_unknown(self):
        for base in (None, "", "HEAD", [], "z" * 40):
            state = self.state()
            state["base_commit"] = base
            prompt, _ = autoplanner.context(state, "glm_revise", Path("/fixture/state.json"))
            reference = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])["regression_reference"]
            self.assertIsNone(reference["repository_base_commit"])
            self.assertEqual("unknown", reference["scope"])


if __name__ == "__main__":
    unittest.main()
