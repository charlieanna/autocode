"""The handwritten greeting reviewer cannot waive unknown or weak fixtures."""

import contextlib
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

import autocode_context as context
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

    def test_compacted_obligations_restore_gap_ids_and_quoted_lines_or_fail_closed(self):
        source = fixture.GREET_PY.splitlines()
        compact_rows = [
            {
                **row,
                "gap_id": f"changed-code:{row['line']}:" + "c" * 50,
                "handle": f"L{row['line']}",
                "source_line": source[row["line"] - 1].strip(),
                "evidence": ["runner.log"] * 6000,
            }
            for row in copy.deepcopy(self.rows)
        ]
        compact_data = {
            "stage": "tester",
            "regression_proof": {"path": "runner-proof.json", "changed_code_evidence": {"obligations": compact_rows}},
        }
        with contextlib.chdir(self.root):
            expected = fixture._quote_changed_lines(compact_data, fixture._changed_code_reasons(compact_data))
            self.assertEqual(7, len(expected))
            self.assertTrue(all(row["source_line"] for row in expected))
            packet, moved = context.compact(compact_data, self.root / "run/state.json")
            self.assertIn("regression_proof_obligations", moved)
            self.assertIn("gaps", packet["regression_proof"]["changed_code_evidence"]["obligations"][0])
            self.assertEqual(expected, fixture._quote_changed_lines(packet, fixture._changed_code_reasons(packet)))
            packet["context_artifact"]["sha256"] = "0" * 64
            self.assertEqual([], fixture._changed_code_reasons(packet))
            packet.pop("context_artifact")
            self.assertEqual([], fixture._changed_code_reasons(packet))
