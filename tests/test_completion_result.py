"""Fresh authenticated preservation evidence permits bounded independent review."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_completion_result as completion
import autocode_git_delivery as delivery
import autocode_goals as goals
import autocode_regression as regression
import autocode_resolver_human as human
import autocode_source_scope as source_scope
import autocode_util as util


class PreservationReviewRefreshTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.run = Path(temp.name)
        self.prior = self.run / "regression" / "proof-01" / "verification.json"
        self.prior.parent.mkdir(parents=True)

    def state(self):
        contract = {"hash": "approved", "approval_status": "approved", "body": {}}
        prior = {
            "verdict": "FAIL",
            "failures": ["Only test files changed; a fix must change product code"],
            "source_revision": "old-source",
        }
        self.prior.write_text(json.dumps(prior))
        return {
            "status": "RUNNING",
            "next_stage": "review_completion",
            "settings": {"max_cost_usd": 23},
            "goal_contract": contract,
            "current_task": {"id": "task", "contract_hash": "approved"},
            "pending_questions": [],
            "regression_proofs": [{"verdict": "FAIL", "source_revision": "old-source", "path": str(self.prior)}],
            "regression_proof": {
                "verdict": "PASS",
                "source_revision": "rev",
                "source_files": [],
                "proved_at": "2026-01-01T00:00:02+00:00",
                "receipt_sha256": "receipt",
                "execution_context": {"contract_identity": util.digest(contract)},
            },
            "stages": [
                {
                    "stage": "review_completion",
                    "output": "/run/review.json",
                    "task_id": "task",
                    "contract_hash": "approved",
                    "source_revision": "rev",
                    "finished_at": "2026-01-01T00:00:01+00:00",
                },
                {
                    "stage": "regression_proof",
                    "runner_owned": True,
                    "output": str(self.prior),
                    "finished_at": "2026-01-01T00:00:00+00:00",
                },
            ],
            human.PRIVATE: {
                "scope": "blocker",
                "origin": {"stage": "review_completion", "output": "/run/review.json"},
                "request": {"kind": "blocker", "decision_needed": "Old proof is missing"},
            },
            "validation": {"source_revision": "rev", "result": "PASS"},
        }

    def review(self, state, *, proved=True, preservation=True):
        with (
            patch.object(regression, "before_review"),
            patch.object(regression, "complete", return_value=proved),
            patch.object(regression, "preserve_only", return_value=preservation),
            patch.object(source_scope, "snapshot", return_value={"revision": "rev"}),
            patch.object(goals, "approved", return_value=True),
            patch.object(delivery, "requirement", return_value=None),
        ):
            completion.before_review(state, "review_completion", "/work", self.run)

    def test_progressing_local_verification_defers_review_before_any_delivery_or_review_preparation(self):
        for stage in ("tester", "review_checkpoint", "review_completion"):
            with self.subTest(stage=stage):
                state = self.state()
                before = copy.deepcopy(state)
                with (
                    patch.object(regression, "before_review", return_value=True),
                    patch.object(
                        delivery,
                        "requirement",
                        side_effect=AssertionError("review prepared before local work finished"),
                    ),
                ):
                    self.assertIs(True, completion.before_review(state, stage, "/work", self.run))
                self.assertEqual(before, state)

    def test_new_passing_guard_proof_archives_old_private_review_blocker_without_granting_authority(self):
        state = self.state()
        before = copy.deepcopy(state)
        state["user_request"] = copy.deepcopy(state[human.PRIVATE]["request"])
        self.review(state)
        self.assertNotIn(human.PRIVATE, state)
        self.assertNotIn("user_request", state)
        for key in ("settings", "goal_contract", "current_task", "validation", "regression_proof", "stages"):
            self.assertEqual(before[key], state[key])
        self.assertEqual(before[human.PRIVATE], state["validation_archive"][0]["resolver_proposal"])
        self.review(state)
        self.assertEqual(1, len(state["validation_archive"]))

    def test_unproven_or_nonmember_evidence_keeps_the_blocker(self):
        for kwargs in ({"proved": False}, {"preservation": False}):
            with self.subTest(kwargs=kwargs):
                state = self.state()
                before = copy.deepcopy(state)
                self.review(state, **kwargs)
                self.assertEqual(before, state)

    def test_real_questions_other_tasks_newer_reviews_and_product_diffs_are_not_retired(self):
        edits = {
            "public": lambda s: s.update({human.PUBLIC: {"request_id": "human-question"}}),
            "questions": lambda s: s.update(pending_questions=[{"id": "business"}]),
            "permission": lambda s: s[human.PRIVATE].update(scope="permission"),
            "task": lambda s: s["stages"][0].update(task_id="other"),
            "task_contract": lambda s: s["current_task"].update(contract_hash="other"),
            "proof_contract": lambda s: s["regression_proof"]["execution_context"].update(contract_identity="other"),
            "source": lambda s: s["stages"][0].update(source_revision="other"),
            "newer": lambda s: s["stages"][0].update(finished_at="2026-01-01T00:00:03+00:00"),
            "product": lambda s: s["regression_proof"].update(source_files=["product.go"]),
            "other_failure": lambda s: self.prior.write_text(
                json.dumps({"verdict": "FAIL", "failures": ["A real regression still fails"]})
            ),
            "malformed_receipt": lambda s: self.prior.write_text("[]"),
            "outside": lambda s: s["regression_proofs"][0].update(path="/outside/verification.json"),
        }
        for label, edit in edits.items():
            with self.subTest(label=label):
                state = self.state()
                edit(state)
                before = copy.deepcopy(state)
                self.review(state)
                self.assertEqual(before, state)


class BuilderFirstTests(unittest.TestCase):
    """Before the Tester, which runs only when the proof of this source passes or its Builder round is spent."""

    def state(self, **settings):
        return {
            "version": 3,
            "iteration": 1,
            "status": "RUNNING",
            "next_stage": "tester",
            "settings": settings,
            "regression_proof": {
                "verdict": "FAIL",
                "failures": ["No regression test"],
                "source_revision": "r1",
                "path": "/run/verification.json",
            },
        }

    def open_finding(self, state):
        import autocode_proof_findings as proof_findings

        return proof_findings.reconcile(
            state, "r1", required=True, proved=False, reason="Completion rejected (No regression test)"
        )

    def test_a_failing_proof_returns_to_the_builder_and_no_tester_launches(self):
        state = self.state()
        self.open_finding(state)
        self.assertTrue(completion._builder_first(state, "r1"))
        self.assertEqual("builder", state["next_stage"])

    def test_tests_the_tester_wrote_or_a_disturbed_proof_keep_the_tester(self):
        state = self.state(spec_first=True)
        self.open_finding(state)
        self.assertFalse(completion._builder_first(state, "r1"))
        import autocode_proof_infrastructure as infrastructure

        state = self.state()
        state["regression_proof"].update(
            verdict="UNVERIFIED", failures=[], unverified=[infrastructure.DISTURBED[0] + " x"]
        )
        self.open_finding(state)
        self.assertFalse(completion._builder_first(state, "r1"))
        self.assertEqual("tester", state["next_stage"])

    def test_the_tester_hook_sends_the_run_to_the_builder_before_the_study(self):
        state = self.state()
        with (
            patch.object(completion, "_before_review", side_effect=lambda st, *_: self.open_finding(st) and None),
            patch.object(source_scope, "snapshot", return_value={"revision": "r1"}),
        ):
            self.assertTrue(completion.before_review(state, "tester", self.run_dir(), self.run_dir()))
        self.assertEqual("builder", state["next_stage"])

    def run_dir(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return Path(temp.name)
