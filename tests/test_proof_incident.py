"""The Resolver reads the regression proof as a diagnosis needs it, not the judging stages' whole record (#4219)."""

import copy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode as runner
import autocode_goals as goals
import autocode_proof_incident as proof_incident
import autocode_support as support
from units import autoresolver

PROOF_PATH = "/run/regression/proof-07/verification.json"


def _mutant(line, outcome):
    return {
        "after": "None",
        "before": f"value_{line} + 1",
        "end_line": line,
        "evidence": [f"/run/regression/proof-07/mutation-check/mutant-{line:04d}-{'a' * 32}.log"],
        "failed_tests": [f"tests.test_engine.EngineTests.test_case_{n}" for n in range(6)]
        if outcome == "killed"
        else [],
        "line": line,
        "operator": "none_assignment",
        "outcome": outcome,
        "site_id": f"site-{line:04d}-{'b' * 56}",
        "source_sha256": "c" * 64,
        "survived_tests": [] if outcome == "killed" else ["tests.test_engine.EngineTests.test_case_0"],
    }


def _line(handle, line, status, source_line):
    return {
        "evidence": [f"/run/regression/proof-07/mutation-check/mutant-{line:04d}-{'a' * 32}.log"],
        "executed": True,
        "fallback": "none",
        "gap_id": f"changed-code:{line:064d}",
        "handle": handle,
        "line": line,
        "mutation_demonstrated": status == "tested",
        "mutation_outcomes": ["killed"] if status == "tested" else [],
        "path": "engine.py",
        "reason": "Current selected tests detect the changed line's mutants.",
        "sha256": "c" * 64,
        "source_line": source_line,
        "status": status,
    }


def _receipt(index, **extra):
    return {
        "command": f"env PYTHONPYCACHEPREFIX=/run/pycache/{index} python3 -m unittest -v "
        + " ".join(f"tests.test_engine.EngineTests.test_case_{n}" for n in range(6)),
        "duration_seconds": 1.5,
        "exit_code": 1,
        "output": f"/run/regression/proof-07/mutation-check/mutant-{index:04d}.log",
        "output_sha256": "d" * 64,
        "results": {f"tests.test_engine.EngineTests.test_case_{n}": "failed" for n in range(6)},
        "results_expected": True,
        "supervision": {"version": 1, "status": "ok"},
        "supervision_errors": [],
        "supervision_sha256": "e" * 64,
        "tail": "Traceback (most recent call last):\n  ...\nAssertionError: 3 != 4\n" * 8,
        "timed_out": False,
        **extra,
    }


def large_proof(lines=300):
    """A regression handoff for a large change: almost every line proven, two open, one mutant survived."""
    mutants = [_mutant(line, "killed") for line in range(1, lines + 1)]
    mutants[40] = _mutant(41, "survived")
    obligations = [
        _line(f"L{line}", line, "tested", f"value_{line} = value_{line} + 1") for line in range(1, lines + 1)
    ]
    obligations[2] = _line("L3", 3, "reason_required", "from __future__ import annotations")
    obligations[40] = _line("L41", 41, "reason_required", "value_41 = value_41 + 1")
    receipts = [_receipt(index) for index in range(lines)]
    receipts.append(_receipt(lines, timed_out=True, exit_code=None))
    return {
        "verdict": "PASS",
        "failures": [],
        "fail_to_pass": ["tests.test_engine.EngineTests.test_new_rule"],
        "pass_to_pass": ["tests.test_engine.EngineTests.test_case_0"],
        "commands": {"suite": "python3 -m unittest discover -s tests"},
        "checks": {"suite": {"command": "python3 -m unittest discover -s tests", "exit_code": 0}},
        "path": PROOF_PATH,
        "mutation_check": {
            "budget_seconds": 900,
            "collected": True,
            "survivors": 1,
            "selection": "all_eligible_changed_sites",
            "files": [{"path": "engine.py", "sha256": "c" * 64, "notes": [], "mutants": mutants}],
        },
        "changed_code_inputs": {
            "custody_gaps": [],
            "installed_dependencies": {"directories": [], "files": [], "reason": "none"},
            "mutation": {"files": [{"path": "engine.py", "mutants": copy.deepcopy(mutants)}]},
            "command_outputs": [row["output"] for row in receipts],
            "line_texts": {"engine.py": {str(line): f"value_{line} = value_{line} + 1" for line in range(lines)}},
        },
        "changed_code_evidence": {
            "schema": 1,
            "status": "UNVERIFIED",
            "source_revision": "r" * 64,
            "proof_context": "p" * 64,
            "obligations": obligations,
        },
        "changed_code_receipts": receipts,
    }


