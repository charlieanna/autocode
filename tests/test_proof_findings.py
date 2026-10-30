import unittest

import autocode_proof_findings as proof_findings

REASON = "Completion rejected: this change has no passing regression proof for the current source (No regression test)"


def failing(revision):
    return {"regression_proof": {"verdict": "FAIL", "source_revision": revision, "path": "/run/verification.json"}}


class ProofFindingTests(unittest.TestCase):
    def test_an_unproven_source_opens_one_blocking_runner_finding_the_builder_sees(self):
        state = failing("r1")
        row = proof_findings.reconcile(state, "r1", required=True, proved=False, reason=REASON)
        self.assertEqual(
            ("runner", "open", True, ["r1"]), (row["source"], row["status"], row["blocking"], row["failed_revisions"])
        )
        self.assertIn("No regression test", row["finding"])
        self.assertEqual([row["id"]], [item["id"] for item in state["unresolved_findings"]])
        again = proof_findings.reconcile(state, "r1", required=True, proved=False, reason=REASON)
        self.assertIs(row, again)
        self.assertEqual(1, len(state["findings_ledger"]))
        self.assertEqual(1, len(state["unresolved_findings"]))

    def test_a_new_failing_source_refreshes_the_same_finding(self):
        state = failing("r1")
        row = proof_findings.reconcile(state, "r1", required=True, proved=False, reason=REASON)
        state.update(failing("r2"))
        proof_findings.reconcile(state, "r2", required=True, proved=False, reason=REASON)
        self.assertEqual(["r1", "r2"], row["failed_revisions"])
        self.assertEqual(2, row["times_reported"])

    def test_a_proof_of_another_source_opens_nothing(self):
        state = failing("old")
        self.assertIsNone(proof_findings.reconcile(state, "r1", required=True, proved=False, reason=REASON))
        self.assertFalse(state.get("findings_ledger"))

    def test_a_passing_proof_closes_it_only_on_a_source_it_has_not_failed_on(self):
        state = failing("r1")
        row = proof_findings.reconcile(state, "r1", required=True, proved=False, reason=REASON)
        proof_findings.reconcile(state, "r1", required=True, proved=True)
        self.assertEqual("open", row["status"])
        proof_findings.reconcile(state, "r2", required=True, proved=True)
        self.assertEqual("resolved", row["status"])
        # Closed by the passing proof's receipt, as the other runner findings record where they closed.
        self.assertEqual("/run/verification.json", row["resolved_in"])
        self.assertFalse(proof_findings.open_rows(state))

    def test_a_contract_that_no_longer_owes_a_proof_closes_it(self):
        state = failing("r1")
        row = proof_findings.reconcile(state, "r1", required=True, proved=False, reason=REASON)
        proof_findings.reconcile(state, "r1", required=False, proved=False)
        self.assertEqual("resolved", row["status"])

    def test_a_completion_request_is_sent_back_once_then_goes_to_the_investigator(self):
        state = {}
        first = proof_findings.send_back(state, "r1")
        self.assertEqual("RUNNING", first["status"])
        self.assertEqual(proof_findings.SEND_BACK_NOTE, first["stop_reason"])
        second = proof_findings.send_back(state, "r1")
        self.assertEqual("PAUSED_COMPLETION_REVIEW", second["status"])
        self.assertEqual("RUNNING", proof_findings.send_back(state, "r2")["status"])


if __name__ == "__main__":
    unittest.main()
