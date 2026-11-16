"""Retiring execution approval must retain the facts of a completed delivery."""

import copy
import unittest

import autocode_acceptance_provenance as provenance
import autocode_contract_identity as identity
import autocode_follow_up_proof as follow_proof

from . import test_follow_up_proof as fixtures


class CompletedTurnProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.project = fixtures.Project({"totals.py": "def sum_positive(v): return sum(x for x in v if x >= 0)\n"})
        self.addCleanup(self.project.close)
        self.state, self.run = fixtures.fixture(self.project)

    def test_completed_turn_uses_its_recorded_approval_after_execution_authority_is_retired(self):
        for status, keep_event in (("draft", False), ("draft", True), ("approved", False)):
            with self.subTest(status=status, keep_event=keep_event):
                state = copy.deepcopy(self.state)
                prior = state["contract_history"][0]
                prior["approval_status"] = status
                if not keep_event:
                    prior["approval_event"] = None
                before = copy.deepcopy(state)
                found = provenance.prior_contract(state)
                self.assertIsNotNone(found, "The completed delivery's ledger approval still authenticates its source")
                self.assertEqual(prior["hash"], found["hash"])
                baseline = follow_proof.context(state, self.project.root, self.run, [fixtures.CASE])
                self.assertEqual(self.state["code_checkpoints"][0]["commit"], baseline["commit"])
                self.assertNotIn("error", baseline)
                self.assertEqual(before, state, "Historical observation must not restore execution authority")

    def test_valid_copies_with_retired_metadata_supply_the_same_source_in_either_order(self):
        original = self.state["contract_history"][0]
        retired = {**copy.deepcopy(original), "approval_status": "draft", "approval_event": None}
        for rows in ([original, retired], [retired, original]):
            with self.subTest(retired_first=rows[0] is retired):
                state = {**copy.deepcopy(self.state), "contract_history": copy.deepcopy(rows)}
                found = provenance.prior_contract(state)
                self.assertIsNotNone(found)
                baseline = follow_proof.context(state, self.project.root, self.run, [fixtures.CASE])
                self.assertEqual(self.state["code_checkpoints"][0]["commit"], baseline["commit"])

    def test_each_copy_still_needs_the_exact_sealed_body_and_ledger_approval(self):
        for fault in ("body", "approval", "origin", "missing_ledger", "late_approval"):
            with self.subTest(fault=fault):
                state = copy.deepcopy(self.state)
                other = copy.deepcopy(state["contract_history"][0])
                if fault == "body":
                    other["body"]["acceptance_criteria"][0]["criterion"] = "unapproved behavior"
                elif fault == "approval":
                    other["approval_event"]["token"] = "forged"
                elif fault == "origin":
                    other["origin"] = "conflicting-origin"
                elif fault == "late_approval":
                    state["user_events"].append(state["user_events"].pop(0))
                else:
                    state["user_events"].remove(state["contract_history"][0]["approval_event"])
                state["contract_history"].append(other)
                self.assertIsNone(provenance.prior_contract(state))

    def test_past_approval_never_approves_the_new_turn(self):
        state = copy.deepcopy(self.state)
        prior = state["contract_history"][0]
        prior.update(approval_status="draft", approval_event=None)
        state["goal_contract"].update(approval_status="draft", approval_event=None)
        self.assertFalse(identity.approved(state))
        self.assertIsNone(follow_proof.context(state, self.project.root, self.run, [fixtures.CASE]))