class ProofViewTests(unittest.TestCase):
    def test_open_lines_unkilled_mutants_and_abnormal_receipts_stay_whole(self):
        proof = large_proof()
        view = proof_incident.view(proof)
        evidence = view["changed_code_evidence"]
        self.assertEqual(["L3", "L41"], [row["handle"] for row in evidence["obligations"]])
        self.assertEqual(proof["changed_code_evidence"]["obligations"][2], evidence["obligations"][0])
        self.assertEqual(298, len(evidence["settled_lines"]["tested"]["engine.py"]))
        mutants = view["mutation_check"]["files"][0]
        self.assertEqual([proof["mutation_check"]["files"][0]["mutants"][40]], mutants["mutants"])
        self.assertEqual(299, len(mutants["settled_mutant_lines"]["killed"]))
        self.assertEqual([proof["changed_code_receipts"][-1]], view["changed_code_receipts"])
        self.assertEqual({"total": 301, "shown": 1}, view["changed_code_receipts_listed"])
        self.assertNotIn("mutation", view["changed_code_inputs"])
        self.assertIn("custody_gaps", view["changed_code_inputs"])
        for key in ("verdict", "failures", "fail_to_pass", "pass_to_pass", "commands", "checks", "path"):
            self.assertEqual(proof[key], view[key])
        self.assertIn("regression_proof.path", view["incident_view"]["read"])

    def test_the_view_shrinks_with_the_settled_record_not_with_a_byte_limit(self):
        small, large = large_proof(lines=50), large_proof(lines=600)
        size = lambda value: len(json.dumps(proof_incident.view(value)))  # noqa: E731
        self.assertLess(size(large), len(json.dumps(large)) / 20)
        # Only the line numbers of settled work grow; every open row and survivor is carried at any size.
        self.assertLess(size(large) - size(small), 15_000)

    def test_a_proof_without_the_per_line_record_is_unchanged(self):
        proof = {"verdict": "FAIL", "failures": ["tests.test_engine fails on base"], "path": PROOF_PATH}
        self.assertEqual(proof, proof_incident.view(proof))
        self.assertIsNone(proof_incident.view(None))


class ResolverHandoffTests(unittest.TestCase):
    def upstream(self, proof):
        payload = {"task": "Speed up the engine.", "regression_proof": proof, "validation": {"verdict": "REWORK"}}
        return SimpleNamespace(
            schema=goals.role_schema(support.read(runner.SCHEMA_DIR / "v2/astra-decision.schema.json"), "task_planner"),
            metrics={},
            prompt="Review it.\nCURRENT HANDOFF DATA\n" + json.dumps(payload),
        )

    def handoff(self, prompt):
        return json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])

    def state(self):
        return {
            "workspace": "/ws",
            "settings": {"roles": {"task_planner": {"engine": "claude", "model": "fixture"}}},
            "resolution_request": {"source_revision": "r", "review": {"status": "REWORK"}},
            "diagnosis_request": {"source_revision": "r", "repeated_count": 3},
        }

    def test_the_resolver_gets_the_incident_view_of_the_proof(self):
        proof = large_proof()
        with (
            patch.object(autoresolver, "guard"),
            patch.object(autoresolver, "execution_request", return_value=self.upstream(proof)),
        ):
            request = autoresolver.prepare(self.state(), "resolve", Path("/run/state.json"), runner.SCHEMA_DIR)
        carried = self.handoff(request.prompt)["regression_proof"]
        self.assertEqual(proof_incident.view(proof), carried)
        self.assertEqual(["L3", "L41"], [row["handle"] for row in carried["changed_code_evidence"]["obligations"]])
        self.assertLess(len(request.prompt), len(json.dumps(proof, indent=2)) / 10)

    def test_its_diagnosis_gets_the_same_view(self):
        proof = large_proof()
        with (
            patch.object(autoresolver, "diagnosis_guard"),
            patch.object(autoresolver, "execution_request", return_value=self.upstream(proof)),
        ):
            request = autoresolver.prepare_diagnosis(
                self.state(), "diagnose", Path("/run/state.json"), runner.SCHEMA_DIR
            )
        self.assertEqual(proof_incident.view(proof), self.handoff(request.prompt)["regression_proof"])


if __name__ == "__main__":
    unittest.main()
