"""Patch-relative review verdict through real fake-provider CLI and owned probes."""

import difflib
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from autocode_taskrun import TaskRun
from harness import catalog
from harness.driver import fake_setup, saved_run_flags


@unittest.skipUnless(sys.platform == "linux", "supervised fake-provider checks require supported Linux")
class ReviewAttributionCLI(unittest.TestCase):
    def test_unrelated_patch_does_not_make_an_existing_defect_block_the_review(self):
        with tempfile.TemporaryDirectory(prefix="review-attribution-cli-") as tmp:
            root = Path(tmp).resolve()
            project = root / "project"
            project.mkdir()
            original = "def double(n):\n    return n * 2\n\ndef parse(value):\n    return None\n"
            (project / "calc.py").write_text(original)
            changed = original.replace("return n * 2", "return n + 2")
            reviewed_patch = "diff --git a/calc.py b/calc.py\n" + "".join(
                difflib.unified_diff(
                    original.splitlines(keepends=True),
                    changed.splitlines(keepends=True),
                    fromfile="a/calc.py",
                    tofile="b/calc.py",
                )
            )
            (project / "change.patch").write_text(reviewed_patch)
            for args in (
                ("init", "-q"),
                ("add", "-A"),
                ("-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "base"),
            ):
                subprocess.run(["git", *args], cwd=project, check=True, capture_output=True)
            subprocess.run(["git", "apply", "--check", "change.patch"], cwd=project, check=True, capture_output=True)
            solution = root / "solution"
            tests = solution / "review/tests"
            tests.mkdir(parents=True)
            (tests / "test_parse.py").write_text(
                "import unittest\nfrom calc import parse\n\nclass Probe(unittest.TestCase):\n"
                "    def test_f1_empty(self):\n        self.assertEqual(0, parse(''))\n"
            )
            (solution / "review/findings.json").write_text(
                json.dumps(
                    {
                        "verdict": "request_changes",
                        "findings": [
                            {
                                "id": "F1",
                                "severity": "blocking",
                                "file": "calc.py",
                                "lines": [5],
                                "summary": "Empty parse returns None",
                                "evidence": "Focused empty-input assertion",
                                "example": "Given empty input, parse returns None; expected 0",
                                "untestable": "",
                            }
                        ],
                    }
                )
            )
            scenario = catalog.load("review-vacuous-tests")
            flags, fake_env = fake_setup(scenario, root, solution)
            run = TaskRun.start(
                project,
                "Review change.patch before merge",
                options=saved_run_flags(flags),
                start_options=("--workflow", "review"),
                env={**os.environ, **fake_env, "AUTOCODE_HOME": str(root / "registry")},
                cwd=root,
                timeout=180,
            )
            view = run.advance_until_input()
            self.assertTrue(view["done"], view)
            result = view["evidence"]["workflow_result"]
            self.assertEqual("approve", result["outcome"])
            report_path = run.workspace / "review/findings.json"
            raw = report_path.read_bytes()
            report = json.loads(raw)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), result["artifact_sha256"])
            self.assertEqual(("request_changes", "approve"), (report["submitted_verdict"], report["verdict"]))
            self.assertEqual({"blocking": 0, "advisory": 0, "pre_existing": 1, "inconclusive": 0}, report["counts"])
            finding = report["findings"][0]
            self.assertEqual("pre_existing", finding["disposition"]["kind"])
            for key in ("base_proof", "changed_proof", "inputs"):
                ref = finding["disposition"][key]
                self.assertEqual(ref["sha256"], hashlib.sha256(Path(ref["path"]).read_bytes()).hexdigest())
            self.assertEqual(original, (run.workspace / "calc.py").read_text())
            self.assertEqual(original, (project / "calc.py").read_text())
            canonical = run.evidence_report(require_current=False)
            self.assertEqual(result, canonical["document"]["workflow_result"])
            self.assertIn("pre-existing observations", canonical["markdown"])
