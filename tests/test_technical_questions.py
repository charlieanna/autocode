"""Internal planning choices are AutoCode's work, never human authorization."""

import copy
import json
import unittest
from pathlib import Path

import autocode_chat_history as chat_history
import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
import autocode_report_repair_context as repair_context
import autocode_requirement_cues as requirement_cues
import autocode_source_log as source_log
import autocode_technical_questions as technical
import autocode_util as util
from goal_fixtures import body
from units import autoplanner as planner

from .test_autoplanner_step2 import EpisodeCase, clarification_only, discovery, requirements
from .test_autoplanner_step3 import NO_TASK


def question(qid="Q1", **extra):
    return {
        "id": qid,
        "question": "May AC4 use the complete CSV fixture and exact Python command?",
        "why": "Make the example executable without changing the requested output.",
        "options": [],
        "proposed_default": "Use the complete fixture and python3 reports/export.py.",
        "kind": "decision",
        "category": "technical",
        "delegable": False,
        **extra,
    }


def state():
    return {
        "version": 3,
        "task_id": "task-technical",
        "task": "Add JSON output while retaining the existing totals.",
        "workspace": "/absent-workspace",
        "status": "RUNNING",
        "next_stage": "astra_discovery",
        "settings": {"joint_planning": True},
        "answers": {},
        "user_events": [],
    }


class TechnicalQuestionPolicyTests(unittest.TestCase):
    def test_selects_technical_defaults_without_requiring_human_delegation(self):
        rows = [question(), question("Q2", kind="inferable")]
        original = copy.deepcopy(rows)
        self.assertEqual((rows, []), technical.split(rows, set()))
        self.assertEqual(original, rows)

    def test_keeps_human_intent_protected_and_legacy_questions_visible(self):
        rows = [
            question(str(i), category=category)
            for i, category in enumerate(
                ("behavior", "requested_outcome", "permission", "cost", "quota", "external_side_effect", "other")
            )
        ]
        rows += [question("empty", proposed_default=" "), question("fact", kind="discoverable")]
        legacy = question("legacy")
        legacy.pop("category")
        rows.append(legacy)
        self.assertEqual(([], rows), technical.split(rows, set()))

    def test_never_self_answers_a_rejected_assumption_obligation(self):
        row = question()
        self.assertEqual(([], [row]), technical.split([row], {"Q1"}))

    def test_records_an_internal_answer_without_changing_user_intent(self):
        current = state()
        source_log.seed(current)
        current["clarification_episode"] = {"id": "unchanged", "investigation_used": True}
        before_log = copy.deepcopy(current["source_log"])
        row = question()
        self.assertEqual([], technical.settle(current, [row], "r1:original"))
        event = current["user_events"][-1]
        self.assertEqual("autocode", event["actor"])
        self.assertEqual("technical_decision", event["kind"])
        self.assertEqual(row["proposed_default"], event["text"])
        self.assertEqual("r1:original", event["contract_token"])
        self.assertEqual(row, event["question"])
        self.assertIn(event, current["user_events"])
        self.assertNotIn("starts_episode", event)
        self.assertEqual(before_log, current["source_log"])
        self.assertEqual({}, current["answers"])
        self.assertEqual("unchanged", current["clarification_episode"]["id"])
        self.assertEqual([current["task"]], [r["text"] for r in chat_history.messages(current)])
        row["question"] = "changed by caller"
        self.assertNotEqual(row, event["question"])

    def test_mixed_questions_return_only_genuine_user_choices(self):
        current = state()
        human = question("Q2", category="behavior", question="Should empty input print an empty object?")
        self.assertEqual([human], technical.settle(current, [question(), human], "r1:draft"))
        self.assertEqual(["Q1"], [e["question_id"] for e in current["user_events"]])
        self.assertEqual({}, current["answers"])

    def test_internal_receipts_never_become_requirement_sources_or_repair_authority(self):
        current = state()
        technical.settle(current, [question()], "r1:draft")
        self.assertEqual([current["task"]], requirement_cues.source_texts(current))
        self.assertEqual([current["task"]], requirement_cues.scan_texts(current))
        self.assertEqual(
            [], repair_context.requirements_sources(current, "requirements_gather")["requirements_correction_sources"]
        )

    def test_an_unresolved_workspace_fact_is_not_answered_with_a_guessed_default(self):
        current = state()
        current["clarification_episode"] = {"converted_to_decision": ["Q1"]}
        row = question()
        self.assertEqual([row], technical.settle(current, [row], "r1:draft"))
        self.assertEqual([], current["user_events"])

    def test_repeated_internal_questions_require_report_repair_instead_of_another_planning_loop(self):
        current = state()
        technical.settle(current, [question()], "r1:draft")
        before = copy.deepcopy(current)
        with self.assertRaisesRegex(ValueError, "already settled"):
            technical.settle(current, [question()], "r2:draft")
        self.assertEqual(before, current)
        current["user_events"].append({"kind": "brief_feedback", "actor": "user_cli", "text": "Use a different input."})
        self.assertEqual([], technical.settle(current, [question()], "r3:draft"))


