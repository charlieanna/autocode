"""Prompt compression must preserve the information needed to reject bad work."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_context as context
import autocode_stage_context as stage_context


def packet(stage="astra_review"):
    criteria = [
        {
            "id": f"C{i}",
            "criterion": f"Preserve requirement {i}: " + "exact behavior " * 20,
            "verification_method": f"Run independent check {i}",
            "human_review": i == 18,
        }
        for i in range(1, 19)
    ]
    command = "python3 -c '" + "assert 1 == 1; " * 100 + "'"
    return {
        "stage": stage,
        "acceptance_criteria": [{k: c[k] for k in ("id", "criterion")} for c in criteria],
        "goal_contract": {
            "hash": "approved-hash",
            "revision": 3,
            "body": {"acceptance_criteria": criteria, "constraints": ["Never modify authentication"]},
        },
        "validation": {
            "verdict": "FAIL",
            "checks": [{"command": command, "exit_code": 0}],
            "check_replay": {
                "verdict": "FAIL",
                "source_revision": "current-source",
                "checks": [
                    {
                        "command": command,
                        "exit_code": 1,
                        "reported_exit_code": 0,
                        "timed_out": False,
                        "output": "runner.log",
                        "output_sha256": "proof",
                        "error": "independent failure",
                        "tail": "AssertionError",
                    },
                    {"command": "unreported check", "exit_code": 1},
                ],
            },
        },
        "saved_answers": {"old-denial": {"answer": "Do not deploy"}},
        "brief_feedback": [{"text": "Authentication must remain unchanged"}],
        "human_reviews": {"C18": {"approved": False}},
        "unresolved_findings": [{"id": "F1", "severity": "blocking"}],
    }


def resolve(document, pointer):
    value = document
    for part in pointer.removeprefix("#/").split("/"):
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


class ReviewHandoffTests(unittest.TestCase):
    def test_scripted_reviewers_return_full_criteria_from_compact_prompts(self):
        original = packet()
        original["current_task"] = {"id": "task", "milestone_id": "M1"}
        small, _ = context.compact(original, "/nonexistent/state.json")
        self.assertIn("acceptance_criteria_ref", small)
        tools = Path(__file__).resolve().parents[1] / "tools"
        with tempfile.TemporaryDirectory() as tmp:
            for name, output_flag in (
                ("fake_codex.py", "-o"),
                ("fake_command_tool.py", "--report"),
                ("fake_parallel_builder.py", "-o"),
            ):
                for data in (original, small):
                    with self.subTest(provider=name, compact=data is small):
                        output = Path(tmp) / "report.json"
                        result = subprocess.run(
                            [sys.executable, str(tools / name), output_flag, str(output)],
                            input="CURRENT HANDOFF DATA\n" + json.dumps(data),
                            text=True,
                            capture_output=True,
                            cwd=tmp,
                            timeout=20,
                        )
                        self.assertEqual(0, result.returncode, result.stderr)
                        report = json.loads(output.read_text())
                        self.assertEqual(
                            original["acceptance_criteria"],
                            [{k: c[k] for k in ("id", "criterion")} for c in report["acceptance_criteria"]],
                        )

    def test_scripted_builder_preserves_requirements_and_builds_from_compact_handoff(self):
        original = packet("terra")
        requirements = [row["criterion"] for row in original["acceptance_criteria"]]
        original["current_task"] = {"id": "task", "milestone_id": "M1", "requirements": requirements}
        original["actionable_findings"] = []
        small, _ = context.compact(original, "/nonexistent/state.json")
        self.assertIn("acceptance_criteria_ref", small)
        provider = Path(__file__).resolve().parents[1] / "tools" / "fake_codex.py"
        for data in (original, small):
            with self.subTest(compact=data is small), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "report.json"
                process = subprocess.run(
                    [sys.executable, str(provider), "-o", str(output)],
                    input="CURRENT HANDOFF DATA\n" + json.dumps(data),
                    cwd=tmp,
                    text=True,
                    capture_output=True,
                    timeout=20,
                )
                self.assertEqual(0, process.returncode, process.stderr)
                report = json.loads(output.read_text())
                self.assertEqual(requirements, report["addressed_requirements"])
                for argument, code, stdout in (("Ada", 0, "Hello, Ada\n"), ("", 2, "")):
                    result = subprocess.run(
                        [sys.executable, "greet.py", argument], cwd=tmp, text=True, capture_output=True, timeout=20
                    )
                    self.assertEqual((code, stdout), (result.returncode, result.stdout))

    def test_duplicates_shrink_without_losing_rejection_evidence(self):
        for stage in ("terra", "sol", "astra_review", "astra_checkpoint"):
            with self.subTest(stage=stage):
                original = packet(stage)
                before = copy.deepcopy(original)
                small, moved = context.compact(original, "/nonexistent/state.json")
                self.assertEqual(before, original)
                self.assertEqual([], moved)
                self.assertLess(len(json.dumps(small)), len(json.dumps(original)) * 0.8)
                ref = small["acceptance_criteria_ref"]
                self.assertEqual(
                    original["acceptance_criteria"],
                    [{k: c[k] for k in ref["fields"]} for c in resolve(small, ref["path"])],
                )
                restored = copy.deepcopy(small["validation"])
                for row in restored["check_replay"]["checks"]:
                    if "command_ref" in row:
                        row["command"] = resolve(small, row.pop("command_ref"))
                self.assertEqual(original["validation"], restored)
                for key in ("goal_contract", "saved_answers", "brief_feedback", "human_reviews", "unresolved_findings"):
                    self.assertEqual(original[key], small[key])
                self.assertEqual(small, context.compact(small, "/nonexistent/state.json")[0])

    def test_repeated_changed_line_reasons_and_receipts_become_exact_references(self):
        gap = "changed-code:" + "a" * 64
        other = "changed-code:" + "b" * 64
        reason = "Go mutation tooling is unavailable; line 48 is executed by the Build tests " * 3
        receipt = [{"command": "go test ./...", "supervision": {"receipt": "r" * 120}}]
        original = packet("astra_review")
        original["regression_proof"] = {
            "diff_coverage": {"command_receipts": copy.deepcopy(receipt)},
            "changed_code_receipts": copy.deepcopy(receipt),
            "changed_code_evidence": {
                "obligations": [
                    {"gap_id": gap, "line": 48, "reason": reason, "exception": {"reason": reason, "evidence": ["e1"]}},
                    {"gap_id": other, "line": 56, "reason": reason + " other", "exception": {"reason": "own"}},
                ]
            },
        }
        original["validation"]["changed_code_decisions"] = [
            {"gap_id": gap, "accepted": True, "reason": reason},
            {"gap_id": other, "accepted": False, "reason": reason},
        ]
        original["validation"]["changed_code_exceptions"] = [{"gap_id": gap, "decision": "accept", "reason": reason}]
        before = copy.deepcopy(original)
        small, _ = context.compact(original, "/nonexistent/state.json")
        self.assertEqual(before, original)
        self.assertLess(len(json.dumps(small)), len(json.dumps(original)))
        self.assertIn("reason_ref", small["handoff_reference_policy"])
        proof = small["regression_proof"]
        self.assertNotIn("changed_code_receipts", proof)
        self.assertEqual(receipt, resolve(small, proof["changed_code_receipts_ref"]))
        first, second = proof["changed_code_evidence"]["obligations"]
        self.assertEqual(reason, first["reason"])
        self.assertEqual(reason, resolve(small, first["exception"]["reason_ref"]))
        # A different gap, or a different reason for the same gap, keeps its own text.
        self.assertEqual("own", second["exception"]["reason"])
        decisions = small["validation"]["changed_code_decisions"]
        self.assertEqual(reason, resolve(small, decisions[0]["reason_ref"]))
        self.assertEqual(reason, decisions[1]["reason"])
        self.assertEqual(reason, resolve(small, small["validation"]["changed_code_exceptions"][0]["reason_ref"]))
        # Resolving every reference restores the original packet exactly.
        restored = copy.deepcopy(small)

        def expand(node):
            if isinstance(node, dict):
                for key in [k for k in node if k in ("reason_ref", "changed_code_receipts_ref")]:
                    node[key.removesuffix("_ref")] = copy.deepcopy(resolve(small, node.pop(key)))
                for value in node.values():
                    expand(value)
            elif isinstance(node, list):
                for value in node:
                    expand(value)

        expand(restored["regression_proof"])
        expand(restored["validation"])
        for row in restored["validation"]["check_replay"]["checks"]:
            if "command_ref" in row:
                row["command"] = resolve(small, row.pop("command_ref"))
        self.assertEqual(original["regression_proof"], restored["regression_proof"])
        self.assertEqual(original["validation"], restored["validation"])
        self.assertEqual(small, context.compact(small, "/nonexistent/state.json")[0])

    def test_coverage_receipts_leading_the_changed_code_receipts_become_exact_references(self):
        # The changed-code receipts are the coverage run's followed by the mutation runs', so the
        # whole-list reference never applied once a mutant ran and each coverage receipt went twice.
        coverage = [{"command": f"coverage run {n}", "supervision": {"receipt": "r" * 400}} for n in range(2)]
        mutant = {"command": "unittest mutant-01", "supervision": {"receipt": "m" * 400}}
        tiny = {"command": "x"}
        for stage in ("sol", "astra_review"):
            with self.subTest(stage=stage):
                original = packet(stage)
                original["regression_proof"] = {
                    "diff_coverage": {"command_receipts": copy.deepcopy(coverage)},
                    "changed_code_receipts": [*copy.deepcopy(coverage), mutant],
                }
                before = copy.deepcopy(original)
                small, _ = context.compact(original, "/nonexistent/state.json")
                self.assertEqual(before, original)
                receipts = small["regression_proof"]["changed_code_receipts"]
                self.assertEqual(
                    [
                        {"receipt_ref": "#/regression_proof/diff_coverage/command_receipts/0"},
                        {"receipt_ref": "#/regression_proof/diff_coverage/command_receipts/1"},
                        mutant,
                    ],
                    receipts,
                )
                self.assertIn("receipt_ref", small["handoff_reference_policy"])
                restored = [resolve(small, row["receipt_ref"]) if "receipt_ref" in row else row for row in receipts]
                self.assertEqual(original["regression_proof"]["changed_code_receipts"], restored)
                self.assertEqual(small, context.compact(small, "/nonexistent/state.json")[0])
                # A receipt that differs, or one too small to be worth a pointer, stays inline.
                original["regression_proof"]["changed_code_receipts"] = [
                    {**coverage[0], "exit_code": 1},
                    *copy.deepcopy(coverage[1:]),
                ]
                kept = context.compact(original, "/nonexistent/state.json")[0]["regression_proof"]
                self.assertEqual(original["regression_proof"]["changed_code_receipts"], kept["changed_code_receipts"])
                original["regression_proof"] = {
                    "diff_coverage": {"command_receipts": [tiny]},
                    "changed_code_receipts": [tiny, mutant],
                }
                kept = context.compact(original, "/nonexistent/state.json")[0]["regression_proof"]
                self.assertEqual([tiny, mutant], kept["changed_code_receipts"])

    def test_different_criteria_or_commands_are_never_conflated(self):
        for stage in ("terra", "sol", "astra_review", "astra_checkpoint"):
            with self.subTest(stage=stage):
                original = packet(stage)
                original["acceptance_criteria"][0]["criterion"] = "Different approved wording"
                original["validation"]["check_replay"]["checks"][0]["command"] += " --different"
                self.assertEqual((original, []), context.compact(original, "/nonexistent/state.json"))

    def test_planning_and_legacy_packets_keep_their_shape(self):
        for stage in ("astra_discovery", "astra_plan"):
            original = packet(stage)
            self.assertEqual((original, []), context.compact(original, "/nonexistent/state.json"))
        original = {"stage": "sol", "acceptance_criteria": [{"id": "C1", "criterion": "Legacy"}]}
        self.assertEqual((original, []), context.compact(original, "/nonexistent/state.json"))

    def test_small_packets_are_not_made_larger(self):
        original = {
            "stage": "sol",
            "acceptance_criteria": [{"id": "C1", "criterion": "Works"}],
            "goal_contract": {"body": {"acceptance_criteria": [{"id": "C1", "criterion": "Works"}]}},
        }
        self.assertEqual((original, []), context.compact(original, "/nonexistent/state.json"))

    def test_actual_stage_prompt_keeps_the_complete_contract_and_counts_reduced_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = packet()
            state.update(
                version=3,
                task="Implement all 18 requirements",
                workspace=tmp,
                settings={"roles": {r: {"engine": "codex", "model": "fake"} for r in ("task_planner", "terra", "sol")}},
                answers=state["saved_answers"],
            )
            before = copy.deepcopy(state)
            with patch.object(stage_context.support, "snapshot", return_value={"revision": "r", "head": "h"}):
                for stage in ("terra", "sol", "astra_review", "astra_checkpoint"):
                    prompt, metrics = stage_context.context_packet(state, stage, Path(tmp) / "state.json")
                    data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
                    self.assertIn("acceptance_criteria_ref", data)
                    self.assertEqual(state["goal_contract"], data["goal_contract"])
                    self.assertEqual(state["saved_answers"], data["saved_answers"])
                    self.assertEqual((len(prompt.encode()) + 3) // 4, metrics["estimated_prompt_tokens"])
            self.assertEqual(before, state)
