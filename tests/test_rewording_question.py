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


def concern(cid="C2", text="AC2 claims the whole suite passes but names one test as proof.", *, blocking=True):
    return {
        "id": cid,
        "concern": text,
        "evidence_refs": ["tests/test_report.py"],
        "requested_change": "Keep a named test for what it proves and put the suite run in its own criterion.",
        "acceptance_test": "Every proof names what it proves.",
        "blocking": blocking,
    }


def reviewed_draft(*concerns):
    state = {"goal_contract": contract(approval_status="draft", approval_event=None)}
    state["planning"] = {"reports": {"astra_challenge": {"report": {"concerns": list(concerns)}}}}
    return state


class ReviewBackedChangeTests(unittest.TestCase):
    """A refused change the Plan Reviewer asked for, in a plan nobody approved, is the person's question (#1630)."""

    def downgrade(self, state):
        body = state["goal_contract"]["body"]
        body["acceptance_criteria"][0]["verification_method"] = "test: test_ac2_report_total"
        after = copy.deepcopy(body)
        after["acceptance_criteria"][0]["verification_method"] = "Run python3 -m unittest discover"
        return after

    def test_an_undeclared_proof_change_a_blocking_concern_asked_for_asks_the_person(self):
        state = reviewed_draft(concern())
        after = self.downgrade(state)
        with self.assertRaises(rewording.ProtectedRewording) as caught:
            revision_guard(state, after, [], "glm_revise")
        self.assertEqual(
            [
                {
                    "item": "AC2",
                    "approved": APPROVED,
                    "proposed": APPROVED,
                    "approved_proof": "test: test_ac2_report_total",
                    "proposed_proof": "Run python3 -m unittest discover",
                    "concern": "C2",
                }
            ],
            caught.exception.rewordings,
        )
        self.assertIn("as the Plan Reviewer asked", str(caught.exception))

    def test_a_declared_rewording_that_drops_a_quote_asks_when_a_concern_asked_for_it(self):
        state = reviewed_draft(concern(text="AC2 pins a space count the request never states."))
        body = state["goal_contract"]["body"]
        body["acceptance_criteria"][0]["sources"] = [{"source_id": "task:0", "quote": "one total line"}]
        after, changes = revised(body)
        after["acceptance_criteria"][0]["sources"] = []
        with self.assertRaises(rewording.ProtectedRewording) as caught:
            revision_guard(state, after, changes, "glm_revise")
        self.assertEqual(
            ("AC2", "C2", APPROVED, PROPOSED),
            tuple(caught.exception.rewordings[0][k] for k in ("item", "concern", "approved", "proposed")),
        )

    def test_without_a_blocking_concern_naming_the_criterion_it_stays_a_repair(self):
        for state in (
            reviewed_draft(),
            reviewed_draft(concern(blocking=False)),
            reviewed_draft(concern(text="AC12 and AC20 need examples.")),
            reviewed_draft(concern(text="The milestone graph has a cycle.")),
        ):
            with self.subTest(concerns=state["planning"]["reports"]["astra_challenge"]["report"]["concerns"]):
                after = self.downgrade(state)
                with self.assertRaises(ValueError) as caught:
                    revision_guard(state, after, [], "glm_revise")
                self.assertNotIsInstance(caught.exception, rewording.ProtectedRewording)
                self.assertIn("Keep a test:/guard: name", str(caught.exception))

    def test_the_question_names_the_concern_and_both_proofs_and_round_trips(self):
        state = reviewed_draft(concern())
        after = self.downgrade(state)
        with self.assertRaises(rewording.ProtectedRewording) as caught:
            revision_guard(state, after, [], "glm_revise")
        queued = {}

        def queue(target, scope, origin, **kwargs):
            queued.update(scope=scope, origin=origin, **kwargs)

        self.assertTrue(
            rewording.ask(state, {"stage": "glm_revise", "output": "/run/revise.json"}, caught.exception, queue)
        )
        text = queued["questions"][0]["question"]
        self.assertIn("asked by the Plan Reviewer's blocking concern C2", text)
        self.assertIn('AC2 approved proof: "test: test_ac2_report_total"', text)
        self.assertIn('AC2 proposed proof: "Run python3 -m unittest discover"', text)
        self.assertIn("traced to your request", text)
        self.assertEqual([rewording.ACCEPT, rewording.KEEP], queued["questions"][0]["options"])
        proposal = {"scope": "clarification", "questions": queued["questions"], "evidence": queued["evidence"]}
        self.assertTrue(rewording.accepts(state, proposal))

    def test_the_accepted_answer_lets_the_next_revision_make_the_change(self):
        state = reviewed_draft(concern())
        after = self.downgrade(state)
        with self.assertRaises(rewording.ProtectedRewording) as caught:
            revision_guard(state, after, [], "glm_revise")
        question = rewording.question(state, caught.exception.rewordings)
        state["answers"] = {question["id"]: {"kind": "answer", "actor": "user_cli", "text": rewording.ACCEPT}}
        changes = [
            {
                "item": "AC2",
                "change": "reworded",
                "basis": "user_answer",
                "answer_id": question["id"],
                "replacement": APPROVED,
            }
        ]
        self.assertEqual(changes, revision_guard(state, after, changes, "glm_revise"))


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

    def test_unchanged_criterion_with_new_sources_is_invalid_metadata_not_a_user_decision(self):
        for approved in (True, False):
            with self.subTest(approved=approved):
                current = contract(approval_status="approved" if approved else "draft")
                state = {"goal_contract": current}
                if not approved:
                    state["contract_history"] = [contract(revision=1, approval_status="approved")]
                after = copy.deepcopy(current["body"])
                row = after["acceptance_criteria"][0]
                row["sources"] = [{"source_id": "feedback:f1", "quote": "Keep the current output."}]
                changes = [
                    {
                        "item": "AC2",
                        "change": "reworded",
                        "basis": "agent_proposed",
                        "answer_id": "",
                        "replacement": json.dumps(row),
                    }
                ]
                with self.assertRaisesRegex(ValueError, "was not changed.*drop that row") as caught:
                    revision_guard(state, after, changes, "glm_revise")
                self.assertNotIsInstance(caught.exception, rewording.ProtectedRewording)
                self.assertEqual(APPROVED, after["acceptance_criteria"][0]["criterion"])

    def test_unchanged_plain_text_declaration_is_also_an_invalid_report(self):
        state = {"goal_contract": contract(approval_status="approved")}
        after = copy.deepcopy(state["goal_contract"]["body"])
        changes = [
            {"item": "AC2", "change": "reworded", "basis": "agent_proposed", "answer_id": "", "replacement": APPROVED}
        ]
        with self.assertRaisesRegex(ValueError, "was not changed.*drop that row") as caught:
            revision_guard(state, after, changes, "glm_revise")
        self.assertNotIsInstance(caught.exception, rewording.ProtectedRewording)

    def test_unchanged_protected_list_entry_does_not_ask_for_a_rewording(self):
        state = {"goal_contract": contract(approval_status="approved")}
        after = copy.deepcopy(state["goal_contract"]["body"])
        changes = [
            {
                "item": "Print one total line",
                "change": "reworded",
                "basis": "agent_proposed",
                "answer_id": "",
                "replacement": "Print one total line",
            }
        ]
        with self.assertRaisesRegex(ValueError, "was not changed.*drop that row") as caught:
            revision_guard(state, after, changes, "glm_revise")
        self.assertNotIsInstance(caught.exception, rewording.ProtectedRewording)

    def test_mixed_real_and_spurious_changes_repair_the_report_before_any_question(self):
        state = {"goal_contract": contract(approval_status="approved")}
        after, changes = revised(state["goal_contract"]["body"])
        changes.append(
            {
                "item": "Print one total line",
                "change": "reworded",
                "basis": "agent_proposed",
                "answer_id": "",
                "replacement": "Print one total line",
            }
        )
        with self.assertRaisesRegex(ValueError, "was not changed.*drop that row") as caught:
            revision_guard(state, after, changes, "glm_revise")
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