class TechnicalDraftTests(unittest.TestCase):
    def test_nonjoint_runs_keep_their_existing_human_question_path(self):
        current = state()
        current["settings"]["joint_planning"] = False
        draft = body()
        draft["open_blocking_questions"] = [question()]
        lifecycle.install_draft(current, draft, origin="astra_discovery")
        self.assertEqual("RESOLVER_PENDING", current["status"])
        self.assertEqual([], current["user_events"])
        self.assertEqual(["Q1"], [q["id"] for q in lifecycle.human.internal_questions(current)])

    def test_all_internal_questions_redraft_without_a_human_request_or_approval(self):
        current = state()
        draft = body()
        draft["open_blocking_questions"] = [question()]
        lifecycle.install_draft(current, draft, origin="glm_revise")
        self.assertEqual("RUNNING", current["status"])
        self.assertEqual("astra_discovery", current["next_stage"])
        self.assertEqual("DISCOVERING", current["phase"])
        self.assertEqual("none", lifecycle.human.evaluate(current))
        self.assertEqual([], current["pending_questions"])
        self.assertEqual([], current["goal_contract"]["body"]["open_blocking_questions"])
        self.assertEqual("draft", current["goal_contract"]["approval_status"])
        self.assertIsNone(current["goal_contract"]["approval_event"])
        self.assertFalse(goals.approved(current))
        contract = current["goal_contract"]
        self.assertEqual(util.digest({k: contract[k] for k in ("task_id", "revision", "body")}), contract["hash"])
        self.assertEqual(["Q1"], [q["id"] for q in draft["open_blocking_questions"]])

    def test_mixed_draft_publishes_only_the_human_question(self):
        current = state()
        draft = body()
        draft["open_blocking_questions"] = [question(), question("Q2", category="behavior")]
        lifecycle.install_draft(current, draft, origin="glm_draft")
        lifecycle.human.evaluate(current)
        self.assertEqual("WAITING_FOR_USER", current["status"])
        self.assertEqual(["Q2"], [q["id"] for q in current["pending_questions"]])
        self.assertEqual(["Q1"], [e["question_id"] for e in current["user_events"]])

    def test_queue_disabled_does_not_answer_a_private_candidate(self):
        current = state()
        draft = body()
        draft["open_blocking_questions"] = [question()]
        lifecycle.install_draft(current, draft, origin="glm_draft", queue_human=False)
        self.assertEqual({}, current["answers"])

    def test_internal_answers_do_not_authorize_protected_behavior_changes(self):
        current = state()
        draft = body()
        draft["open_blocking_questions"] = [question()]
        lifecycle.install_draft(current, draft, origin="glm_draft")
        changed = copy.deepcopy(current["goal_contract"]["body"])
        changed["required_behaviors"] = []
        changes = [
            {"item": draft["required_behaviors"][0], "change": "removed", "basis": "user_answer", "answer_id": "Q1"}
        ]
        with self.assertRaisesRegex(ValueError, "saved user"):
            goals.revision_guard(current, changed, changes, "glm_revise")

    def test_internal_answers_cannot_be_laundered_into_user_or_delegated_decisions(self):
        for basis in ("user_answer", "delegated"):
            with self.subTest(basis=basis):
                current = state()
                technical.settle(current, [question()], "r1:draft")
                draft = body()
                draft["accepted_assumptions"] = [{"text": "A new user decision", "basis": basis, "answer_id": "Q1"}]
                with self.assertRaisesRegex(ValueError, "saved user event"):
                    lifecycle.validate_body(current, draft)

    def test_redrafting_an_internal_default_keeps_earlier_approved_scope_protected(self):
        current = state()
        approved = body()
        current["goal_contract"] = {
            "task_id": current["task_id"],
            "revision": 1,
            "hash": "approved",
            "body": approved,
            "origin": "astra_finalize",
            "approval_status": "approved",
            "approval_event": {"kind": "goal_approval"},
        }
        draft = copy.deepcopy(approved)
        draft["open_blocking_questions"] = [question()]
        lifecycle.install_draft(current, draft, origin="glm_revise")
        changed = body()
        changed["permission_boundaries"] = ["Allow network writes"]
        with self.assertRaisesRegex(ValueError, "user-backed"):
            lifecycle.install_draft(current, changed, origin="glm_draft")


