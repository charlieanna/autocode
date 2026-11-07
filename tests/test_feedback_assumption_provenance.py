"""A real feedback ID does not make a new model interpretation the person's words."""

import copy
import json
import unittest
from pathlib import Path

import autocode_contract_revision as revision
import autocode_goals as goals
import autocode_report_repair_context as repair_context
import autocode_rewording_question as rewording
from units import autoplanner

FEEDBACK = (
    "AC6 and AC8 were added in the current unapproved draft after revision 6. "
    "The saved approval covers AC1 through AC5. "
    "The installed core now distinguishes those identities under the corrected revision guard. "
    "Re-evaluate the pending corrections with the requested feature and test scope preserved. "
    "This is corrective information and grants no plan approval."
)
WRONG = (
    "The completed r6 fail-first proofs are carried forward unchanged; only the new start behavior needs fresh proof."
)
RIGHT = "The feedback corrects criterion identities and grants no approval or exemption from fresh runner proof."


def inputs():
    event = {"id": "feedback-1", "kind": "brief_feedback", "actor": "user_cli", "text": FEEDBACK}
    body = {
        "acceptance_criteria": [
            {
                "id": "AC1",
                "criterion": "Preserve the output",
                "verification_method": "test: test_old",
                "human_review": False,
            }
        ],
        "required_behaviors": ["Preserve the output"],
        "scope_exclusions": [],
        "constraints": [],
        "important_failure_cases": [],
        "permission_boundaries": ["No network"],
        "accepted_assumptions": [{"text": WRONG, "basis": "user_feedback", "answer_id": "feedback-1"}],
        "delegated_decisions": [],
    }
    approved = copy.deepcopy(body)
    approved["accepted_assumptions"] = []
    state = {
        "goal_contract": {
            "body": body,
            "origin": "glm_draft",
            "approval_status": "draft",
            "approval_event": None,
            "revision": 8,
            "hash": "draft-hash",
        },
        "contract_history": [{"body": approved, "origin": "astra_finalize", "approval_status": "approved"}],
        "brief_feedback": [event],
        "user_events": [event],
        "answers": {},
    }
    after = copy.deepcopy(body)
    after["accepted_assumptions"][0]["text"] = RIGHT
    return state, after


class FeedbackAssumptionRevisionTests(unittest.TestCase):
    def test_correcting_new_model_interpretation_does_not_request_user_authority(self):
        state, after = inputs()
        before = copy.deepcopy(state)
        self.assertEqual([], revision.revision_guard(state, after, [], "glm_revise"))
        self.assertEqual(before, state)

    def test_overdeclared_interpretation_correction_is_an_engineering_change(self):
        state, after = inputs()
        changes = [
            {"item": WRONG, "change": "reworded", "replacement": RIGHT, "basis": "agent_proposed", "answer_id": ""}
        ]
        self.assertEqual(changes, revision.revision_guard(state, after, changes, "glm_revise"))

    def test_person_owned_or_uncertain_rows_stay_protected(self):
        for case in (
            "approved_now",
            "human_edit_now",
            "unknown_origin",
            "approved_history",
            "human_edit_history",
            "receipt",
            "approval_receipt_current",
            "approval_receipt_history",
            "unresolved_approval_receipt",
            "missing_approval_token",
            "approval_without_contract_identity",
            "quote",
            "answer",
            "missing_source",
            "unauthenticated_source",
            "incomplete_history",
            "malformed_history",
            "lost_source",
        ):
            with self.subTest(case=case):
                state, after = inputs()
                contract = state["goal_contract"]
                if case == "approved_now":
                    contract["approval_status"] = "approved"
                elif case == "human_edit_now":
                    contract["origin"] = "user_cli_edit"
                elif case == "unknown_origin":
                    contract.pop("origin")
                elif case in ("approved_history", "human_edit_history"):
                    protected = copy.deepcopy(contract)
                    protected.update(
                        approval_status="approved" if case == "approved_history" else "draft",
                        origin="astra_finalize" if case == "approved_history" else "user_cli_edit",
                    )
                    state["contract_history"].append(protected)
                elif case == "receipt":
                    contract["declared_changes"] = [
                        {
                            "item": "old wording",
                            "replacement": WRONG,
                            "change": "reworded",
                            "basis": "user_feedback",
                            "answer_id": "feedback-1",
                        }
                    ]
                elif case == "approval_receipt_current":
                    state["user_events"].append(
                        {"kind": "goal_approval", "actor": "user_cli", "token": "r8:draft-hash"}
                    )
                elif case == "approval_receipt_history":
                    prior = copy.deepcopy(contract)
                    prior.update(revision=7, hash="old-hash")
                    state["contract_history"].append(prior)
                    state["user_events"].append({"kind": "goal_approval", "actor": "user_cli", "token": "r7:old-hash"})
                elif case == "unresolved_approval_receipt":
                    state["user_events"].append({"kind": "goal_approval", "actor": "user_cli", "token": "r7:missing"})
                elif case == "missing_approval_token":
                    state["user_events"].append({"kind": "goal_approval", "actor": "user_cli"})
                elif case == "approval_without_contract_identity":
                    state["goal_contract"].pop("revision")
                    state["goal_contract"].pop("hash")
                    state["user_events"].append({"kind": "goal_approval", "actor": "user_cli", "token": "rNone:None"})
                elif case == "quote":
                    contract["body"]["accepted_assumptions"][0]["text"] = (
                        "This is corrective information and grants no plan approval."
                    )
                elif case == "answer":
                    contract["body"]["accepted_assumptions"][0].update(basis="user_answer", answer_id="A1")
                    state["answers"]["A1"] = {"kind": "answer", "text": "Carry the proofs forward"}
                elif case == "missing_source":
                    state["brief_feedback"] = []
                elif case == "unauthenticated_source":
                    state["user_events"] = []
                elif case == "incomplete_history":
                    state["contract_history"].append({"approval_status": "approved"})
                elif case == "malformed_history":
                    state["contract_history"].append({"approval_status": "approved", "body": []})
                elif case == "lost_source":
                    after["accepted_assumptions"][0].update(basis="agent_proposed", answer_id="")
                with self.assertRaises((ValueError, rewording.ProtectedRewording)):
                    revision.revision_guard(state, after, [], "glm_revise")

    def test_interpretation_correction_does_not_authorize_other_protected_changes(self):
        for case in ("behavior", "permission", "criterion", "proof"):
            with self.subTest(case=case):
                state, after = inputs()
                if case == "behavior":
                    after["required_behaviors"] = []
                elif case == "permission":
                    after["permission_boundaries"] = ["Allow network"]
                elif case == "criterion":
                    after["acceptance_criteria"][0]["criterion"] = "Different behavior"
                elif case == "proof":
                    after["acceptance_criteria"][0]["verification_method"] = "Run anything"
                with self.assertRaises(ValueError):
                    revision.revision_guard(state, after, [], "glm_revise")

    def test_interpretation_correction_keeps_a_new_criterion_source_quote_protected(self):
        state, after = inputs()
        criterion = {
            "id": "AC6",
            "criterion": "Given a new input, the unchanged requested output is returned",
            "verification_method": "test: test_new",
            "human_review": False,
            "sources": [{"source_id": "feedback:feedback-1", "quote": FEEDBACK}],
        }
        state["goal_contract"]["body"]["acceptance_criteria"].append(copy.deepcopy(criterion))
        after["acceptance_criteria"].append(copy.deepcopy(criterion))
        after["acceptance_criteria"][-1]["criterion"] = "Given that new input, the requested output is returned"
        revision.revision_guard(state, after, [], "glm_revise")
        after["acceptance_criteria"][-1]["sources"] = []
        with self.assertRaises(ValueError):
            revision.revision_guard(state, after, [], "glm_revise")


