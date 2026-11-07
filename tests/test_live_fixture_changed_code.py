"""The handwritten greeting reviewer cannot waive unknown or weak fixtures."""

import contextlib
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

import live_fixture_provider as fixture


class GreetingReasonsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "greet.py").write_text(fixture.GREET_PY)
        (self.root / "test_greet.py").write_text(fixture.TEST_GREET_PY)
        digest = hashlib.sha256(fixture.GREET_PY.encode()).hexdigest()
        self.rows = [
            {
                "gap_id": f"gap:{line}",
                "path": "greet.py",
                "sha256": digest,
                "line": line,
                "status": "reason_required",
                "mutation_outcomes": [],
            }
            for line in range(1, len(fixture.GREET_PY.splitlines()) + 1)
        ]
        self.data = {
            "regression_proof": {"path": "runner-proof.json", "changed_code_evidence": {"obligations": self.rows}}
        }

    def decisions(self, data=None):
        with contextlib.chdir(self.root):
            return fixture._changed_code_reasons(data or self.data)

    def test_only_precise_known_declaration_and_unsupported_statement_reasons_are_given(self):
        decisions = self.decisions()
        source = fixture.GREET_PY.splitlines()
        known = {
            "import sys",
            'USAGE = "usage: greet.py NAME"',
            "def greet(name: str) -> str:",
            "def main(argv=None) -> int:",
            "print(greet(argv[0]))",
            'if __name__ == "__main__":',
            "raise SystemExit(main())",
        }
        self.assertEqual(known, {source[int(row["gap_id"].split(":")[1]) - 1].strip() for row in decisions})
        self.assertTrue(all(row["evidence_refs"] == ["runner-proof.json", "greet.py"] for row in decisions))
        self.assertEqual(len(decisions), len({row["reason"] for row in decisions}))

    def test_surviving_unrun_or_wrong_source_observations_never_get_a_waiver(self):
        for change in (
            {"mutation_outcomes": ["survived"]},
            {"mutation_outcomes": ["not_run"]},
            {"sha256": "0" * 64},
            {"path": "other.py"},
        ):
            data = copy.deepcopy(self.data)
            for row in data["regression_proof"]["changed_code_evidence"]["obligations"]:
                row.update(change)
            with self.subTest(change=change):
                self.assertEqual([], self.decisions(data))

    def test_changed_product_or_missing_cli_assertions_refuse_the_handwritten_judgment(self):
        for name, original in (("greet.py", fixture.GREET_PY), ("test_greet.py", fixture.TEST_GREET_PY)):
            with self.subTest(name=name):
                (self.root / name).write_text("# altered or weakened fixture\n")
                self.assertEqual([], self.decisions())
                (self.root / name).write_text(original)
