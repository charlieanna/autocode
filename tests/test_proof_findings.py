import unittest
from unittest.mock import patch

import autocode_completion_result as completion_result
import autocode_proof_findings as proof_findings
import autocode_regression as regression
import autocode_source_scope as source_scope
import autocode_workflow as workflow

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

    def test_milestone_reconciliation_does_not_open_a_finding_for_future_cases(self):
        state = {
            "version": 3,
            "workspace": "/work",
            "settings": {},
            "goal_contract": {"hash": "contract", "body": {"milestones": [{"id": "M1"}, {"id": "M2"}]}},
            "current_task": {"milestone_id": "M1"},
            "regression_proof": {"source_revision": "r1", "path": "/run/proof.json"},
        }

        def proof_passes_current_scope(_state, _revision, **options):
            return options.get("all_due") is False

        with (
            patch.object(workflow, "enabled", return_value=False),
            patch.object(source_scope, "snapshot", return_value={"revision": "r1"}),
            patch.object(regression, "required", return_value=True) as required,
            patch.object(regression, "complete", side_effect=proof_passes_current_scope) as complete,
            patch.object(regression, "rejection", return_value="future cases are not due") as rejection,
        ):
            completion_result._reconcile_proof_finding(state, "/work")

        required.assert_called_once_with(state, all_due=False)
        complete.assert_called_once()
        self.assertIs(complete.call_args.kwargs["all_due"], False)
        rejection.assert_not_called()
        self.assertFalse(proof_findings.open_rows(state))

    def test_scoped_proof_retires_legacy_future_case_finding_on_the_same_source(self):
        state = {
            "version": 3,
            "workspace": "/work",
            "settings": {},
            "goal_contract": {"hash": "contract", "body": {"milestones": [{"id": "M1"}, {"id": "M2"}]}},
            "current_task": {"milestone_id": "M1"},
            "regression_proof": {"source_revision": "r1", "path": "/run/proof.json"},
        }
        row = proof_findings.reconcile(
            state,
            "r1",
            required=True,
            proved=False,
            reason="Completion rejected: the milestone proof still needs all required cases: AC5, AC6",
        )
        row["scope"] = {"milestone_id": "M1", "criteria": ["AC1", "AC2"]}

        with (
            patch.object(workflow, "enabled", return_value=False),
            patch.object(source_scope, "snapshot", return_value={"revision": "r1"}),
            patch.object(regression, "required", return_value=True),
            patch.object(regression, "complete", return_value=True),
            patch.object(regression, "rejection") as rejection,
        ):
            completion_result._reconcile_proof_finding(state, "/work")

        rejection.assert_not_called()
        self.assertEqual("resolved", row["status"])
        self.assertFalse(proof_findings.open_rows(state))

    def test_milestone_completion_request_is_not_sent_back_for_future_cases(self):
        state = {
            "version": 3,
            "settings": {},
            "goal_contract": {"hash": "contract", "body": {"milestones": [{"id": "M1"}, {"id": "M2"}]}},
            "current_task": {"milestone_id": "M1"},
            "regression_proof": {"source_revision": "r1", "path": "/run/proof.json"},
        }
        proof_findings.reconcile(state, "r1", required=True, proved=False, reason=REASON)

        def proof_passes_current_scope(_state, _revision, **options):
            return options.get("all_due") is False

        with (
            patch.object(workflow, "enabled", return_value=False),
            patch.object(regression, "complete", side_effect=proof_passes_current_scope) as complete,
        ):
            sent_back = completion_result._send_back_unproven(
                state,
                {"revision": "r1"},
                {"stage": "astra_review"},
                regression.complete,
            )

        complete.assert_called_once_with(state, "r1", all_due=False)
        self.assertIsNone(sent_back)


if __name__ == "__main__":
    unittest.main()
