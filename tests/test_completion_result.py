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
            "next_stage": "astra_review",
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
                    "stage": "astra_review",
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
                "origin": {"stage": "astra_review", "output": "/run/review.json"},
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
            completion.before_review(state, "astra_review", "/work", self.run)

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
