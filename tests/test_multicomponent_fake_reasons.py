"""The multi-component fake Tester judges a surviving mutant only for the exact bytes, line and operator it names."""

import copy
import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_context as context

ROOT = Path(__file__).resolve().parent.parent
ANALYTICS = "components/analytics-service/server.py"
SOURCE = (ROOT / "scenarios/catalog/local-compose-two-services/reference" / ANALYTICS).read_text()


def load_fixture():
    with tempfile.TemporaryDirectory() as folder:
        manifest = Path(folder) / "manifest.json"
        manifest.write_text("{}")
        with mock.patch.dict(os.environ, {"FAKE_MANIFEST": str(manifest)}):
            spec = importlib.util.spec_from_file_location(
                "multicomponent_fake", ROOT / "tools/fixtures/multicomponent_fake.py"
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module


fixture = load_fixture()


def line_of(text):
    return SOURCE.splitlines().index(next(line for line in SOURCE.splitlines() if line.strip() == text)) + 1


def handoff(line, operator="drop_call", outcome="survived", source=SOURCE):
    digest = hashlib.sha256(source.encode()).hexdigest()
    row = {
        "gap_id": f"gap:{line}",
        "path": ANALYTICS,
        "sha256": digest,
        "line": line,
        "status": "reason_required",
        "mutation_outcomes": [outcome],
    }
    mutant = {"line": line, "operator": operator, "outcome": outcome}
    proof = {
        "path": "proof.json",
        "changed_code_evidence": {"obligations": [row]},
        "mutation_check": {"files": [{"path": ANALYTICS, "mutants": [mutant]}]},
    }
    return {"files": {ANALYTICS: source}, "check": "true"}, {"regression_proof": proof}


class EquivalentMutantReasonsTests(unittest.TestCase):
    def test_a_named_surviving_drop_call_gets_its_equivalent_mutant_reason(self):
        for text in ("connection.close()", 'self.rfile.read(int(self.headers.get("Content-Length", 0)))'):
            with self.subTest(text=text):
                (decision,) = fixture.changed_code_reasons(*handoff(line_of(text)))
                self.assertEqual(("accept", "equivalent_mutant"), (decision["decision"], decision["reason_kind"]))
                self.assertEqual(f"gap:{line_of(text)}", decision["gap_id"])
                self.assertEqual(["proof.json", ANALYTICS], decision["evidence_refs"])

    def test_no_reason_for_another_line_operator_outcome_or_file_bytes(self):
        close = line_of("connection.close()")
        content_length = line_of('self.send_header("Content-Length", str(len(body)))')
        changed = SOURCE.replace("timeout=3", "timeout=4")
        for spec, data in (
            handoff(content_length),
            handoff(close, operator="none_assignment"),
            handoff(close, outcome="not_run"),
            handoff(close, source=changed),
        ):
            with self.subTest(row=data["regression_proof"]["changed_code_evidence"]["obligations"][0]):
                self.assertEqual([], fixture.changed_code_reasons(spec, data))

    def test_one_mutant_of_another_kind_on_the_line_withholds_the_reason(self):
        spec, data = handoff(line_of("connection.close()"))
        proof = copy.deepcopy(data["regression_proof"])
        mutants = proof["mutation_check"]["files"][0]["mutants"]
        mutants.append({**mutants[0], "operator": "none_assignment"})
        self.assertEqual([], fixture.changed_code_reasons(spec, {"regression_proof": proof}))

    def test_compacted_tester_and_checkpoint_proofs_restore_full_rows_and_fail_closed(self):
        spec, data = handoff(line_of("connection.close()"))
        row = data["regression_proof"]["changed_code_evidence"]["obligations"][0]
        row["handle"] = "L1"
        row["source_line"] = "connection.close()"
        row["evidence"] = ["runner.log"] * 2000
        data["regression_proof"]["mutation_check"]["files"][0]["notes"] = "x" * 14000
        expected = fixture.quote_changed_lines(data, fixture.changed_code_reasons(spec, data))
        with tempfile.TemporaryDirectory() as folder:
            for stage in ("tester", "review_checkpoint"):
                state_path = Path(folder) / stage / "state.json"
                state_path.parent.mkdir(parents=True, exist_ok=True)
                packet, _, _ = context.compact_with_metrics(
                    {**copy.deepcopy(data), "stage": stage}, state_path, stage=stage
                )
                actual = fixture.quote_changed_lines(packet, fixture.changed_code_reasons(spec, packet))
                self.assertEqual(expected, actual)

            self.assertEqual([], fixture.changed_code_reasons(spec, []))
            self.assertIsNone(fixture._review_data([]))
            self.assertIsNone(fixture._review_data({"context_artifact": []}))
            self.assertIsNone(fixture._review_data({"context_artifact": {"fields": []}}))
            self.assertIsNone(
                fixture._review_data({"regression_proof": {"details_artifact": {"field": "regression_proof"}}})
            )
            self.assertIsNone(
                fixture._review_data({"regression_proof": {"mutation_check": {"files": [{"mutants": 1}]}}})
            )
            self.assertIsNone(
                fixture._review_data({"regression_proof": {"changed_code_evidence": {"obligations": [{"gaps": []}]}}})
            )
            art_path = Path(folder) / "bad.json"
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

    def test_compacted_proof_restores_full_mutants_obligations_and_quotes_or_fails_closed(self):
        line = line_of("connection.close()")
        source_line = SOURCE.splitlines()[line - 1].strip()
        spec, data = handoff(line)
        proof = data["regression_proof"]
        proof["mutation_check"]["files"][0]["mutants"].append(
            {"line": 1, "outcome": "killed", "evidence": ["runner.log"] * 6000}
        )
        proof["changed_code_evidence"]["obligations"][0].update(
            {
                "gap_id": "changed-code:" + "a" * 64,
                "handle": f"L{line}",
                "source_line": source_line,
                "evidence": ["runner.log"] * 6000,
            }
        )
        data["stage"] = "tester"
        expected = fixture.quote_changed_lines(data, fixture.changed_code_reasons(spec, data))
        self.assertEqual(1, len(expected))
        self.assertEqual(source_line, expected[0]["source_line"])

        with tempfile.TemporaryDirectory() as tmp:
            packet, moved = context.compact(data, Path(tmp) / "run/state.json")
            self.assertIn("regression_proof_mutation_files", moved)
            self.assertIn("regression_proof_obligations", moved)
            self.assertIsInstance(packet["regression_proof"]["mutation_check"]["files"][0]["mutants"], int)
            self.assertIn("gaps", packet["regression_proof"]["changed_code_evidence"]["obligations"][0])

            got = fixture.quote_changed_lines(packet, fixture.changed_code_reasons(spec, packet))
            self.assertEqual(expected, got)

            for tamper in (
                lambda p: p["context_artifact"].update(sha256="0" * 64),
                lambda p: p["context_artifact"]["fields"]["regression_proof_obligations"].update(total=99),
                lambda p: p.pop("context_artifact"),
            ):
                bad = copy.deepcopy(packet)
                tamper(bad)
                self.assertEqual([], fixture.changed_code_reasons(spec, bad))


if __name__ == "__main__":
    unittest.main()
