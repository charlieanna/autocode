"""A review handoff for a large wave keeps every gap decidable without repeating its mutation detail (#2806)."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_changed_code_review as changed_code_review
import autocode_context as context
import autocode_proof_detail as proof_detail
import autocode_regression as regression
import autocode_stage_context as stage_context

from . import test_handoff_dedup as fixtures

LOG = "/runs/r/regression/proof-05-c39b1e6f/mutation-check/mutant-{:03d}-4c9f3f15b6fd463191446252911610f3.log"


def mutant(index):
    return {
        "line": index,
        "end_line": index,
        "operator": "constant_return",
        "before": "return value",
        "after": "return None",
        "source_sha256": "a" * 64,
        "failed_tests": [] if index % 3 else ["tests.test_core.CoreTests.test_value"],
        "survived_tests": ["tests.test_core.CoreTests.test_other"] if index % 3 else [],
        "evidence": [LOG.format(index)],
        "outcome": "killed" if index % 3 == 0 else "survived",
    }


def obligation(index):
    tested = index % 4 == 0
    return {
        "gap_id": "changed-code:" + hashlib.sha256(str(index).encode()).hexdigest(),
        "handle": f"L{index + 1}",
        "path": f"pkg/mod{index % 3}.py",
        "sha256": str(index % 3) * 64,
        "line": index,
        "status": "tested" if tested else "reason_required",
        "executed": index % 2 == 0,
        "mutation_demonstrated": tested,
        "mutation_outcomes": ["killed"] if tested else ([] if index % 5 else ["survived"]),
        "evidence": [LOG.format(index)] if tested else [],
        "fallback": "none" if tested else "reviewer_reason_required",
        "reason": "Current selected tests detect the changed line's mutants."
        if tested
        else "No measured execution or complete behavioral mutation evidence for this changed line.",
    }


def proof():
    files = [
        {"path": f"pkg/mod{n}.py", "sha256": str(n) * 64, "notes": [], "mutants": [mutant(i) for i in range(120)]}
        for n in range(3)
    ]
    survivors = {
        f"AC{n}": [
            {
                "test": f"tests.test_core.CoreTests.test_ac{n}",
                "path": "pkg/mod0.py",
                "line": i,
                "operator": "drop_branch",
                "before": "return None",
                "after": "pass",
                "text": f"test_ac{n} passes with pkg/mod0.py:{i} changed (`return None` -> `pass`)",
            }
            for i in range(150)
        ]
        for n in range(1, 4)
    }
    return {
        "verdict": "PASS",
        "fail_to_pass": ["tests.test_core.CoreTests.test_ac1"],
        "mutation_check": {"collected": 360, "limit_per_file": 120, "files": files, "survivors": survivors},
        "changed_code_inputs": {
            "inventory": {"files": ["pkg/mod0.py", "pkg/mod1.py", "pkg/mod2.py"]},
            "mutation": {"files": copy.deepcopy(files)},
            "command_outputs": [LOG.format(i) for i in range(400)],
            "custody_gaps": [],
        },
        "changed_code_evidence": {
            "schema": 1,
            "status": "reason_required",
            "reason": "Some changed lines lack behavioral evidence.",
            "obligations": [obligation(i) for i in range(400)],
        },
    }


def packet(stage="sol"):
    return {
        "stage": stage,
        "goal_contract": {"body": {"constraints": ["No network"]}},
        "unresolved_findings": [{"id": "F1", "severity": "blocking"}],
        "regression_proof": proof(),
    }


class ProofDetailTests(unittest.TestCase):
    def test_a_large_wave_handoff_keeps_every_gap_and_moves_its_mutation_detail_whole(self):
        for stage in proof_detail.STAGES:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as tmp:
                original = packet(stage)
                before = copy.deepcopy(original)
                path = Path(tmp) / "state.json"
                small, moved = context.compact(original, path)
                self.assertEqual(before, original)
                fields = [proof_detail.FILES, proof_detail.SURVIVORS, proof_detail.INPUTS, proof_detail.OBLIGATIONS]
                self.assertEqual(fields, moved)
                self.assertLess(len(json.dumps(small)), len(json.dumps(original)) // 4)
                artifact = small["context_artifact"]
                raw = Path(artifact["path"]).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), artifact["sha256"])
                saved = json.loads(raw)
                whole = before["regression_proof"]
                self.assertEqual(whole["mutation_check"]["files"], saved[proof_detail.FILES])
                self.assertEqual(whole["mutation_check"]["survivors"], saved[proof_detail.SURVIVORS])
                self.assertEqual(whole["changed_code_inputs"], saved[proof_detail.INPUTS])
                self.assertEqual(whole["changed_code_evidence"]["obligations"], saved[proof_detail.OBLIGATIONS])

                inline = small["regression_proof"]
                for key in ("verdict", "fail_to_pass"):
                    self.assertEqual(whole[key], inline[key])
                for key in ("goal_contract", "unresolved_findings"):
                    self.assertEqual(before[key], small[key])
                self.assertEqual(360, inline["mutation_check"]["collected"])
                self.assertEqual(
                    {"mutants": 120, "outcomes": {"killed": 40, "survived": 80}},
                    {k: inline["mutation_check"]["files"][1][k] for k in ("mutants", "outcomes")},
                )
                self.assertEqual(
                    {
                        "count": 150,
                        "tests": ["tests.test_core.CoreTests.test_ac2"],
                        "details_artifact": {"field": proof_detail.SURVIVORS, "key": "AC2"},
                    },
                    inline["mutation_check"]["survivors"]["AC2"],
                )
                self.assertEqual(whole["changed_code_inputs"]["inventory"], inline["changed_code_inputs"]["inventory"])
                self.assertEqual(400, inline["changed_code_inputs"]["command_outputs"]["total"])

                # Every changed line keeps its handle, line, status, execution and outcomes; each gap the
                # Tester may decide is named by a handle the runner resolves to the run's exact gap id.
                assessment = whole["changed_code_evidence"]
                lines = [
                    {**line, "path": group["path"], "sha256": group["sha256"]}
                    for group in inline["changed_code_evidence"]["obligations"]
                    for line in group["lines"]
                ]
                self.assertEqual(len(assessment["obligations"]), len(lines))
                by_handle = {line["handle"]: line for line in lines}
                for row in assessment["obligations"]:
                    kept = by_handle[row["handle"]]
                    for key in ("path", "sha256", "line", "status", "executed", "mutation_outcomes"):
                        self.assertEqual(row[key], kept[key])
                    if row["status"] == "reason_required":
                        self.assertEqual(row["gap_id"], changed_code_review.resolve(assessment, row["handle"]))
                self.assertEqual(assessment["status"], inline["changed_code_evidence"]["status"])

                instruction = inline["proof_detail_instruction"]
                self.assertIn("before relying", instruction)
                self.assertIn("never permission to infer PASS", instruction)
                for field in fields:
                    self.assertIn(field, instruction)
                self.assertEqual(small, context.compact(original, path)[0])
                self.assertEqual(small, context.compact(small, path)[0])

    def test_the_actual_tester_prompt_fits_where_the_whole_proof_did_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = fixtures.packet()
            state.update(
                version=3,
                task="Implement the approved requirements",
                workspace=tmp,
                settings={"roles": {r: {"engine": "codex", "model": "fake"} for r in ("astra", "terra", "sol")}},
                answers=state["saved_answers"],
            )
            whole = proof()
            with (
                patch.object(stage_context.support, "snapshot", return_value={"revision": "r", "head": "h"}),
                patch.object(regression, "handoff", return_value=whole),
            ):
                prompt, metrics = stage_context.context_packet(state, "sol", Path(tmp) / "state.json")
            data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
            saved = json.loads(Path(data["context_artifact"]["path"]).read_text())
            self.assertEqual(whole["changed_code_evidence"]["obligations"], saved[proof_detail.OBLIGATIONS])
            self.assertIn(proof_detail.SURVIVORS, metrics["externalized_fields"])
            self.assertLess(len(json.dumps(data["regression_proof"])), len(json.dumps(whole)) // 4)

    def test_small_unhandled_pointed_and_nonreview_detail_stays_inline(self):
        cases = [packet(stage) for stage in ("terra", "astra_plan", "astra_resolve", "investigate_stuck")]
        small = packet()
        small["regression_proof"] = {
            "verdict": "PASS",
            "mutation_check": {"files": [{"path": "a.py", "mutants": [mutant(1)]}], "survivors": {}},
            "changed_code_evidence": {"obligations": [obligation(1)]},
        }
        cases.append(small)
        unnamed = packet()
        unnamed["regression_proof"] = {"changed_code_evidence": proof()["changed_code_evidence"]}
        del unnamed["regression_proof"]["changed_code_evidence"]["obligations"][7]["handle"]
        cases.append(unnamed)
        pointed = packet()
        pointed["regression_proof"] = {"changed_code_evidence": proof()["changed_code_evidence"]}
        pointed["changed_code_evidence"] = {"reason_ref": proof_detail.POINTER + "/3/reason"}
        cases.append(pointed)
        malformed = packet()
        malformed["regression_proof"] = {"mutation_check": {"files": ["x" * 70000], "survivors": ["y" * 70000]}}
        cases.append(malformed)
        already = packet()
        already["context_artifact"] = {"path": "/saved/context.json", "sha256": "a" * 64}
        cases.append(already)
        cases.extend([{"stage": "sol"}, {"stage": "sol", "regression_proof": None}])
        for original in cases:
            with self.subTest(stage=original.get("stage")), tempfile.TemporaryDirectory() as tmp:
                self.assertEqual((original, []), context.compact(original, Path(tmp) / "state.json"))
                self.assertFalse((Path(tmp) / "context").exists())


if __name__ == "__main__":
    unittest.main()
