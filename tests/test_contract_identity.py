"""Public contract predicates remain consistent after their cycle-free move."""

import copy
import unittest

import autocode_contract_identity as identity
import autocode_goals as goals
import autocode_util as util


class ContractIdentityTests(unittest.TestCase):
    def fixture(self):
        contract = {"task_id": "T1", "revision": 3, "body": {"open_blocking_questions": []}, "origin": "finalize_plan"}
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

    def test_equivalent_reviewed_body_ignores_only_reviewer_execution_file_receipts(self):
        base = {
            "open_blocking_questions": [],
            "acceptance_criteria": [{"id": "C1", "verification_method": "test: test_a.py::T.test_one"}],
            "source_trace": {
                "out_of_scope": [{"source_id": "task:0", "quote": "q", "reason": "r"}],
                "review": {
                    "stage": "finalize_plan",
                    "events": "/run/e1.jsonl",
                    "events_sha256": "111",
                    "output": "/run/o1.json",
                    "output_sha256": "222",
                    "trace_sha256": "abc",
                },
            },
            "brief_acceptance": {
                "proposals": [],
                "review": {
                    "stage": "finalize_plan",
                    "events": "/run/e1.jsonl",
                    "events_sha256": "111",
                    "output": "/run/o1.json",
                    "output_sha256": "222",
                    "proposals_sha256": "def",
                },
            },
        }
        re_reviewed = copy.deepcopy(base)
        re_reviewed["source_trace"]["review"].update(
            events="/run/e2.jsonl", events_sha256="333", output="/run/o2.json", output_sha256="444"
        )
        re_reviewed["brief_acceptance"]["review"].update(
            events="/run/e2.jsonl", events_sha256="333", output="/run/o2.json", output_sha256="444"
        )
        self.assertTrue(identity.equivalent_reviewed_body(base, re_reviewed))
        changed_trace = copy.deepcopy(re_reviewed)
        changed_trace["source_trace"]["review"]["trace_sha256"] = "changed"
        self.assertFalse(identity.equivalent_reviewed_body(base, changed_trace))
        changed_selector = copy.deepcopy(re_reviewed)
        changed_selector["acceptance_criteria"][0]["verification_method"] = "guard: test_a.py::T.test_one"
        self.assertFalse(identity.equivalent_reviewed_body(base, changed_selector))
        self.assertFalse(identity.equivalent_reviewed_body(base, {"source_trace": "malformed"}))
        self.assertFalse(identity.equivalent_reviewed_body(base, {"source_trace": {"review": "malformed"}}))

    def test_retained_approval_requires_authentic_earlier_person_approval_of_equivalent_body(self):
        prior_body = {
            "open_blocking_questions": [],
            "source_trace": {"review": {"events_sha256": "111", "trace_sha256": "same"}},
        }
        prior = {"task_id": "T1", "revision": 1, "body": prior_body, "origin": "finalize_plan"}
        prior["hash"] = util.digest({k: prior[k] for k in ("task_id", "revision", "body")})
        prior_event = {"kind": "goal_approval", "actor": "user_cli", "token": identity.token(prior)}
        prior.update(approval_status="approved", approval_event=prior_event)

        next_body = {
            "open_blocking_questions": [],
            "source_trace": {"review": {"events_sha256": "222", "trace_sha256": "same"}},
        }
        current = {"task_id": "T1", "revision": 2, "body": next_body, "origin": "finalize_plan"}
        current["hash"] = util.digest({k: current[k] for k in ("task_id", "revision", "body")})
        retained_event = {
            "kind": "goal_approval",
            "actor": "workflow_policy",
            "source": "retained_plan_approval",
            "retained_from_token": identity.token(prior),
            "token": identity.token(current),
        }
        current.update(approval_status="approved", approval_event=retained_event)
        state = {
            "contract_history": [prior],
            "goal_contract": current,
            "user_events": [prior_event, retained_event],
        }
        self.assertTrue(identity.approved(state))
        # View helpers passing only goal_contract + user_events still validate event ordering.
        self.assertTrue(identity.approved({"goal_contract": current, "user_events": [prior_event, retained_event]}))
        self.assertFalse(identity.approved({"goal_contract": current, "user_events": [retained_event, prior_event]}))
        # Missing history entry, mismatched task_id, or unapproved origin fails closed.
        no_history = {**state, "contract_history": []}
        self.assertFalse(identity.approved(no_history))
        wrong_origin = copy.deepcopy(state)
        wrong_origin["goal_contract"]["origin"] = "draft_plan"
        self.assertFalse(identity.approved(wrong_origin))
