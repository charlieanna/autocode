"""Pure public-view fixtures: projection never grants execution or product PASS."""

import copy
import unittest

import autocode_progressive_plan as rules
import autocode_run_view as run_view
import autocode_util as util


def proposed_state():
    check = {
        "id": "lesson-check",
        "relation": "contributes_to",
        "criterion_ids": ["C1"],
        "method": "python -m unittest tests.test_lesson",
    }
    first = {
        "id": "S1",
        "intended_result": "Persist one answer",
        "criterion_ids": ["C1"],
        "paths": ["src"],
        "depends_on": [],
        "tentative": False,
        "checks": [check],
    }
    future = {**copy.deepcopy(first), "id": "S2", "tentative": True, "depends_on": ["S1"]}
    proposal = {
        "version": 1,
        "needed_because": "Deliver the learning journey progressively",
        "slices": [first, future],
        "shared_decisions": [],
        "done_slices": [],
        "outstanding_criteria": ["C2"],
    }
    criteria = [{"id": "C1", "criterion": "Complete learning journey"}, {"id": "C2", "criterion": "Author lessons"}]
    body = {"acceptance_criteria": criteria, "open_blocking_questions": [], **rules.disclosure(proposal, ["C1", "C2"])}
    contract = {"task_id": "task", "revision": 1, "body": body, "approval_status": "draft", "origin": "user"}
    contract["hash"] = util.digest({key: contract[key] for key in ("task_id", "revision", "body")})
    return {
        "status": "AWAITING_GOAL_APPROVAL",
        "goal_contract": contract,
        "current_task": {"id": "task", "objective": "Build", "milestone_id": "M1"},
        "progressive": {"version": 1, "candidate": {"proposal": proposal, "plan_hash": rules.plan_identity(proposal)}},
    }


def approved_state():
    state = proposed_state()
    contract = state["goal_contract"]
    token = f"r1:{contract['hash']}"
    event = {"actor": "user_cli", "token": token}
    contract.update(approval_status="approved", approval_event=event)
    state["user_events"] = [event]
    record = state["progressive"]
    plan = record.pop("candidate")
    record.update(
        initial_plan=copy.deepcopy(plan), plan=plan, delegation=rules.seal_delegation(plan["proposal"], token)
    )
    state["status"] = "RUNNING"
    return state


