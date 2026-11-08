"""Cache fixture reasons never cover real survivors or unknown producer bytes."""

import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scenarios.harness import fake_changed_code as fixture


class CacheFixtureReasonTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.root, self.reference = base / "project", base / "reference"
        scenario = Path(__file__).resolve().parents[1] / "scenarios/catalog/discuss-then-design-then-build"
        shutil.copytree(scenario / "seed", self.root)
        shutil.copytree(scenario / "reference", self.reference)
        shutil.copytree(self.reference, self.root, dirs_exist_ok=True)
        self.producer = {
            "reference": str(self.reference),
            "paths": list(fixture.CACHE_FIXTURE_PINS),
            "check": "python3 -m unittest discover -s tests -t .",
            "turn_paths": [["docs/decisions/"], ["docs/design/"], ["app/", "tests/"]],
        }
        self.proof = {
            "path": "current-proof.json",
            "verdict": "PASS",
            "source_revision": "a" * 64,
            "mutation_check": {
                "selected_tests": [
                    "tests.test_metadata.ParseTests.test_metadata_uses_configured_shared_cache_and_refreshes_at_one_hour",
                    *[
                        "tests.test_shared_cache.SharedFileCacheTests." + name
                        for name in (
                            "test_missing_nested_directory_and_corrupt_entry_are_replaced_and_reused",
                            "test_fresh_hit_returns_without_waiting_for_a_writer_lock",
                            "test_miss_rechecks_after_lock_when_another_worker_has_published",
                        )
                    ],
                ]
            },
            "changed_code_evidence": {"source_revision": "a" * 64, "proof_context": "b" * 64, "obligations": []},
        }
        self.gaps = []
        for path, lines in fixture.CACHE_FIXTURE_REASONS.items():
            for line in lines:
                identity = ["a" * 64, "b" * 64, path, fixture.CACHE_FIXTURE_PINS[path], line]
                gap = "changed-code:" + hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
                self.gaps.append(
                    {
                        "gap_id": gap,
                        "path": path,
                        "sha256": identity[3],
                        "line": line,
                        "status": "reason_required",
                        "mutation_outcomes": [],
                    }
                )
        self.proof["changed_code_evidence"]["obligations"] = self.gaps

    def judge(self, proof=None, producer=None):
        return fixture.judgments(
            {"regression_proof": self.proof if proof is None else proof},
            self.root,
            producer=self.producer if producer is None else producer,
        )

    def test_only_exact_scripted_imports_and_fallback_get_reasons(self):
        self.assertEqual({row["gap_id"] for row in self.gaps}, {row["gap_id"] for row in self.judge()})
        for tree in (self.root, self.reference):
            for name in fixture.CACHE_FIXTURE_PINS:
                p = tree / name
                old = p.read_bytes()
                p.write_bytes(old + b"\n# unknown\n")
                with self.subTest(tree=tree.name, path=name):
                    self.assertEqual([], self.judge())
                p.write_bytes(old)
        for change in (
            {"paths": []},
            {"reference": str(self.root)},
            {"check": "echo PASS"},
            {"turn_paths": [["docs/decisions/"], ["docs/design/", "app/"], ["app/", "tests/"]]},
        ):
            with self.subTest(change=change):
                self.assertEqual([], self.judge(producer={**self.producer, **change}))

    def test_stale_missing_failed_and_unexecuted_proofs_grant_no_authority(self):
        for change in (
            {"verdict": "FAIL"},
            {"source_revision": "old"},
            {"path": ""},
            {"mutation_check": {"selected_tests": []}},
        ):
            with self.subTest(change=change):
                self.assertEqual([], self.judge({**self.proof, **change}))
        for outcomes in (["survived"], ["not_run"], ["killed"]):
            for gap in self.gaps:
                proof = copy.deepcopy(self.proof)
                proof["changed_code_evidence"]["obligations"] = [{**gap, "mutation_outcomes": outcomes}]
                with self.subTest(gap=gap["gap_id"], outcomes=outcomes):
                    self.assertEqual([], self.judge(proof))
        for change in ({"gap_id": "old"}, {"sha256": "unknown"}, {"line": 1}, {"status": "tested"}):
            proof = copy.deepcopy(self.proof)
            proof["changed_code_evidence"]["obligations"] = [{**self.gaps[0], **change}]
            with self.subTest(change=change):
                self.assertEqual([], self.judge(proof))

    def test_borrowed_links_and_duplicate_gaps_are_refused(self):
        proof = copy.deepcopy(self.proof)
        proof["changed_code_evidence"]["obligations"] = [self.gaps[0]] * 2
        self.assertEqual([], self.judge(proof))
        for tree in (self.root, self.reference):
            path = tree / "app"
            held = tree / "held"
            path.rename(held)
            path.symlink_to(held, target_is_directory=True)
            with self.subTest(tree=tree.name):
                self.assertEqual([], self.judge())
            path.unlink()
            held.rename(path)
