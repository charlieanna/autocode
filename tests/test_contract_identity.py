"""Public contract predicates remain consistent after their cycle-free move."""

import copy
import unittest

import autocode_contract_identity as identity
import autocode_goals as goals
import autocode_util as util


class ContractIdentityTests(unittest.TestCase):
    def fixture(self):
        contract = {"task_id": "T1", "revision": 3, "body": {"open_blocking_questions": []}, "origin": "astra_finalize"}
        contract["hash"] = util.digest({key: contract[key] for key in ("task_id", "revision", "body")})
        event = {"kind": "goal_approval", "actor": "user_cli", "token": identity.token(contract)}
        contract.update(approval_status="approved", approval_event=event)
        return {"goal_contract": contract, "user_events": [event]}

    def test_existing_public_api_is_the_same_cycle_free_predicate(self):
        self.assertIs(goals.approved, identity.approved)
        self.assertIs(goals.sealed, identity.sealed)
        self.assertIs(goals.token, identity.token)
        state = self.fixture()
        before = copy.deepcopy(state)
        self.assertTrue(identity.approved(state))
        self.assertEqual(before, state)

    def test_stale_approval_or_changed_body_never_grants_authority(self):
        for change in ("event_token", "missing_event", "body", "status", "actor"):
            with self.subTest(change=change):
                state = self.fixture()
                contract = state["goal_contract"]
                if change == "event_token":
                    contract["approval_event"]["token"] = "r2:old"
                elif change == "missing_event":
                    state["user_events"] = []
                elif change == "body":
                    contract["body"]["open_blocking_questions"] = [{"id": "Q1"}]
                elif change == "status":
                    contract["approval_status"] = "proposed"
                else:
                    contract["approval_event"]["actor"] = "model"
                self.assertFalse(identity.approved(state))

    def test_shared_run_slug_preserves_name_and_length_rules(self):
        self.assertEqual("hello-world", util.slug("Hello, world!"))
        self.assertEqual("task", util.slug("!!!"))
        self.assertEqual("x" * 48, util.slug("x" * 80))