class FeedbackAssumptionContextTests(unittest.TestCase):
    def state(self):
        state, _ = inputs()
        state.update(
            version=3,
            task_id="task-1",
            task="Preserve the output",
            workspace="/absent-workspace",
            status="RUNNING",
            acceptance_criteria=[],
            planning={"reports": {}, "astra_calls": 1, "final_token": None},
            settings={
                "joint_planning": True,
                "context_soft_tokens": 10000,
                "roles": {
                    role: {"model": "fixed-model", "engine": "codex"} for role in ("astra", "glm", "plan_reviewer")
                },
            },
        )
        state["goal_contract"].update(revision=8, hash="draft-hash", task_id="task-1")
        return state

    def test_revision_prompt_distinguishes_interpretation_from_exact_saved_feedback(self):
        state = self.state()
        before = copy.deepcopy(state)
        prompt, _ = autoplanner.context(state, "glm_revise", Path("/fixture/state.json"))
        packet = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
        context = packet["feedback_interpretations"]
        self.assertEqual(WRONG, context["rows"][0]["text"])
        self.assertEqual(FEEDBACK, context["rows"][0]["source_text"])
        self.assertIn("not the person's own wording", context["instruction"])
        self.assertEqual(before, state)

    def test_revision_repair_gets_the_same_source_and_scope_without_extra_file_reads(self):
        state = self.state()
        human = {"text": "Keep the chosen behavior", "basis": "user_answer", "answer_id": "A1"}
        state["goal_contract"]["body"]["accepted_assumptions"].append(human)
        state["answers"]["A1"] = {"kind": "answer", "text": human["text"]}
        original = {"format": "json", "content": {}, "truncated": False}
        _, handoff = repair_context.prompt_parts(
            state, {"original": {"stage": "glm_revise"}}, original, original, ("", {})
        )
        self.assertEqual(goals.feedback_interpretation_context(state), handoff["header"]["feedback_interpretations"])
        self.assertEqual(
            [human],
            handoff["protected_contract"]["accepted_assumptions"],
        )
        handoff["header"]["feedback_interpretations"]["rows"][0]["source_text"] = "changed"
        self.assertEqual(FEEDBACK, state["brief_feedback"][0]["text"])

    def test_actual_source_quotation_is_not_presented_as_an_editable_interpretation(self):
        state = self.state()
        state["goal_contract"]["body"]["accepted_assumptions"][0]["text"] = FEEDBACK
        self.assertEqual({}, goals.feedback_interpretation_context(state))
