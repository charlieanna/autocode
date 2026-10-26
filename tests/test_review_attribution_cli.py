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
from harness.processes import run_cli
from harness.project import materialize


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
                start_options=("--workflow", "review", "--spec-first"),
                env={**os.environ, **fake_env, "AUTOCODE_HOME": str(root / "registry")},
                cwd=root,
                timeout=180,
            )
            view = run.advance_until_input()
            self.assertTrue(view["done"], view)
            result = view["evidence"]["workflow_result"]
            self.assertEqual("approve", result["outcome"])
            self.assertEqual("current", result["freshness"])
            attempts = view["usage"]["accounting"]["attempts"]
            self.assertTrue(attempts)
            self.assertTrue(all(row.get("stage") == "review_change" for row in attempts), attempts)
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
            recorded = canonical["document"]["workflow_result"]
            self.assertEqual(result["source_revision"], recorded["source_revision"])
            self.assertEqual(recorded, {key: result[key] for key in recorded})
            self.assertIn("pre-existing observations", canonical["markdown"])
            # Inspection is read-only and does not silently extend an old review.
            self.assertEqual(result, run.status()["evidence"]["workflow_result"])
            code = run.workspace / "calc.py"
            code.write_text(original + "# changed after review\n")
            stale = run.status()["evidence"]["workflow_result"]
            self.assertEqual(("approve", "stale_or_unverified"), (stale["outcome"], stale["freshness"]))
            code.write_text(original)
            self.assertEqual("current", run.status()["evidence"]["workflow_result"]["freshness"])
            report_path.write_bytes(raw + b"\n")
            self.assertEqual("stale_or_unverified", run.status()["evidence"]["workflow_result"]["freshness"])
            report_path.unlink()
            self.assertEqual("stale_or_unverified", run.status()["evidence"]["workflow_result"]["freshness"])

    def test_new_feature_failure_on_both_sides_remains_a_blocker_on_changed_lines(self):
        with tempfile.TemporaryDirectory(prefix="review-new-feature-cli-") as tmp:
            root = Path(tmp).resolve()
            scenario = catalog.load("review-vacuous-tests")
            project = materialize(scenario.seed, root / "project")
            flags, fake_env = fake_setup(scenario, root, scenario.reference)
            run = TaskRun.start(
                project,
                scenario.brief,
                options=saved_run_flags(flags),
                start_options=("--workflow", "review"),
                env={**os.environ, **fake_env, "AUTOCODE_HOME": str(root / "registry")},
                cwd=root,
                timeout=180,
            )
            view = run.advance_until_input()
            self.assertTrue(view["done"], view)
            self.assertEqual("request_changes", view["evidence"]["workflow_result"]["outcome"])
            report = json.loads((run.workspace / "review/findings.json").read_text())
            finding = next(row for row in report["findings"] if row["id"] == "F1")
            self.assertTrue(finding["proven_by"])
            disposition = finding["disposition"]
            self.assertEqual("inconclusive", disposition["kind"])
            self.assertIn("changed source", disposition["reason"])
            base = json.loads(Path(disposition["base_proof"]["path"]).read_text())
            changed = json.loads(Path(disposition["changed_proof"]["path"]).read_text())
            for name in finding["proven_by"]:
                self.assertEqual(base["failure_details"][name], changed["failure_details"][name])
            self.assertEqual(
                (scenario.seed / "regclient/policies.py").read_bytes(),
                (run.workspace / "regclient/policies.py").read_bytes(),
            )

    def test_blocking_nit_control_is_rejected_by_the_unchanged_catalog_oracle(self):
        with tempfile.TemporaryDirectory(prefix="review-nit-control-") as tmp:
            root = Path(tmp).resolve()
            repo = Path(__file__).resolve().parents[1]
            completed = run_cli(
                [
                    sys.executable,
                    str(repo / "scenarios/run.py"),
                    "run",
                    "review-clean-pr",
                    "--fake",
                    "--fake-solution",
                    "broken/blocks-on-a-nit",
                    "--out",
                    str(root / "results"),
                    "--timeout-minutes",
                    "5",
                ],
                env={**os.environ, "AUTOCODE_HOME": str(root / "registry")},
                cwd=repo,
                timeout=360,
            )
            files = list((root / "results").glob("*/result.json"))
            self.assertEqual(1, len(files), completed.stdout + completed.stderr)
            result = json.loads(files[0].read_text())
            self.assertEqual(1, completed.returncode, result)
            self.assertEqual("FALSE_COMPLETE", result["verdict"], result)
            self.assertEqual("TASK_COMPLETE", result["runner_status"], result)
            checks = {row["name"]: row["ok"] for row in result["checks"]}
            self.assertFalse(checks["no_blocking_findings"], result)
            self.assertFalse(checks["verdict_approves"], result)
