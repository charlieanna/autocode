"""Property-style tests: no generated state may complete without evidence (#705).

Fixed example counts, no real time, no provider. Shrunk counterexamples belong
as ordinary regression tests beside the module they failed.
"""

from __future__ import annotations

import copy
import itertools
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import autocode_brief_literals as brief_literals
import autocode_completion as completion_gate
import autocode_support as s
import autocode_verify as verify

COMPLETION_MUTATIONS = 40
LITERAL_CONTRACTS = 24
TOKENS = 12


class CompletionGateProperties(unittest.TestCase):
    """1. The gate never completes a state that fails one required rule."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@e.test",
                "commit",
                "--allow-empty",
                "-qm",
                "fixture",
            ],
            check=True,
        )
        self.evidence = self.root / "check.log"
        self.evidence.write_text("3 tests passed\n")
        self.criteria = [
            {"id": "C1", "criterion": "Contract holds", "status": "verified", "evidence": str(self.evidence)}
        ]
        self.state = {
            "version": 2,
            "workspace": str(self.root),
            "task": "Keep the contract",
            "status": "RUNNING",
            "acceptance_criteria": self.criteria,
            "criteria_revision": s.digest(s.criteria_definition(self.criteria)),
            "settings": {"roles": {}, "headroom": {"enabled": False}},
        }
        current = s.snapshot(self.root)
        self.state["validation"] = {
            "verdict": "PASS",
            "criteria_revision": self.state["criteria_revision"],
            "source_revision": current["revision"],
            "checks": [{"exit_code": 0}],
            "findings": [],
            "unverified_criteria": [],
            "criterion_results": [{"id": "C1", "status": "PASS", "evidence_refs": [str(self.evidence)]}],
            "evidence_hashes": {str(self.evidence): s.file_hash(self.evidence)},
        }
        self.decision = {
            "status": "TASK_COMPLETE",
            "acceptance_criteria": copy.deepcopy(self.criteria),
            "next_objective": "",
            "evidence": [str(self.evidence)],
            "blocker": "",
            "plan": ["Done"],
            "affected_paths": ["source.rb"],
        }
        self.current = current

    def ready(self, state=None, decision=None):
        return completion_gate.completion_ready(state or self.state, decision or self.decision, self.current)

    def test_the_unmutated_state_completes(self):
        self.assertTrue(self.ready())

    def test_every_required_rule_mutation_refuses_completion(self):
        mutations = []
        for status in ("FAIL", "NOT_VERIFIED", "UNVERIFIED", "", "verified"):
            if status == "verified":
                continue  # the valid status; other cases cover real mutations
            state_criteria = copy.deepcopy(self.criteria)
            state_criteria[0]["status"] = status
            decision_criteria = copy.deepcopy(state_criteria)
            mutations.append(
                (
                    f"criterion status {status!r}",
                    {"acceptance_criteria": state_criteria},
                    {"acceptance_criteria": decision_criteria},
                )
            )
        mutations += [
            (
                "criterion result not PASS",
                {
                    "validation": {
                        **self.state["validation"],
                        "criterion_results": [{"id": "C1", "status": "FAIL", "evidence_refs": [str(self.evidence)]}],
                    }
                },
                None,
            ),
            (
                "criterion result without evidence",
                {
                    "validation": {
                        **self.state["validation"],
                        "criterion_results": [{"id": "C1", "status": "PASS", "evidence_refs": []}],
                    }
                },
                None,
            ),
            (
                "PASS bound to another revision",
                {"validation": {**self.state["validation"], "source_revision": "0" * 40}},
                None,
            ),
            ("PASS without checks", {"validation": {**self.state["validation"], "checks": []}}, None),
            (
                "evidence hash altered",
                {"validation": {**self.state["validation"], "evidence_hashes": {str(self.evidence): "0" * 64}}},
                None,
            ),
            ("evidence hash dropped", {"validation": {**self.state["validation"], "evidence_hashes": {}}}, None),
            ("decision not COMPLETE", {}, {"status": "CONTINUE"}),
            (
                "decision criterion unverified",
                {},
                {"acceptance_criteria": [{**self.criteria[0], "status": "unverified", "evidence": ""}]},
            ),
        ]
        for name, state_patch, decision_patch in mutations[:COMPLETION_MUTATIONS]:
            with self.subTest(name=name):
                state, decision = copy.deepcopy(self.state), copy.deepcopy(self.decision)
                state.update(state_patch)
                if decision_patch:
                    decision.update(decision_patch)
                self.assertFalse(self.ready(state, decision), name)

    def test_a_human_review_criterion_without_its_token_refuses_on_v3(self):
        human = [{**self.criteria[0], "human_review": True}]
        revision = s.digest(s.criteria_definition(human))
        state = copy.deepcopy(self.state)
        state.update(
            version=3,
            acceptance_criteria=human,
            criteria_revision=revision,
            validation={**self.state["validation"], "criteria_revision": revision},
        )
        decision = copy.deepcopy(self.decision)
        decision.update(acceptance_criteria=human)
        self.assertFalse(self.ready(state, decision), "human review without a token must not complete")


class BriefLiteralProperties(unittest.TestCase):
    """2. A draft that drops a backticked brief literal is sent back, naming the row."""

    def test_dropping_a_literal_is_always_named(self):
        # Only non-template literals: a placeholder/alternation literal is a
        # template that a filled example can satisfy (see the next property).
        for literal in ["exit 0", "todo.py", "greet.py", "readme.md"]:
            for kept in (False, True):
                with self.subTest(literal=literal, kept=kept):
                    contract = {
                        "acceptance_criteria": [
                            {"id": "C1", "criterion": (f"Works with `{literal}`" if kept else "Does something else")}
                        ]
                    }
                    lost = brief_literals.missing([literal], contract)
                    self.assertEqual([] if kept else [literal], lost)
                    if lost:
                        message = brief_literals.error(lost)
                        self.assertIn(literal, message)
                        self.assertIn("drops literals", message)

    def test_a_filled_template_keeps_the_literal(self):
        for example in ("1 buy milk [open]", "2 walk dog [done]"):
            with self.subTest(example=example):
                contract = {"acceptance_criteria": [{"id": "C1", "criterion": f"Prints {example}"}]}
                lost = brief_literals.missing(["ID TEXT [open|done]"], contract)
                self.assertEqual([], lost)


class RegressionJudgeProperties(unittest.TestCase):
    """3. _judge_regression never passes a test that failed on the candidate."""

    @staticmethod
    def receipt(results, complete=True):
        return {
            "exit_code": 0,
            "timed_out": False,
            "interrupted": False,
            "error": "",
            "results_expected": True,
            "results": {
                "passed": sorted(results.get("passed", [])),
                "failed": sorted(results.get("failed", [])),
                "collection_errors": list(results.get("collection_errors", [])),
                "complete": complete,
            },
        }

    def test_a_failed_candidate_test_is_never_a_pass(self):
        # _judge_regression fills fail/unverified/notes; the caller turns those
        # into a verdict. An unexplained candidate failure must never be silent.
        for name in ("test_c1", "test_c2"):
            with self.subTest(name=name):
                proof: dict = {}
                candidate = self.receipt({"passed": ["test_ok"], "failed": [name]})
                # Passed on base, failed on the candidate: a regression.
                base = self.receipt({"passed": ["test_ok", name], "failed": []})
                notes: list[str] = []
                reasons: list[str] = []
                fail: list[str] = []
                unverified: list[str] = []
                verify._judge_regression(
                    candidate, base, fail, unverified, notes, proof, reasons, known_failures=lambda: set()
                )
                self.assertTrue(fail or unverified, (fail, unverified, notes, reasons))
                self.assertNotEqual([], fail, "a new candidate failure must be a failure")

    def test_a_preexisting_failure_is_noted_and_does_not_prove_the_fix(self):
        proof: dict = {}
        candidate = self.receipt({"passed": ["test_ok"], "failed": ["net"]})
        base = self.receipt({"passed": ["test_ok"], "failed": ["net"]})
        notes: list[str] = []
        fail: list[str] = []
        unverified: list[str] = []
        verify._judge_regression(candidate, base, fail, unverified, notes, proof, [], known_failures=lambda: {"net"})
        # The pre-existing failure is not counted as proof, so the run still
        # needs a fail-to-pass test — it cannot pass on this evidence alone.
        self.assertTrue(fail or unverified, (fail, unverified, notes))
        self.assertTrue(
            any("not counted" in note or "reproduce" in text for note in notes for text in [note] + fail + unverified),
            notes,
        )


class ApprovalTokenProperties(unittest.TestCase):
    """4. A token minted for one goal never equals a token for another."""

    def test_tokens_do_not_cross_goal_or_source(self):
        from autocode_goals import token

        pairs = [(f"goal-{index}", token({"revision": 1, "hash": f"goal-{index}"})) for index in range(TOKENS)]
        for (goal, minted), (other, rest) in itertools.combinations(pairs, 2):
            self.assertNotEqual(minted, rest, (goal, other))
        self.assertEqual("r1:goal-0", pairs[0][1])


if __name__ == "__main__":
    unittest.main()
