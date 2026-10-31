"""Completion review retries failed proofs within their approved contract and case context."""

import unittest
from pathlib import Path
from unittest import mock

import autocode_regression as regression
import autocode_util as util


class ReviewRecheckTests(unittest.TestCase):
    def review(self, state):
        """Run before_review for the Completion Owner on source "rev"; return how many proofs it ran."""
        proved = []

        def prove(state, workspace, run_dir):
            proved.append(1)
            proof = {
                "source_revision": "rev",
                "verdict": state.pop("_next_verdict", "PASS"),
                "execution_context": {
                    "contract_identity": util.digest(state.get("goal_contract")),
                    "cases_identity": util.digest([]),
                    "preserve_only": regression.preserve_only(state),
                },
            }
            state["regression_proof"] = proof
            state.setdefault("regression_proofs", []).append(
                dict(proof, proof_context=util.digest(proof["execution_context"]))
            )

        with (
            mock.patch.object(regression.schedule, "guard"),
            mock.patch.object(regression.runner_check, "clear"),
            mock.patch.object(regression.package_writes, "check"),
            mock.patch.object(regression.acceptance, "is_blind", return_value=False),
            mock.patch.object(regression, "required", return_value=True),
            mock.patch.object(regression, "cases", return_value=[]),
            mock.patch.object(regression.source_scope, "snapshot", return_value={"revision": "rev"}),
            mock.patch.object(regression, "prove", side_effect=prove),
        ):
            regression.before_review(state, "astra_review", Path("/w"), Path("/r"))
        return len(proved)

    def failed_once(self):
        proof = {
            "source_revision": "rev",
            "verdict": "FAIL",
            "execution_context": {
                "contract_identity": util.digest(None), "cases_identity": util.digest([]), "preserve_only": False,
            },
        }
        return {
            "regression_proof": dict(proof),
            "regression_proofs": [dict(proof, proof_context=util.digest(proof["execution_context"]))],
        }

    def test_a_single_failure_is_replayed_before_review(self):
        # 2026-10-25: one timing flake failed the replay; the review demanded REWORK on unchanged source.
        state = self.failed_once()
        self.assertEqual(1, self.review(state))
        self.assertEqual("PASS", state["regression_proof"]["verdict"])

    def test_a_repeated_failure_stands(self):
        state = self.failed_once()
        state["_next_verdict"] = "FAIL"
        self.assertEqual(1, self.review(state))
        self.assertEqual("FAIL", state["regression_proof"]["verdict"])
        self.assertEqual(0, self.review(state))

    def test_a_passing_or_unverified_proof_of_this_source_is_reused(self):
        for verdict in ("PASS", "UNVERIFIED"):
            with self.subTest(verdict=verdict):
                proof = {
                    "source_revision": "rev",
                    "verdict": verdict,
                    "execution_context": {"contract_identity": util.digest(None), "cases_identity": util.digest([])},
                }
                self.assertEqual(0, self.review({"regression_proof": proof, "regression_proofs": [proof]}))

    def test_reapproval_replays_repeated_failure_without_source_change(self):
        state = self.failed_once()
        state["regression_proofs"] *= 2
        state["goal_contract"] = {"hash": "new-approved-test-binding"}
        self.assertEqual(1, self.review(state))
        self.assertEqual("PASS", state["regression_proof"]["verdict"])
        self.assertEqual(3, len(state["regression_proofs"]))

    def test_changed_preservation_mode_rechecks_repeated_failure_once(self):
        state = self.failed_once()
        state["regression_proofs"] *= 2
        with mock.patch.object(regression, "preserve_only", return_value=True):
            self.assertEqual(1, self.review(state))
            self.assertTrue(state["regression_proof"]["execution_context"]["preserve_only"])
            self.assertEqual(0, self.review(state))

    def test_lost_preservation_mode_cannot_reuse_a_passing_proof(self):
        state = self.failed_once()
        state["regression_proof"]["verdict"] = "PASS"
        state["regression_proof"]["execution_context"]["preserve_only"] = True
        self.assertEqual(1, self.review(state))

    def test_changed_cases_replace_even_a_passing_proof(self):
        state = self.failed_once()
        state["regression_proof"]["verdict"] = "PASS"
        state["regression_proof"]["execution_context"]["cases_identity"] = "old-case-binding"
        self.assertEqual(1, self.review(state))

    def test_earlier_contract_failures_do_not_exhaust_current_retry(self):
        state = self.failed_once()
        state["regression_proofs"] *= 2
        state["goal_contract"] = {"hash": "new-approved-test-binding"}
        state["_next_verdict"] = "FAIL"
        self.assertEqual(1, self.review(state))
        state["_next_verdict"] = "FAIL"
        self.assertEqual(1, self.review(state))
        self.assertEqual(0, self.review(state))

    def test_legacy_proof_without_context_is_reexecuted(self):
        proof = {"source_revision": "rev", "verdict": "FAIL"}
        self.assertEqual(1, self.review({"regression_proof": proof, "regression_proofs": [proof, proof]}))

    def test_a_proof_of_other_source_is_replaced(self):
        proof = {"source_revision": "old", "verdict": "PASS"}
        self.assertEqual(1, self.review({"regression_proof": proof, "regression_proofs": [proof]}))


if __name__ == "__main__":
    unittest.main()
