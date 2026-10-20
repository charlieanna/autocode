"""A planner's rewording of approved wording becomes the person's question (#1691)."""

import copy
import json
import unittest

import autocode_goals as goals
import autocode_rewording_question as rewording
import autocode_util as util
from autocode_goals import revision_guard

from . import test_autocode as base
from . import test_report_repair

runner = base.runner

APPROVED = "stdout is exactly 'total             3.50h\\n'"
PROPOSED = "stdout is exactly 'total            3.50h\\n'"


def contract(**fields):
    body = {
        "acceptance_criteria": [
            {
                "id": "AC2",
                "criterion": APPROVED,
                "verification_method": "Run python3 -m timesheet report examples/march.csv",
                "human_review": False,
            }
        ],
        "required_behaviors": ["Print one total line"],
        "scope_exclusions": [],
        "constraints": [],
        "important_failure_cases": [],
        "permission_boundaries": ["No network"],
    }
    sealed = {"task_id": "t1", "revision": fields.pop("revision", 2), "body": body}
    return {**sealed, "hash": util.digest(sealed), "origin": "glm_revise", **fields}


def revised(body, answer_id=""):
    after = copy.deepcopy(body)
    after["acceptance_criteria"][0]["criterion"] = PROPOSED
    basis = "user_answer" if answer_id else "agent_proposed"
    return after, [
        {"item": "AC2", "change": "reworded", "basis": basis, "answer_id": answer_id, "replacement": PROPOSED}
    ]


class GuardTests(unittest.TestCase):
    def test_an_approved_criterion_reworded_without_an_answer_names_the_rewording(self):
        state = {"goal_contract": contract(approval_status="approved", approval_event={"kind": "goal_approval"})}
        after, changes = revised(state["goal_contract"]["body"])
        with self.assertRaises(rewording.ProtectedRewording) as caught:
            revision_guard(state, after, changes, "glm_revise")
        self.assertEqual([{"item": "AC2", "approved": APPROVED, "proposed": PROPOSED}], caught.exception.rewordings)
        self.assertIn("needs a saved user answer", str(caught.exception))

    def test_an_unapproved_draft_or_a_removal_stays_an_ordinary_refusal(self):
        draft = {"goal_contract": contract(approval_status="draft", approval_event=None)}
        after, changes = revised(draft["goal_contract"]["body"])
        with self.assertRaises(ValueError) as caught:
            revision_guard(draft, after, changes, "glm_revise")
        self.assertNotIsInstance(caught.exception, rewording.ProtectedRewording)

        approved = {"goal_contract": contract(approval_status="approved")}
        after = copy.deepcopy(approved["goal_contract"]["body"])
        after["required_behaviors"] = []
        removed = [{"item": "Print one total line", "change": "removed", "basis": "agent_proposed", "answer_id": ""}]
        with self.assertRaises(ValueError) as caught:
            revision_guard(approved, after, removed, "glm_revise")
        self.assertNotIsInstance(caught.exception, rewording.ProtectedRewording)


class AskTests(unittest.TestCase):
    setUp = base.RetrofitTest.setUp
    tearDown = base.RetrofitTest.tearDown
    stage_record = test_report_repair.RepairTests.stage_record

    def test_the_person_is_asked_and_their_answer_backs_the_next_revision(self):
        approved = contract(approval_status="approved", approval_event={"kind": "goal_approval"})
        self.state.update(
            task_id="t1",
            goal_contract=copy.deepcopy(approved),
            contract_history=[contract(revision=1, approval_status="approved")],
            next_stage="glm_revise",
        )
        self.state["settings"]["report_repair"] = {"max_attempts": 2}
        after, changes = revised(approved["body"])
        with self.assertRaises(rewording.ProtectedRewording) as refused:
            revision_guard(self.state, after, changes, "glm_revise")
        record = self.stage_record(stage="glm_revise", role="astra")

        with self.assertRaises(runner.ReportRepairQueued):
            runner.reject_completed_stage(self.state, self.run, record, refused.exception)

        saved = json.loads((self.run / "state.json").read_text())
        self.assertEqual("WAITING_FOR_USER", saved["status"])
        self.assertNotIn("pending_report_repair", saved, "no repair call is spent restoring the approved text")
        [question] = saved["pending_questions"]
        self.assertTrue(question["id"].startswith(rewording.PREFIX))
        self.assertIn(f"AC2 approved: {json.dumps(APPROVED)}", question["question"])
        self.assertIn(f"AC2 proposed: {json.dumps(PROPOSED)}", question["question"])
        self.assertEqual([rewording.ACCEPT, rewording.KEEP], question["options"])

        goals.answer(saved, question["id"], rewording.ACCEPT)
        self.assertEqual("RUNNING", saved["status"])
        after, changes = revised(approved["body"], answer_id=question["id"])
        self.assertEqual(changes, revision_guard(saved, after, changes, "glm_revise"))

    def test_a_question_about_wording_the_contract_no_longer_has_is_not_published(self):
        self.state["goal_contract"] = contract(approval_status="approved")
        rows = [{"item": "AC2", "approved": APPROVED, "proposed": PROPOSED}]
        proposal = {
            "scope": "clarification",
            "questions": [rewording.question(self.state, rows)],
            "evidence": {"rewording": {"contract_token": rewording.contract_token(self.state), "rewordings": rows}},
        }
        self.assertTrue(rewording.accepts(self.state, proposal))
        self.state["goal_contract"]["body"]["acceptance_criteria"][0]["criterion"] = "something else"
        self.assertFalse(rewording.accepts(self.state, proposal))
        self.state["goal_contract"] = contract(approval_status="approved", revision=3)
        self.assertFalse(rewording.accepts(self.state, proposal), "a newer contract")


if __name__ == "__main__":
    unittest.main()
