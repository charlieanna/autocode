"""A proof whose context changed while it ran is proved again, never handed to the Builder.

A live run on 2026-11-26 had AutoCode's own code updated in its checkout while the regression proof
ran. The proof was UNVERIFIED ("Execution context changed while proving the candidate:
runtime_sources"), which opened a blocking runner finding that only new source could close; the
Builder was sent to "correct the change or its tests" with nothing wrong in either, and the run paused
at the completion gate. Such a proof judged nothing (``proof_infrastructure.disturbed``): it runs once
more at once, again before review, and a later passing proof of the same source settles its finding.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_completion_result as completion
import autocode_git_delivery as delivery
import autocode_proof_findings as findings
import autocode_proof_infrastructure as infrastructure
import autocode_regression as regression
import autocode_source_scope as source_scope
import autocode_workflow as workflow

CHANGED = infrastructure.CONTEXT_CHANGED + ": runtime_sources"


def unverified(*reasons, failures=()):
    return {"verdict": "UNVERIFIED", "unverified": list(reasons), "failures": list(failures), "source_revision": "rev"}


class DisturbedTests(unittest.TestCase):
    def test_only_a_changed_context_counts(self):
        for reason in (CHANGED, infrastructure.FOLLOW_UP_CHANGED, infrastructure.CONTINUATION_CHANGED):
            self.assertTrue(infrastructure.disturbed(unverified(reason)), reason)
        self.assertFalse(infrastructure.disturbed(unverified("The base suite provided no passing-test evidence")))
        self.assertFalse(infrastructure.disturbed({"verdict": "FAIL", "unverified": [CHANGED]}))
        self.assertFalse(infrastructure.disturbed(None))


class ProveAgainTests(unittest.TestCase):
    def prove(self, *attempts):
        with (
            patch.object(source_scope, "snapshot", return_value={"revision": "rev"}),
            patch.object(regression, "_prove_once", side_effect=list(attempts)) as once,
        ):
            return regression.prove({}, "/work", "/run"), once.call_count

    def test_a_disturbed_proof_runs_once_more_in_the_context_that_holds_now(self):
        passed = {"verdict": "PASS"}
        self.assertEqual((passed, 2), self.prove(unverified(CHANGED), passed))

    def test_it_runs_again_only_once(self):
        again = unverified(CHANGED)
        self.assertEqual((again, 2), self.prove(unverified(CHANGED), again))

    def test_any_other_verdict_is_kept(self):
        for proof in ({"verdict": "PASS"}, {"verdict": "FAIL"}, unverified("No passing-test evidence")):
            with self.subTest(proof=proof):
                self.assertEqual((proof, 1), self.prove(proof))

    def test_review_proves_a_disturbed_saved_proof_again(self):
        state = {"regression_proof": {**unverified(CHANGED), "execution_context": {}}}
        with (
            patch.object(source_scope, "snapshot", return_value={"revision": "rev"}),
            patch.object(regression.recovery_review, "builder_timeout_inspection", return_value=False),
            patch.object(regression.role_policy, "proof_current", return_value=True),
            patch.object(regression.util, "digest", return_value=None),
            patch.object(regression, "cases", return_value=[]),
            patch.object(regression, "preserve_only", return_value=False),
        ):
            self.assertTrue(regression._review_needs_proof(state, "/work"))
            state["regression_proof"] = {**unverified("No passing-test evidence"), "execution_context": {}}
            self.assertFalse(regression._review_needs_proof(state, "/work"))


class SettleTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run = Path(tmp.name).resolve()
        folder = self.run / "regression" / "old"
        folder.mkdir(parents=True)
        self.path = folder / "verification.json"
        self.state = {
            "version": 3,
            "workspace": "/work",
            "run_dir": str(self.run),
            "status": "RUNNING",
            "next_stage": "review_completion",
            "current_task": {"id": "task", "milestone_id": "M1"},
            "stages": [
                {
                    "stage": "regression_proof",
                    "runner_owned": True,
                    "output": str(self.path),
                    "finished_at": "2026-01-01T00:00:00+00:00",
                }
            ],
            "regression_proof": {"verdict": "UNVERIFIED", "source_revision": "rev", "path": str(self.path)},
        }

    def review(self, receipt):
        self.path.write_text(json.dumps(receipt))
        row = findings.reconcile(self.state, "rev", required=True, proved=False, reason="Context changed")
        self.state["regression_proof"] = {
            "verdict": "PASS",
            "source_revision": "rev",
            "proved_at": "2026-01-01T00:01:00+00:00",
            "path": str(self.run / "regression" / "new" / "verification.json"),
        }
        with (
            patch.object(regression, "before_review"),
            patch.object(regression, "required", return_value=True),
            patch.object(regression, "complete", return_value=True),
            patch.object(source_scope, "snapshot", return_value={"revision": "rev"}),
            patch.object(delivery, "requirement", return_value=None),
            patch.object(workflow, "enabled", return_value=False),
        ):
            completion.before_review(self.state, "review_completion", "/work", self.run)
        return row

    def test_a_later_pass_of_the_same_source_settles_a_refusal_that_judged_nothing(self):
        row = self.review(unverified(CHANGED, infrastructure.FOLLOW_UP_CHANGED))
        self.assertEqual("resolved", row["status"])
        self.assertEqual(self.state["regression_proof"]["path"], row["resolved_in"])

    def test_a_refusal_that_also_judged_the_change_stays_open(self):
        for receipt in (
            unverified(CHANGED, failures=["Tests that pass on base fail on the candidate: t"]),
            unverified(CHANGED, "No passing-test evidence"),
            {**unverified(CHANGED), "source_revision": "other"},
            {"verdict": "FAIL", "failures": ["x"], "source_revision": "rev"},
        ):
            with self.subTest(receipt=receipt):
                self.setUp()
                self.assertEqual("open", self.review(receipt)["status"])


if __name__ == "__main__":
    unittest.main()
