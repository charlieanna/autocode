"""The handwritten greeting reviewer cannot waive unknown or weak fixtures."""

import contextlib
import copy
import hashlib
import json
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

    def test_compacted_tester_and_checkpoint_proofs_restore_full_rows_and_fail_closed(self):
        lines = fixture.GREET_PY.splitlines()
        padded = copy.deepcopy(self.data)
        for index, row in enumerate(padded["regression_proof"]["changed_code_evidence"]["obligations"], start=1):
            row["handle"] = f"L{index}"
            row["source_line"] = lines[row["line"] - 1].strip()
            row["evidence"] = ["runner.log"] * 1200
        expected = fixture._quote_changed_lines(padded, self.decisions(padded))
        for stage in ("tester", "review_checkpoint"):
            state_path = self.root / stage / "state.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            packet, _, _ = context.compact_with_metrics(
                {**copy.deepcopy(padded), "stage": stage}, state_path, stage=stage
            )
            with contextlib.chdir(self.root):
                actual = fixture._quote_changed_lines(packet, fixture._changed_code_reasons(packet))
            self.assertEqual(expected, actual)

        self.assertEqual([], fixture._changed_code_reasons([]))
        self.assertIsNone(fixture._review_data([]))
        self.assertIsNone(fixture._review_data({"context_artifact": []}))
        self.assertIsNone(fixture._review_data({"context_artifact": {"fields": []}}))
        self.assertIsNone(
            fixture._review_data({"regression_proof": {"details_artifact": {"field": "regression_proof"}}})
        )
        self.assertIsNone(fixture._review_data({"regression_proof": {"mutation_check": {"files": [{"mutants": 1}]}}}))
        self.assertIsNone(
            fixture._review_data({"regression_proof": {"changed_code_evidence": {"obligations": [{"gaps": []}]}}})
        )
        art_path = self.root / "bad.json"
        art_path.write_bytes(b"[]")
        self.assertIsNone(
            fixture._review_data(
                {
                    "context_artifact": {
                        "path": str(art_path),
                        "sha256": hashlib.sha256(b"[]").hexdigest(),
                        "fields": {"regression_proof": {"total": 1}},
                    }
                }
            )
        )
        raw = json.dumps({"regression_proof": {"path": "p"}}).encode()
        art_path.write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        self.assertIsNone(
            fixture._review_data(
                {
                    "context_artifact": {
                        "path": str(art_path),
                        "sha256": "0" * 64,
                        "fields": {"regression_proof": {"total": 1}},
                    }
                }
            )
        )
        self.assertIsNone(
            fixture._review_data(
                {
                    "context_artifact": {
                        "path": str(art_path),
                        "sha256": digest,
                        "fields": {"regression_proof": {"total": 2}},
                    }
                }
            )
        )
        raw_rows = json.dumps({"regression_proof_obligations": [{"gap_id": "g"}]}).encode()
        art_path.write_bytes(raw_rows)
        digest_rows = hashlib.sha256(raw_rows).hexdigest()
        self.assertIsNone(
            fixture._review_data(
                {
                    "regression_proof": ["bad"],
                    "context_artifact": {
                        "path": str(art_path),
                        "sha256": digest_rows,
                        "fields": {"regression_proof_obligations": {"total": 1}},
                    },
                }
            )
        )
        self.assertIsNone(
            fixture._review_data(
                {
                    "regression_proof": {"path": "p"},
                    "context_artifact": {
                        "path": str(art_path),
                        "sha256": digest_rows,
                        "fields": {"regression_proof_obligations": {"total": 2}},
                    },
                }
            )
        )
        restored = fixture._review_data(
            {
                "regression_proof": {"path": "p"},
                "context_artifact": {
                    "path": str(art_path),
                    "sha256": digest_rows,
                    "fields": {"regression_proof_obligations": {"total": 1}},
                },
            }
        )
        self.assertEqual([{"gap_id": "g"}], restored["regression_proof"]["changed_code_evidence"]["obligations"])
        self.assertIsNone(
            fixture._review_data(
                {
                    "context_artifact": {
                        "path": str(art_path) + ".missing",
                        "sha256": digest_rows,
                        "fields": {"regression_proof_obligations": {"total": 1}},
                    }
                }
            )
        )