class ProgressiveRunViewTests(unittest.TestCase):
    def test_ordinary_view_is_unchanged(self):
        state = {"status": "RUNNING", "phase": "build"}
        expected = {
            "runner_check": None,
            "dependency": None,
            "schema": 2,
            "status": "RUNNING",
            "done": False,
            "needs": {"kind": "continue"},
            "phase": "build",
            "next_stage": None,
            "iteration": None,
            "stop_reason": None,
            "current_task": None,
            "workflow": None,
            "workflow_source": None,
            "workflow_reason": None,
            "turn": 1,
            "evidence": {
                "created_at": None,
                "outcome": None,
                "workflow_result": None,
                "base_commit": None,
                "acceptance": [],
                "validator_source_revision": None,
                "findings": [],
                "regression_proof": None,
                "test_cases": [],
                "check_replay": None,
            },
        }
        result = run_view.view(state)
        self.assertEqual({key: result[key] for key in expected}, expected)
        self.assertNotIn("progressive", result)
        self.assertEqual(result.get("usage", {}).get("stages", 0), 0)
        state["progressive"] = {}
        self.assertEqual({key: run_view.view(state)[key] for key in expected}, expected)

    def test_proposal_and_disclosure_are_not_approval(self):
        state = proposed_state()
        result = run_view.view(state)["progressive"]
        self.assertEqual(result["status"], "proposed")
        self.assertFalse(result["delegation_approved"])
        self.assertEqual([row["id"] for row in result["tentative_next_work"]], ["S2"])
        state.pop("progressive")
        self.assertEqual(run_view.view(state)["progressive"]["status"], "unapproved")

    def test_approved_needs_activation_then_records_active_definition(self):
        state = approved_state()
        self.assertEqual(run_view.view(state)["progressive"]["status"], "needs_activation")
        record = state["progressive"]
        definition = record["plan"]["proposal"]["slices"][0]
        record["active"] = {
            "definition": definition,
            "plan_hash": record["plan"]["plan_hash"],
            "artifact": {"sha256": "artifact"},
            "review": {"sha256": "review"},
        }
        record["required_checks"] = definition["checks"]
        record["outstanding_criteria"] = ["C2"]
        record["budget"] = {
            "pools": {"P1": {"reviews_used": 2, "seconds_used": 300}},
            "run_seconds": 400,
            "run_limit": 43200,
        }
        result = run_view.view(state)["progressive"]
        self.assertEqual(result["status"], "active")
        self.assertEqual(result["active_slice"], definition)
        self.assertEqual(result["required_checks"], definition["checks"])
        self.assertEqual(result["allowance_usage"], record["budget"])
        self.assertEqual(result["outstanding_criteria"], ["C2"])
        valid_active = copy.deepcopy(record["active"])
        for field in ("artifact", "review"):
            with self.subTest(missing=field):
                record["active"] = copy.deepcopy(valid_active)
                record["active"].pop(field)
                self.assertEqual(run_view.view(state)["progressive"]["status"], "needs_activation")
        record["active"] = copy.deepcopy(valid_active)
        record["active"]["definition"]["intended_result"] = "unreviewed replacement"
        self.assertEqual(run_view.view(state)["progressive"]["status"], "needs_activation")
        record["active"] = valid_active
        record["active"]["plan_hash"] = "stale"
        self.assertEqual(run_view.view(state)["progressive"]["status"], "needs_activation")

    def test_suspended_and_stale_or_false_approval(self):
        state = approved_state()
        state["progressive"]["suspended"] = True
        self.assertEqual(run_view.view(state)["progressive"]["status"], "suspended")
        state["progressive"].pop("suspended")
        for change in ("token", "event", "body"):
            with self.subTest(change=change):
                stale = copy.deepcopy(state)
                if change == "token":
                    stale["progressive"]["delegation"]["contract_token"] = "old"
                elif change == "event":
                    stale["user_events"] = []
                else:
                    stale["goal_contract"]["body"]["constraints"] = []
                result = run_view.view(stale)["progressive"]
                self.assertEqual(result["status"], "suspended")
                self.assertFalse(result["delegation_approved"])

    def test_history_or_saved_pass_is_not_current_product_proof(self):
        state = approved_state()
        record = state["progressive"]
        record["history"] = [
            {"slice_id": "S1", "status": "PASS", "contract_token": "old", "source_revision": "old-source"}
        ]
        record["plan"]["proposal"]["slices"] = []
        state["status"] = "COMPLETE"
        for proof in (None, False, True, {"verified": True, "source_revision": "old-source"}):
            with self.subTest(proof=proof):
                record["current_whole_product_proof"] = proof
                result = run_view.view(state)["progressive"]
                self.assertEqual(result["demonstrated_slices"], record["history"])
                self.assertEqual(result["checkpoints"], record["history"])
                self.assertIs(result["current_whole_product_proof"]["verified"], False)
                self.assertEqual([row["id"] for row in result["outstanding_product_criteria"]], ["C1", "C2"])

    def test_no_mutation_and_existing_fields_unchanged(self):
        state = approved_state()
        before = copy.deepcopy(state)
        ordinary = copy.deepcopy(state)
        ordinary.pop("progressive")
        ordinary["goal_contract"]["body"]["constraints"] = []
        ordinary["goal_contract"]["body"]["technical_approach"] = []
        result = run_view.view(state)
        self.assertEqual(state, before)
        projection = result.pop("progressive")
        # Blanking the body above breaks the plan's seal, so only the progressive run still shows
        # the plan in force; that field is not part of the progressive projection.
        self.assertEqual(state["goal_contract"]["body"], result.pop("approved_contract")["body"])
        self.assertEqual(result, run_view.view(ordinary))
        projection["tentative_next_work"][0]["depends_on"].append("tamper")
        projection["outstanding_product_criteria"][0]["criterion"] = "tamper"
        self.assertEqual(state, before)


if __name__ == "__main__":
    unittest.main()
