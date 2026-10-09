"""Task-chat intent: real saved receipts and CLI boundaries with disposable runs."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_chat_bridge import ChatFixture


class ChatIntentTests(ChatFixture, unittest.TestCase):
    def test_question_card_answer_preserves_words_that_look_like_controls(self):
        self.make_run([{'id': 'q1', 'question': 'What should the button say?'},
                       {'id': 'q2', 'question': 'Where should it go?'}])
        row = self.chat('Continue?', question_id='q1', explicit_answer=True)
        self.settled()
        self.assertEqual('answer', row['kind'])
        self.assertIn('--answer', self.commands()[0])
        self.assertIn('q1=Continue?', self.commands()[0])
        self.assertEqual('Continue?', self.read_state()['answers']['q1'])
        self.assertEqual(['q2'], [q['id'] for q in self.read_state()['pending_questions']])
        self.assertNotIn('continued', self.read_state())

    def test_explicit_answer_without_current_question_refuses_any_effect(self):
        self.make_run([{'id': 'q1', 'question': 'Where?'}])
        before = (self.run / 'state.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'target one current question'):
            self.chat('Stop', explicit_answer=True)
        with self.assertRaises(ValueError):
            self.chat('Stop', question_id='q1', explicit_answer=True,
                      resolver_token='old-token')
        self.assertEqual([], self.commands())
        self.assertEqual(before, (self.run / 'state.json').read_bytes())

    def test_old_card_cannot_answer_reasked_question(self):
        from tools.dashboard.tests.test_pending_decisions import publish
        self.make_run([{'id': 'q1', 'question': 'Which format?'}])
        old = dict(self.read_state()['resolver_human_request'])
        self.state['pending_questions'] = [{'id': 'q1', 'question': 'Which format after the correction?'}]
        publish(self.state)
        self.save_state()
        before = (self.run / 'state.json').read_bytes()
        with self.assertRaises(ValueError):
            self.chat('JSON', question_id='q1', explicit_answer=True,
                      resolver_request=old['request_id'], resolver_token=old['request_token'])
        self.assertEqual(before, (self.run / 'state.json').read_bytes())
        self.assertEqual([], self.commands())

    def test_questions_and_typed_controls_never_mutate_runner_state(self):
        self.make_run()
        before = (self.run / "state.json").read_bytes()
        for index, (text, kind) in enumerate((
                ("How's it going?", "question"), ("Why is this still open?", "question"),
                ("status", "question"), ("yes", "approval"), ("approve", "approval"),
                ("stop", "control"), ("pause after this step", "control"))):
            with self.subTest(text=text), patch.object(self.console, "intervene") as intervene, \
                    patch.object(self.console, "enqueue") as enqueue:
                row = self.chat(text, request_id=f"safe-message-{index}")
                self.assertEqual(kind, row["kind"])
                self.assertEqual("received", row["status"])
                self.assertTrue(row["reply"])
                intervene.assert_not_called()
                enqueue.assert_not_called()
                self.assertEqual(before, (self.run / "state.json").read_bytes())
        restored = self.make_console().task_view(self.workspace, self.run)["chat_messages"]
        self.assertEqual(7, len(restored))
        self.assertTrue(all(row["classification_rule"] == "context-first-v1" for row in restored))
        self.assertFalse((self.root / "inbox.json").exists())

    def test_why_not_done_cites_unchecked_requirements_and_open_findings_read_only(self):
        from copy import deepcopy

        from dashboard_chat_intent import status_reply
        view={'status':'PAUSED_REPEATED_FAILURE', 'criteria':[{'id':'R1','criterion':'Saving survives restart'},
            {'id':'R2','criterion':'Errors stay visible'}],
            'validation':{'criterion_results':[{'id':'R2','status':'FAIL'}]},
            'monitor':{'findings':[{'id':'F1','finding':'Saved edits disappear after restart'}]}}
        before=deepcopy(view)
        reply=status_reply(view)
        self.assertIn('R1: Saving survives restart (unchecked)',reply)
        self.assertIn('R2: Errors stay visible (unchecked)',reply)
        self.assertIn('not been inspected', reply)
        from dashboard_verification import digest
        inspected=deepcopy(view)
        inspected['verification']={'version':1,'freshness':'current','contract_token':None,
            'criteria_token':digest(view['criteria']),'report_token':digest(view['validation']),
            'coverage':[{'id':'R2','state':'failed'}]}
        self.assertIn('R2: Errors stay visible (failed)',status_reply(inspected))
        self.assertIn('F1: Saved edits disappear after restart',reply)
        self.assertEqual(before,view)

    def test_status_and_control_do_not_answer_an_open_question(self):
        self.make_run([{"id": "q1", "question": "Which platform?"}])
        before = (self.run / "state.json").read_bytes()
        for index, text in enumerate(("How's it going?", "status", "stop")):
            with patch.object(self.console, "enqueue") as enqueue, patch.object(self.console, "intervene") as intervene:
                row = self.chat(text, question_id="q1", request_id=f"pending-status-{index}")
                self.assertIn(row["kind"], ("question", "control"))
                self.assertIsNone(row["question_id"])
                enqueue.assert_not_called()
                intervene.assert_not_called()
        self.assertEqual(before, (self.run / "state.json").read_bytes())

    def test_status_during_operational_escalation_is_read_only(self):
        self.make_run()
        current = self.console.view(self.workspace, self.run)
        with patch.object(self.console, "view", return_value={
                **current, "human_escalation": {"scope": "operational_exhaustion"}}), \
                patch.object(self.console, "intervene") as intervene:
            row = self.chat("Why is this still open?")
        self.assertEqual("question", row["kind"])
        intervene.assert_not_called()

    def test_unconfirmed_change_and_declined_change_leave_plan_byte_identical(self):
        self.make_run()
        before = (self.run / "state.json").read_bytes()
        with patch.object(self.console, "intervene") as intervene:
            row = self.chat("Actually, use SMS instead of email")
            self.assertEqual("awaiting_confirmation", row["status"])
            self.assertEqual("proposed_change", row["kind"])
            self.assertEqual(row, self.chat("Actually, use SMS instead of email"))
            declined = self.chat("Actually, use SMS instead of email", decision="question",
                                 decision_token=row["confirmation"]["token"])
            self.assertEqual("question", declined["kind"])
            self.assertEqual("declined", declined["confirmation"]["status"])
            self.assertEqual(before, (self.run / "state.json").read_bytes())
            intervene.assert_not_called()

    def test_only_matching_confirmation_submits_once_and_survives_restart(self):
        self.make_run()
        row = self.chat("Use SMS instead of email")
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.chat("Use SMS instead of email", decision="confirm", decision_token="wrong")
        self.assertFalse((self.root / "inbox.json").exists())
        fields = {"decision": "confirm", "decision_token": row["confirmation"]["token"]}
        accepted = self.chat("Use SMS instead of email", **fields)
        self.assertEqual("received", accepted["status"])
        self.assertEqual("correction", accepted["kind"])
        self.assertEqual("confirmed", accepted["confirmation"]["status"])
        self.assertTrue(accepted["receipt"]["durable"])
        self.assertEqual(accepted, self.chat("Use SMS instead of email", **fields))
        self.assertEqual(accepted, self.make_console().chat({
            "workspace": str(self.workspace), "run": str(self.run),
            "request_id": row["id"], "text": row["submitted_text"], **fields}))
        self.assertEqual(1, len(json.loads((self.root / "inbox.json").read_text())))
        with self.assertRaisesRegex(ValueError, "different saved decision"):
            self.chat("Use SMS instead of email", decision="question", decision_token=fields["decision_token"])

    def test_stale_plan_confirmation_and_forged_first_request_refuse_effect(self):
        self.make_run()
        with self.assertRaisesRegex(ValueError, "saved message"):
            self.chat("Change the plan", decision="confirm", decision_token="forged")
        row = self.chat("Change the plan")
        current = self.console.view(self.workspace, self.run)
        with patch.object(self.console, "view", return_value={**current, "goal_token": "r2:new"}), \
                self.assertRaisesRegex(ValueError, "plan changed"):
            self.chat("Change the plan", decision="confirm", decision_token=row["confirmation"]["token"])
        self.assertFalse((self.root / "inbox.json").exists())
        self.assertEqual("pending", self.console._chat_rows(self.run)[0]["confirmation"]["status"])

    def test_uncertain_confirm_does_not_blindly_retry_on_replayed_click(self):
        self.make_run()
        row = self.chat("Change the plan")
        marker = self.root / "bad-receipt"
        marker.touch()
        fields = {"decision": "confirm", "decision_token": row["confirmation"]["token"]}
        failed = self.chat("Change the plan", **fields)
        self.assertEqual("error", failed["status"])
        marker.unlink()
        self.assertEqual(failed, self.chat("Change the plan", **fields))
        self.assertFalse((self.root / "inbox.json").exists())
        accepted = self.chat("Change the plan", retry=True, **fields)
        self.assertEqual("received", accepted["status"])
        self.assertEqual(1, len(json.loads((self.root / "inbox.json").read_text())))

    def test_failed_delivery_cannot_retry_against_a_changed_plan(self):
        self.make_run()
        row = self.chat("Change the plan")
        fields = {"decision": "confirm", "decision_token": row["confirmation"]["token"]}
        marker = self.root / "bad-receipt"
        marker.touch()
        failed = self.chat("Change the plan", **fields)
        self.assertEqual("error", failed["status"])
        marker.unlink()
        current = self.console.view(self.workspace, self.run)
        for decision in ({}, fields):
            with patch.object(self.console, "view", return_value={**current, "goal_token": "r2:new"}), \
                    patch.object(self.console, "intervene", return_value={"status": "failed", "error": "test sentinel"}) as intervene, \
                    self.assertRaisesRegex(ValueError, "plan changed"):
                self.chat("Change the plan", retry=True, **decision)
            intervene.assert_not_called()
        self.assertFalse((self.root / "inbox.json").exists())

    def test_new_pending_question_does_not_consume_confirmation(self):
        self.make_run()
        row = self.chat("Change the plan")
        current = self.console.view(self.workspace, self.run)
        pending = {**current, "questions": [{"id": "q2", "question": "Which project?"}],
                   "human_escalation": {"scope": "clarification"}}
        with patch.object(self.console, "view", return_value=pending), \
                self.assertRaisesRegex(ValueError, "Choose which question"):
            self.chat("Change the plan", decision="confirm", decision_token=row["confirmation"]["token"])
        self.assertEqual("pending", self.console._chat_rows(self.run)[0]["confirmation"]["status"])
        self.assertFalse((self.root / "inbox.json").exists())
        delivered = self.chat("Change the plan", decision="confirm", decision_token=row["confirmation"]["token"])
        self.assertEqual("received", delivered["status"])

    def test_selected_question_is_an_answer_and_never_plan_approval(self):
        self.make_run([{"id": "q1", "question": "Should the timeout be one hour?"}])
        row = self.chat("yes", question_id="q1")
        self.settled()
        self.assertEqual("answer", row["kind"])
        commands = self.commands()
        self.assertTrue(any("--answer" in command for command in commands))
        self.assertFalse(any("--approve-goal" in command for command in commands))


if __name__ == "__main__":
    unittest.main()
