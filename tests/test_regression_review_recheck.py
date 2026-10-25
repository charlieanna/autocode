"""Before the Completion Owner reviews, a once-failed regression proof of this source is replayed once."""

import unittest
from pathlib import Path
from unittest import mock

import autocode_regression as regression


class ReviewRecheckTests(unittest.TestCase):
    def review(self, state):
        """Run before_review for the Completion Owner on source "rev"; return how many proofs it ran."""
        proved = []

        def prove(state, workspace, run_dir):
            proved.append(1)
            proof = {"source_revision": "rev", "verdict": state.pop("_next_verdict", "PASS")}
            state["regression_proof"] = proof
            state.setdefault("regression_proofs", []).append(dict(proof))

        with (
            mock.patch.object(regression.schedule, "guard"),
            mock.patch.object(regression.runner_check, "clear"),
            mock.patch.object(regression.package_writes, "check"),
            mock.patch.object(regression.acceptance, "is_blind", return_value=False),
            mock.patch.object(regression, "required", return_value=True),
            mock.patch.object(regression.source_scope, "snapshot", return_value={"revision": "rev"}),
            mock.patch.object(regression, "prove", side_effect=prove),
        ):
            regression.before_review(state, "astra_review", Path("/w"), Path("/r"))
        return len(proved)

    def failed_once(self):
        proof = {"source_revision": "rev", "verdict": "FAIL"}
        return {"regression_proof": dict(proof), "regression_proofs": [dict(proof)]}

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
                proof = {"source_revision": "rev", "verdict": verdict}
                self.assertEqual(0, self.review({"regression_proof": proof, "regression_proofs": [proof]}))

    def test_a_proof_of_other_source_is_replaced(self):
        proof = {"source_revision": "old", "verdict": "PASS"}
        self.assertEqual(1, self.review({"regression_proof": proof, "regression_proofs": [proof]}))


if __name__ == "__main__":
    unittest.main()
