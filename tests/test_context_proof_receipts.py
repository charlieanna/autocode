"""Large review handoffs preserve every receipt without repeating its raw execution payload."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_context as context
import autocode_regression as regression
import autocode_stage_context as stage_context

from . import test_handoff_dedup as fixtures


def packet(stage="sol"):
    return {
        "stage": stage,
        "goal_contract": {"body": {"constraints": ["No network"]}},
        "current_task": {"milestone_ids": ["B", "C"]},
        "unresolved_findings": [{"id": "F1", "severity": "blocking"}],
        "regression_proof": {
            "verdict": "FAIL",
            "changed_code_evidence": {"status": "reason_required", "obligations": [{"gap_id": "gap-B"}]},
            "mutation_check": {"files": [{"path": "b.py", "survivors": [21]}]},
            "changed_code_receipts": [
                {
                    "command": "python -c " + "a" * 16000,
                    "tail": "AssertionError " + "b" * 16000,
                    "results": {"tests": ["test_guard"] * 1000},
                    "supervision": {"events": ["observed"] * 1000},
                    "exit_code": index % 2,
                    "timed_out": index == 2,
                    "supervision_errors": ["missing observation"] if index == 3 else [],
                    "output": f"proof/{index}.log",
                    "output_sha256": str(index) * 64,
                    "supervision_sha256": "f" * 64,
                    "results_expected": True,
                }
                for index in range(4)
            ],
        },
    }


class ProofReceiptContextTests(unittest.TestCase):
    def test_actual_review_prompt_uses_the_artifact_and_reports_reduced_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = fixtures.packet()
            state.update(
                version=3,
                task="Implement the approved requirements",
                workspace=tmp,
                settings={"roles": {r: {"engine": "codex", "model": "fake"} for r in ("task_planner", "terra", "sol")}},
                answers=state["saved_answers"],
            )
            proof = packet()["regression_proof"]
            with (
                patch.object(stage_context.support, "snapshot", return_value={"revision": "r", "head": "h"}),
                patch.object(regression, "handoff", return_value=proof),
            ):
                for stage in ("sol", "astra_review", "astra_checkpoint"):
                    with self.subTest(stage=stage):
                        prompt, metrics = stage_context.context_packet(state, stage, Path(tmp) / "state.json")
                        data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
                        saved = json.loads(Path(data["context_artifact"]["path"]).read_text())
                        self.assertEqual(proof["changed_code_receipts"], saved["regression_proof_receipts"])
                        self.assertIn("regression_proof_receipts", metrics["externalized_fields"])
                        self.assertEqual("FAIL", data["regression_proof"]["verdict"])
                        self.assertGreater(metrics["handoff_bytes_saved"], 100000)

    def test_large_review_packet_preserves_every_outcome_and_retrievable_receipt(self):
        for stage in ("sol", "astra_review", "astra_checkpoint"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as tmp:
                original = packet(stage)
                before = copy.deepcopy(original)
                path = Path(tmp) / "state.json"
                small, moved = context.compact(original, path)
                self.assertEqual(before, original)
                self.assertEqual(["regression_proof_receipts"], moved)
                self.assertLess(len(json.dumps(small)), len(json.dumps(original)) // 5)
                artifact = small["context_artifact"]
                raw = Path(artifact["path"]).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), artifact["sha256"])
                saved = json.loads(raw)["regression_proof_receipts"]
                self.assertEqual(before["regression_proof"]["changed_code_receipts"], saved)
                for index, receipt in enumerate(small["regression_proof"]["changed_code_receipts"]):
                    self.assertEqual(
                        {"field": "regression_proof_receipts", "index": index}, receipt["details_artifact"]
                    )
                    for key in (
                        "exit_code",
                        "timed_out",
                        "supervision_errors",
                        "output",
                        "output_sha256",
                        "supervision_sha256",
                        "results_expected",
                    ):
                        self.assertEqual(saved[index][key], receipt[key])
                for key in ("verdict", "changed_code_evidence", "mutation_check"):
                    self.assertEqual(before["regression_proof"][key], small["regression_proof"][key])
                for key in ("goal_contract", "current_task", "unresolved_findings"):
                    self.assertEqual(before[key], small[key])
                small["regression_proof"]["changed_code_receipts"][3]["supervision_errors"].append("local edit")
                self.assertEqual(before, original)
                small = context.compact(original, path)[0]
                self.assertIn("before relying", small["regression_proof"]["receipt_details_instruction"])
                self.assertIn(
                    "never permission to infer PASS", small["regression_proof"]["receipt_details_instruction"]
                )
                self.assertEqual(small, context.compact(original, path)[0])
                self.assertEqual(small, context.compact(small, path)[0])
                Path(artifact["path"]).write_text("{}")
                with self.assertRaisesRegex(ValueError, "Saved context artifact changed"):
                    context.compact(original, path)

    def test_small_missing_malformed_and_nonreview_receipts_stay_inline(self):
        cases = [packet(stage) for stage in ("terra", "astra_plan", "astra_resolve", "investigate_stuck")]
        for receipts in (None, [], [{"exit_code": 1}], ["malformed"]):
            data = packet()
            data["regression_proof"]["changed_code_receipts"] = receipts
            cases.append(data)
        cases.extend([{"stage": "sol"}, {"stage": "sol", "regression_proof": None}])
        already_externalized = packet()
        already_externalized["context_artifact"] = {"path": "/saved/context.json", "sha256": "a" * 64}
        cases.append(already_externalized)
        nothing_to_move = packet()
        nothing_to_move["regression_proof"]["changed_code_receipts"] = [{"other_evidence": "x" * 70000}]
        cases.append(nothing_to_move)
        for original in cases:
            with self.subTest(original=original.get("stage")), tempfile.TemporaryDirectory() as tmp:
                self.assertEqual((original, []), context.compact(original, Path(tmp) / "state.json"))
                self.assertFalse((Path(tmp) / "context").exists())


if __name__ == "__main__":
    unittest.main()