class TechnicalPlanningTests(EpisodeCase):
    def test_requirements_redraft_internal_questions_before_asking_the_human(self):
        self.state["settings"]["spec_first"] = True
        self.apply("requirements_gather", requirements([question()], assistant_reply="May AC4 use the full fixture?"))
        self.assertEqual("RUNNING", self.state["status"])
        self.assertEqual("requirements_gather", self.state["next_stage"])
        self.assertEqual("none", lifecycle.human.evaluate(self.state))
        self.assertEqual({}, self.state["answers"])
        self.apply("requirements_gather", requirements([], assistant_reply="Here is what I understand."))
        self.assertEqual("escalate", lifecycle.human.evaluate(self.state))
        self.assertEqual("WAITING_FOR_USER", self.state["status"])
        self.assertTrue(self.state["pending_questions"][0]["id"].startswith("requirements-review-"))

    def test_v2_requirements_redraft_internal_questions_without_human_authority(self):
        from .test_planning_flow_v2 import requirements as v2_requirements

        current = state()
        current["settings"]["planning_flow"] = "v2"
        draft = v2_requirements()
        draft["open_blocking_questions"] = [question()]
        lifecycle.apply_requirements(current, draft, artifact_sha256="fixture")
        self.assertEqual("RUNNING", current["status"])
        self.assertEqual("requirements", current["next_stage"])
        self.assertEqual("none", lifecycle.human.evaluate(current))
        self.assertEqual({}, current["answers"])

    def test_planning_prompts_distinguish_internal_choices_from_human_authorization(self):
        for stage in ("requirements_gather", "astra_discovery", "glm_revise", "astra_finalize"):
            with self.subTest(stage=stage):
                prompt, _ = planner.context(self.state, stage, Path("/fixture/state.json"))
                for clause in (
                    "INTERNAL TECHNICAL DECISIONS",
                    "technical_decision",
                    "never a user_answer or delegated",
                    "plain words without criterion IDs or stage names",
                ):
                    self.assertTrue(clause in prompt, f"{stage} is missing {clause}")

    def test_discovery_keeps_the_redraft_frontier_and_requires_a_reviewed_plan(self):
        self.apply("requirements_gather", requirements([]))
        self.apply("astra_discovery", discovery(clarification_only([question()])))
        self.assertEqual("RUNNING", self.state["status"])
        self.assertEqual("astra_discovery", self.state["next_stage"])
        self.assertEqual("none", lifecycle.human.evaluate(self.state))
        self.assertEqual("autocode", self.state["user_events"][-1]["actor"])
        prompt, _ = planner.context(self.state, "astra_discovery", Path("/fixture/state.json"))
        packet = json.loads(prompt.split("\nCURRENT HANDOFF DATA\n", 1)[1])
        self.assertTrue(any(row.get("kind") == "technical_decision" for row in packet["user_events"]))
        self.assertEqual({}, packet["saved_answers"])
        self.apply("astra_discovery", discovery(body()))
        self.assertEqual("astra_challenge", self.state["next_stage"])
        self.assertFalse(goals.approved(self.state))

    def test_revision_does_not_skip_redrafting_after_an_internal_answer(self):
        self.apply("requirements_gather", requirements([]))
        self.apply("astra_discovery", discovery(body()))
        self.apply("astra_challenge", {"summary": "Review", "concerns": []})
        draft = body()
        draft["open_blocking_questions"] = [question()]
        self.apply(
            "glm_revise",
            {
                "contract": draft,
                "summary": "Internal example repair",
                "code_refs": ["config.py:1"],
                "responses": [],
                "contract_changes": [],
                "requirement_trace": [],
            },
        )
        self.assertEqual("astra_discovery", self.state["next_stage"])
        self.assertEqual("none", lifecycle.human.evaluate(self.state))

    def test_final_review_returns_internal_choices_to_planning_without_approval(self):
        self.apply("requirements_gather", requirements([]))
        self.apply("astra_discovery", discovery(body()))
        self.apply("astra_challenge", {"summary": "Review", "concerns": []})
        draft = body()
        draft["open_blocking_questions"] = [question()]
        draft["initial_task"] = copy.deepcopy(NO_TASK)
        self.apply(
            "astra_finalize",
            {
                "contract": draft,
                "summary": "Internal example repair",
                "decisions": [],
                "contract_changes": [],
                "requirement_trace": [],
            },
        )
        self.assertEqual("astra_discovery", self.state["next_stage"])
        self.assertEqual("none", lifecycle.human.evaluate(self.state))
        self.assertFalse(goals.approved(self.state))
        # The restart is AutoCode's own answer, recorded so the next cycle can renew a spent review allowance.
        self.assertTrue(self.state["planning"]["internal_answer_restart"])


if __name__ == "__main__":
    unittest.main()
