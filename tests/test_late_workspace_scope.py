"""Late and follow-up repository scope must reach a human before a plan can run.

The actual job, human-boundary and public view run in process; only reproduction
commands are mocked. Provider-backed TaskRun coverage lives in the CLI tests.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_bug_job as bug_job
import autocode_compact_planning as compact
import autocode_goals as goals
import autocode_requirements_handoff as handoff
import autocode_resolver_human as human
import autocode_run_records as records
import autocode_run_view as run_view
import autocode_scope_question as scope
import autocode_util as util
import autocode_workspace_scope as workspace_scope
from units import autoresolver

from .test_bug_job import diagnosis, state_for
from .test_requirements_handoff import report
from .test_scope_question import HISTORY, answered_state
from .test_scope_question import state as scoped_state


class ScopeReceiptTests(unittest.TestCase):
    def test_unbound_or_unauthenticated_answers_never_choose_scope(self):
        for answer in (
            scope.THIS_REPOSITORY,
            {"text": scope.THIS_REPOSITORY},
            {"actor": "user_cli", "question": scope.question(scoped_state(HISTORY)), "text": scope.THIS_REPOSITORY},
        ):
            with self.subTest(answer=answer):
                value = scoped_state(HISTORY, answers={"workspace-scope-1": answer})
                self.assertIsNone(scope.decision(value))
                self.assertIsNotNone(scope.question(value))

    def test_new_work_same_repository_or_new_turn_requires_a_new_choice(self):
        for replacement in ({**HISTORY, "work": "Also migrate old records"}, {**HISTORY, "target": "billing"}):
            value = answered_state(HISTORY)
            old = scope.question(scoped_state(HISTORY))
            value["requirements_handoff"]["report"]["workspace_scope"]["external_parts"].append(replacement)
            self.assertIsNone(scope.decision(value))
            self.assertNotEqual(old["id"], scope.question(value)["id"])
            value.update(pending_questions=[old], answers={})
            with self.assertRaisesRegex(ValueError, "scope changed"):
                goals.answer(value, old["id"], scope.THIS_REPOSITORY)
        value = answered_state(HISTORY)
        value["turns"] = [{"say": "Also fix the consumer"}]
        self.assertIsNone(scope.decision(value))

    def test_exact_authenticated_legacy_receipt_survives_only_its_original_intent(self):
        value = answered_state(HISTORY)
        receipt = next(iter(value["answers"].values()))
        receipt["question"]["id"] = "workspace-scope-1"
        self.assertEqual("this_repository", scope.decision(value))
        event = {"id": "feedback-1", "actor": "user_cli", "text": "Change the task"}
        value["brief_feedback"] = [event]
        value["user_events"].append(event)
        self.assertIsNone(scope.decision(value))

    def test_exclusions_cover_exact_work_not_only_a_target_substring(self):
        value = answered_state(HISTORY)
        for exclusions in (
            ["Not covered: changes in epp-history: unrelated work"],
            ["Not covered: changes in epp-history-admin: Store the delivery window"],
        ):
            with self.subTest(exclusions=exclusions), self.assertRaises(ValueError):
                scope.check(value, {"scope_exclusions": exclusions})

    def test_model_refresh_retains_discovery_but_authenticated_feedback_can_replace_it(self):
        value = scoped_state()
        workspace_scope.record(value, {"external_parts": [HISTORY]}, {}, stage="investigate_bug")
        first = scope.question(value)
        workspace_scope.record(value, {"external_parts": []}, {}, stage="requirements_gather")
        self.assertEqual(first, scope.question(value))
        event = {"id": "feedback-1", "actor": "user_cli", "text": "History supports it; fix only the client"}
        value.update(brief_feedback=[event], user_events=[event])
        self.assertNotEqual(first["id"], scope.question(value)["id"])
        workspace_scope.record(value, {"external_parts": []}, {}, stage="requirements_gather")
        self.assertIsNone(scope.question(value))


class LateScopeBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "client.py").write_text("HISTORY_SERVICE = 'history-service'\n")
        self.state = state_for(str(self.root), "Fix duplicated renewals.")
        self.state.update(
            task_id="task",
            next_stage="investigate_bug",
            answers={},
            user_events=[],
            iteration=1,
            history=[],
            run_dir=str(self.root / ".autocode/run"),
        )
        self.part = {
            "target": "history-service",
            "work": "Deduplicate delivery events",
            "source_quote": "",
            "evidence": "client.py:1",
        }

    def retained_contract(self):
        body = {"open_blocking_questions": [], "scope_exclusions": [], "acceptance_criteria": []}
        contract = {"task_id": "task", "revision": 1, "body": body}
        contract.update(hash=util.digest(contract), approval_status="approved", approval_event={"old": True})
        self.state["goal_contract"] = contract
        self.state["current_task"] = {"id": "old-task"}
        self.state["turns"] = [{"say": "Fix the newly reported failure", "stage_index": len(self.state["stages"])}]

    def apply(self, value):
        output = self.root / ".autocode/investigation.json"
        output.parent.mkdir(exist_ok=True)
        output.write_text(json.dumps(value))
        record = {
            "stage": "investigate_bug",
            "output": str(output),
            "source_revision": "rev-1",
            "exit_code": 0,
            "changed_files": [],
        }
        self.state["stages"].append(record)
        with mock.patch.object(autoresolver.launch_inputs, "runners", return_value=(mock.Mock(), None)):
            # This saved-response fixture explicitly says its reproduction needs
            # an unavailable external service; it performs no probe/provider call.
            autoresolver.apply_job("investigate_bug", self.state, value, record, self.root)
        records.normalize_human_boundary(self.state, self.state["run_dir"])
        return run_view.view(self.state)

    def test_reproduced_questions_are_nondelegable_with_a_retained_goal(self):
        self.retained_contract()
        question = "Which event semantics should the fix preserve?"
        view = self.apply(diagnosis(questions=[question]))
        self.assertEqual("answer", view["needs"]["kind"], view)
        [offered] = view["needs"]["questions"]
        self.assertEqual(question, offered["question"])
        self.assertEqual("", offered["proposed_default"])
        packet = bug_job.large_correction(self.state)
        self.assertEqual([question], packet["questions"])
        self.assertIn("conclusion", packet)
        self.assertIn("the diagnosis still needs a human decision", compact.decide(self.state)["reasons"])
        with self.assertRaises(ValueError):
            goals.answer(self.state, offered["id"], "", delegated=True)
        goals.answer(self.state, offered["id"], "Preserve one event per renewal")
        self.assertEqual("investigate_bug", self.state["next_stage"])
        self.assertEqual("draft", self.state["goal_contract"]["approval_status"])

    def test_grounded_late_scope_is_public_before_planning_and_answer_is_not_approval(self):
        self.retained_contract()
        value = diagnosis(workspace_scope={"spans_repositories": True, "external_parts": [self.part]})
        view = self.apply(value)
        [offered] = view["needs"]["questions"]
        self.assertTrue(offered["id"].startswith(scope.PREFIX))
        self.assertEqual("", offered["proposed_default"])
        self.assertEqual([{"target": self.part["target"], "work": self.part["work"]}], view["scope"]["external_parts"])
        self.assertIn("history-service", [row["repository"] for row in view["scope"]["briefs"]])
        with self.assertRaises(util.Paused):
            goals.execution_guard(self.state)
        goals.answer(self.state, offered["id"], scope.THIS_REPOSITORY)
        self.assertEqual("draft", self.state["goal_contract"]["approval_status"])
        self.assertEqual("astra_discovery", self.state["next_stage"])
        self.assertEqual("this_repository", scope.decision(self.state))
        with self.assertRaises(ValueError):
            scope.check(self.state, {"scope_exclusions": []})

    def test_normalization_and_dropped_scope_preserve_exact_diagnosis_evidence(self):
        for part in (
            {**self.part, "target": "  history-service  "},
            {**self.part, "evidence": "../outside.py"},
            {**self.part, "evidence": "missing.py"},
        ):
            with self.subTest(part=part):
                value = diagnosis(workspace_scope={"spans_repositories": True, "external_parts": [part]})
                self.apply(value)
                self.assertIsNotNone(bug_job.diagnosis_artifact(self.state, self.root))
                self.assertEqual(value["workspace_scope"], self.state["investigation"]["workspace_scope"])

    def test_followup_requirements_publish_scope_despite_existing_contract(self):
        self.retained_contract()
        value = report(
            intended_outcome="Fix duplicated renewals",
            required_behaviors=["Deduplicate renewals"],
            acceptance_tests=["One event is stored"],
            open_questions=[],
            source_refs=["client.py:1"],
            workspace_scope={"spans_repositories": True, "external_parts": [self.part]},
        )
        handoff.accept(
            self.state,
            value,
            {"output": None, "source_revision": "rev-1"},
            check_code_refs=lambda *_: None,
            queue=human.queue,
        )
        records.normalize_human_boundary(self.state, self.state["run_dir"])
        view = run_view.view(self.state)
        self.assertEqual("answer", view["needs"]["kind"], view)
        self.assertTrue(view["needs"]["questions"][0]["id"].startswith(scope.PREFIX))
        with self.assertRaises(ValueError):
            scope.check(self.state, self.state["goal_contract"]["body"])

    def test_execution_guard_recovery_publishes_the_current_scope_question(self):
        self.retained_contract()
        workspace_scope.record(self.state, {"external_parts": [self.part]}, {}, stage="investigate_bug")
        with self.assertRaises(util.Paused) as caught:
            goals.execution_guard(self.state)
        self.state["status"] = caught.exception.status
        records.normalize_human_boundary(self.state, self.state["run_dir"])
        view = run_view.view(self.state)
        self.assertEqual("answer", view["needs"]["kind"], view)
        self.assertEqual(scope.question(self.state)["id"], view["needs"]["questions"][0]["id"])


if __name__ == "__main__":
    unittest.main()
