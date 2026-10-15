"""Review mutation diagnostics preserve finding authority and authenticated public evidence."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import autocode_command_receipt as command_receipt
import autocode_evidence_export as evidence_export
import autocode_review_job as review_job
import autocode_run_view as run_view

FINDING = "review.tests.test_lookup.FindingTests.test_f1_public_lookup"
PR_TEST = "tests.test_added.AddedTests.test_retry_count"
PATCH = """diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
 def double(n):
-    return n * 2
+    return n + 2
"""
PROVENANCE = {"kind": "fake", "basis": "Injected deterministic runner results", "declaration": "unit test"}


def mutation_record():
    survivor = {
        "test": PR_TEST,
        "path": "calc.py",
        "line": 2,
        "operator": "constant_return",
        "before": "n + 2",
        "after": "None",
        "text": f"{PR_TEST} passes with calc.py:2 changed (`n + 2` -> `None`)",
    }
    return {
        "subject": "reviewed_patch_tests",
        "patch_path": "pr-197.patch",
        "patch_sha256": hashlib.sha256(PATCH.encode()).hexdigest(),
        "selected_files": ["tests/test_added.py"],
        "selected_tests": [PR_TEST],
        "collected": True,
        "note": None,
        "limit_per_file": 6,
        "budget_seconds": 900,
        "files": [
            {
                "path": "calc.py",
                "notes": [],
                "mutants": [
                    {
                        key: value
                        for key, value in {
                            **survivor,
                            "outcome": "survived",
                            "survived_tests": [PR_TEST],
                        }.items()
                        if key not in ("test", "text")
                    }
                ],
            }
        ],
        "survivors": {PR_TEST: [survivor]},
    }


class ReviewMutationEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="review-mutation-evidence-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        self.run = self.root / "run"
        self.run.mkdir()
        self.product = self.workspace / "calc.py"
        self.product.write_text("def double(n):\n    return n * 2\n")
        self.original_product = self.product.read_bytes()
        (self.workspace / "pr-197.patch").write_text(PATCH)
        test = self.workspace / "review/tests/test_lookup.py"
        test.parent.mkdir(parents=True)
        test.write_text(
            "import unittest\nfrom calc import double\n\nclass FindingTests(unittest.TestCase):\n"
            "    def test_f1_public_lookup(self):\n        self.assertEqual(6, double(3))\n"
        )
        self.state = {
            "version": 3,
            "task": "Review pr-197.patch",
            "workspace": str(self.workspace),
            "run_dir": str(self.run),
            "status": "RUNNING",
            "phase": "REVIEWING",
            "workflow": {"kind": "review"},
            "created_at": "2026-10-15T00:00:00+00:00",
            "stages": [],
            "settings": {"joint_planning": True, "roles": {}},
        }
        self.value = {
            "verdict": "request_changes",
            "summary": "The reviewed change adds rather than doubles.",
            "change_under_review": "pr-197.patch",
            "change_patch": "pr-197.patch",
            "findings": [
                {
                    "id": "F1",
                    "severity": "blocking",
                    "file": "calc.py",
                    "lines": [2, 2],
                    "summary": "The public calculation adds instead of doubling.",
                    "evidence": "double(3) is 5 instead of 6",
                    "example": "Given n=3, double(n) returns 5; expected 6.",
                    "untestable": "",
                }
            ],
            # Free-form model text has no authority over the injected runner's
            # patch selection or collected test identities.
            "tests_run": ["model-reported-command must not select mutation tests"],
            "delivered_tests": ["review/tests/test_lookup.py"],
        }
        self.record = {"changed_files": ["review/tests/test_lookup.py"], "output": str(self.run / "review.json")}

    def finding_result(self, *, failing=True):
        return {
            "command": "python -m unittest review.tests.test_lookup",
            "exit_code": 1 if failing else 0,
            "timed_out": False,
            "tail": "one named finding test",
            "results": {
                "complete": True,
                "passed": [] if failing else [FINDING],
                "failed": [FINDING] if failing else [],
                "collection_errors": [],
            },
        }

    def apply(self, *, run_mutation_check=None, run_tests=None, value=None, record=None):
        review_job.apply(
            self.state,
            value or self.value,
            record or self.record,
            self.workspace,
            run_tests=run_tests or (lambda tests, patch: self.finding_result()),
            run_mutation_check=run_mutation_check,
        )
        return run_view.view(self.state)

    def written(self):
        return json.loads((self.workspace / "review/findings.json").read_bytes())

    def publish(self):
        view = run_view.view(self.state)
        self.assertTrue(evidence_export.publish(self.run, self.state, view, PROVENANCE))
        accounting = view["usage"]["accounting"]
        binding = evidence_export.binding(self.state, accounting)
        anchor = evidence_export.metadata(self.state)
        report = evidence_export.read(self.run, anchor, expected_binding=binding, include=True)
        return view, anchor, binding, report

    def test_collection_runs_after_finding_proof_and_keeps_request_changes(self):
        events = []

        def finding(tests, patch):
            events.append(("finding", tests, patch))
            return self.finding_result()

        collected = mutation_record()

        def collect(patch):
            events.append(("mutation", patch))
            return collected

        view = self.apply(run_tests=finding, run_mutation_check=collect)
        self.assertEqual(
            [("finding", ["review/tests/test_lookup.py"], "pr-197.patch"), ("mutation", "pr-197.patch")], events
        )
        self.assertEqual("TASK_COMPLETE", view["status"], view)
        self.assertTrue(view["done"], view)
        self.assertEqual("request_changes", view["evidence"]["workflow_result"]["outcome"])
        self.assertEqual(collected, view["evidence"]["mutation_check"])
        self.assertEqual(collected, self.written()["mutation_check"])
        self.assertEqual([FINDING], self.written()["findings"][0]["proven_by"])
        self.assertEqual(self.original_product, self.product.read_bytes())
        self.assertIsNone(view["evidence"]["regression_proof"])

    def test_a_survivor_does_not_turn_an_approval_into_a_blocking_finding(self):
        value = {**self.value, "verdict": "approve", "findings": [], "delivered_tests": []}
        finding = Mock(side_effect=AssertionError("an approval has no finding test to prove"))
        view = self.apply(value=value, run_tests=finding, run_mutation_check=lambda patch: mutation_record())
        finding.assert_not_called()
        self.assertEqual("TASK_COMPLETE", view["status"], view)
        self.assertEqual("approve", view["evidence"]["workflow_result"]["outcome"])
        self.assertEqual([], self.written()["findings"])
        self.assertTrue(view["evidence"]["mutation_check"]["survivors"])

    def test_an_unproven_finding_never_spends_on_mutations_or_publishes_completion(self):
        collect = Mock(return_value=mutation_record())
        with self.assertRaisesRegex(ValueError, "no delivered test.*fails"):
            self.apply(run_tests=lambda tests, patch: self.finding_result(failing=False), run_mutation_check=collect)
        collect.assert_not_called()
        self.assertEqual("RUNNING", run_view.view(self.state)["status"])
        self.assertFalse((self.workspace / "review/findings.json").exists())

    def test_a_product_write_is_rejected_before_finding_or_mutation_collection(self):
        finding = Mock(return_value=self.finding_result())
        collect = Mock(return_value=mutation_record())
        with self.assertRaisesRegex(ValueError, "must not change the repository"):
            self.apply(
                record={**self.record, "changed_files": ["calc.py"]}, run_tests=finding, run_mutation_check=collect
            )
        finding.assert_not_called()
        collect.assert_not_called()
        self.assertEqual("RUNNING", run_view.view(self.state)["status"])
        self.assertFalse((self.workspace / "review/findings.json").exists())

    def test_an_ordinary_diagnostic_error_is_not_a_mutation_pass_or_a_review_blocker(self):
        def unavailable(patch):
            raise ValueError("diagnostic tool unavailable")

        view = self.apply(run_mutation_check=unavailable)
        check = view["evidence"]["mutation_check"]
        self.assertFalse(check["collected"], check)
        self.assertEqual([], check["files"], check)
        self.assertNotIn("survivors", check)
        self.assertIn("ValueError: diagnostic tool unavailable", check["note"])
        self.assertEqual("TASK_COMPLETE", view["status"], view)
        self.assertEqual("request_changes", self.written()["verdict"])
        self.assertEqual([FINDING], self.written()["findings"][0]["proven_by"])
        _, _, _, report = self.publish()
        self.assertIn("No mutation result was collected.", report["markdown"])
        self.assertEqual(check, report["document"]["workflow_result"]["mutation_check"])

    def test_unknown_command_custody_preserves_the_prior_artifact_and_cannot_complete(self):
        target = self.workspace / "review/findings.json"
        previous = b"previous artifact must survive uncertain command cleanup\n"
        target.write_bytes(previous)
        uncertain = command_receipt.OwnershipUncertain("mutation worker cleanup is unverified")
        collect = Mock(side_effect=uncertain)
        with self.assertRaises(command_receipt.OwnershipUncertain) as caught:
            self.apply(run_mutation_check=collect)
        self.assertIs(uncertain, caught.exception)
        collect.assert_called_once_with("pr-197.patch")
        view = run_view.view(self.state)
        self.assertEqual("RUNNING", view["status"], view)
        self.assertFalse(view["done"], view)
        self.assertEqual(previous, target.read_bytes())
        self.assertIsNone(view["evidence"]["workflow_result"])

    def test_legacy_callers_without_collection_keep_the_old_report_shape(self):
        view = self.apply()
        self.assertNotIn("mutation_check", self.written())
        self.assertNotIn("mutation_check", view["evidence"]["workflow_result"])
        self.assertIsNone(view["evidence"]["mutation_check"])
        _, _, _, report = self.publish()
        self.assertNotIn("mutation_check", report["document"]["workflow_result"])
        self.assertNotIn("## Review test mutation check", report["markdown"])

    def test_the_public_view_and_canonical_pair_share_report_only_review_evidence(self):
        collected = mutation_record()
        self.apply(run_mutation_check=lambda patch: copy.deepcopy(collected))
        view, _, _, report = self.publish()
        self.assertEqual("recorded", report["availability"], report)
        self.assertEqual(collected, view["evidence"]["mutation_check"])
        self.assertEqual(collected, view["evidence"]["workflow_result"]["mutation_check"])
        document = report["document"]
        self.assertEqual(collected, document["workflow_result"]["mutation_check"])
        self.assertIsNone(document["regression_proof"])
        self.assertEqual([], document["criteria"])
        self.assertIn("## Review test mutation check", report["markdown"])
        self.assertIn("Report only; this does not change the review verdict.", report["markdown"])
        self.assertIn(PR_TEST, report["markdown"])
        for kind in ("json", "markdown"):
            raw = Path(report[kind + "_path"]).read_bytes()
            self.assertEqual(report[kind + "_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(document, json.loads(Path(report["json_path"]).read_bytes()))
        self.assertEqual(report["markdown"], Path(report["markdown_path"]).read_text())
        view["evidence"]["mutation_check"]["selected_tests"].clear()
        view["evidence"]["workflow_result"]["mutation_check"]["survivors"].clear()
        fresh = run_view.view(self.state)
        self.assertEqual(collected, fresh["evidence"]["mutation_check"])
        self.assertEqual(collected, fresh["evidence"]["workflow_result"]["mutation_check"])

    def test_changed_review_metadata_or_canonical_bytes_cannot_reuse_the_binding(self):
        self.apply(run_mutation_check=lambda patch: mutation_record())
        view, anchor, binding, report = self.publish()
        changed = copy.deepcopy(self.state)
        changed["review"]["mutation_check"]["selected_tests"].append("tests.Unselected.test_unobserved")
        different = evidence_export.binding(changed, view["usage"]["accounting"])
        self.assertNotEqual(binding, different)
        stale = evidence_export.read(self.run, anchor, expected_binding=different, include=True)
        self.assertEqual("stale", stale["availability"], stale)
        path = Path(report["json_path"])
        value = json.loads(path.read_bytes())
        value["workflow_result"]["mutation_check"]["selected_files"].append("tests/unselected.py")
        path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
        invalid = evidence_export.read(self.run, anchor, expected_binding=binding, include=True)
        self.assertEqual("invalid", invalid["availability"], invalid)
        self.assertNotIn("document", invalid)


if __name__ == "__main__":
    unittest.main()
