"""Fail-closed controls for the offline SemVer scripted Tester."""

import ast
import copy
import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "semver-reference-m1"
SOURCE_HASHES = {
    "src/semver/__init__.py": "e03edce6cf0d508b28fac1ad2364e43e636f479bd642a0046405708ef1484f81",
    "src/semver/jsonio.py": "7f680a59850141ee59262c3eb11a4b5c5aa738fee115212d1fb9110f5197c537",
    "src/semver/version.py": "b873ee66dac37631f47e2b2f89995bddbdfa50dbfab4e985f4f42bc735c3ce23",
    "tests/test_json.py": "9e46de969c4882d90dd2fa521bf76e76996cc6ced088db71a5c0fb8200aee699",
}


class ScriptedTesterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        self.prior_cwd = Path.cwd()
        os.chdir(self.workspace)
        self.addCleanup(os.chdir, self.prior_cwd)
        self.bytes_to_digest = {}
        for path, digest in SOURCE_HASHES.items():
            target = self.workspace / path
            target.parent.mkdir(parents=True, exist_ok=True)
            content = path.encode()
            target.write_bytes(content)
            self.bytes_to_digest[content] = digest
        for name in ("control-grounded.py", "control-api.py", "control-delimiters.py"):
            path = FIXTURES / name
            if path.exists():
                shutil.copyfile(path, self.root / name)
        (self.root / "writer-transitions.jsonl").write_text("broad-diagnostics-compatible.py\n")
        self.calls = []
        self.fake = SimpleNamespace(
            OUTPUT_SCHEMA={
                "properties": {"changed_code_exceptions": {"items": {"properties": {"gap_id": {"enum": ["gap"]}}}}}
            },
            run_verify=lambda command: (self.calls.append(command) or 0, ""),
        )
        self.proof = {
            "verdict": "PASS",
            "changed_code_evidence": {"obligations": []},
            "mutation_check": {
                "collected": True,
                "selection": "all_eligible_changed_sites",
                "test_selection": {"unobserved_mutants": 0},
                "files": [
                    {
                        "path": path,
                        "sha256": digest,
                        "mutants": [
                            {
                                "outcome": "killed",
                                "source_sha256": digest,
                                "failed_tests": ["test"],
                                "evidence": "event",
                            }
                        ],
                    }
                    for path, digest in SOURCE_HASHES.items()
                    if path.startswith("src/")
                ],
            },
        }

        class Hash:
            def __init__(self, content):
                self.value = self_map.get(content)
                self.content = content

            def hexdigest(self):
                return self.value or hashlib.sha256(self.content).hexdigest()

        self_map = self.bytes_to_digest
        tree = ast.parse((ROOT / "tests" / "semver_reference_provider.py").read_text())
        function = next(
            node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "semantic_review"
        )
        namespace = {
            "previous_tester": lambda *_: {"checks_run": [], "checks": []},
            "fake_changed_code": SimpleNamespace(_review_data=lambda _: {"regression_proof": self.proof}),
            "fake": self.fake,
            "root": self.root,
            "Path": Path,
            "hashlib": SimpleNamespace(sha256=Hash),
            "shlex": __import__("shlex"),
            "sys": __import__("sys"),
        }
        exec(
            compile(
                ast.Module(body=[function], type_ignores=[]),
                str(ROOT / "tests" / "semver_reference_provider.py"),
                "exec",
            ),
            namespace,
        )
        self.review = namespace["semantic_review"]

    def gap(self, line=11, source_line="from typing import List"):
        return {
            "status": "reason_required",
            "path": "src/semver/jsonio.py",
            "line": line,
            "source_line": source_line,
            "sha256": SOURCE_HASHES["src/semver/jsonio.py"],
            "gap_id": "gap",
        }

    def test_exact_delimiter_is_accepted_only_with_its_own_control(self):
        self.proof["changed_code_evidence"]["obligations"] = [self.gap(89, ")")]
        report = self.review("tester", {})
        self.assertEqual(report["changed_code_exceptions"][0]["evidence_refs"], ["check:2", "tests/test_json.py"])
        self.assertIn("control-delimiters.py", self.calls[1])

    def test_known_structural_gap_remains_reviewable(self):
        self.proof["changed_code_evidence"]["obligations"] = [self.gap()]
        report = self.review("tester", {})
        self.assertEqual(report["changed_code_exceptions"][0]["evidence_refs"], ["check:1", "tests/test_json.py"])

    def test_unknown_gap_or_untrusted_inventory_is_refused(self):
        mutations = (
            ("unknown line", lambda gap, proof: gap.update(line=777)),
            ("wrong source hash", lambda gap, proof: gap.update(sha256="0" * 64)),
            ("survived", lambda gap, proof: gap.update(mutation_outcomes=["survived"])),
            ("unrun", lambda gap, proof: gap.update(mutation_outcomes=["not_run"])),
            ("overlapping survivor", lambda gap, proof: gap.update(overlapping_mutations=[{"outcome": "survived"}])),
            ("omitted overlap", lambda gap, proof: gap.update(overlapping_mutations_omitted=1)),
            ("uncollected", lambda gap, proof: proof["mutation_check"].update(collected=False)),
            ("partial selection", lambda gap, proof: proof["mutation_check"].update(selection="partial")),
            ("missing files", lambda gap, proof: proof["mutation_check"].update(files=[])),
            ("unobserved", lambda gap, proof: proof["mutation_check"]["test_selection"].update(unobserved_mutants=1)),
            (
                "surviving inventory",
                lambda gap, proof: proof["mutation_check"]["files"][0]["mutants"][0].update(outcome="survived"),
            ),
            (
                "unrun inventory",
                lambda gap, proof: proof["mutation_check"]["files"][0]["mutants"][0].update(outcome="not_run"),
            ),
            (
                "missing failed test",
                lambda gap, proof: proof["mutation_check"]["files"][0]["mutants"][0].update(failed_tests=[]),
            ),
            (
                "missing evidence",
                lambda gap, proof: proof["mutation_check"]["files"][0]["mutants"][0].update(evidence=None),
            ),
        )
        original = copy.deepcopy(self.proof)
        for label, mutate in mutations:
            with self.subTest(control=label):
                self.calls.clear()
                self.proof = copy.deepcopy(original)
                gap = self.gap()
                self.proof["changed_code_evidence"]["obligations"] = [gap]
                mutate(gap, self.proof)
                with self.assertRaises(SystemExit):
                    self.review("tester", {})
                self.assertEqual(self.calls, [])
        self.proof = copy.deepcopy(original)
        self.calls.clear()
        gap = self.gap(89, "]")
        self.proof["changed_code_evidence"]["obligations"] = [gap]
        with self.assertRaises(SystemExit):
            self.review("tester", {})
        self.assertEqual(self.calls, [])

    def test_changed_source_is_refused(self):
        self.proof["changed_code_evidence"]["obligations"] = [self.gap()]
        (self.workspace / "src/semver/jsonio.py").write_text("tampered source")
        with self.assertRaises(SystemExit):
            self.review("tester", {})
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
