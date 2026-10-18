"""The chat surface is a projection of public task history and admitted actions."""

import copy
import json
import shlex
import unittest
from pathlib import Path

import autocode_chat_view as chat
import autocode_util as util


class ChatViewTests(unittest.TestCase):
    def test_connection_stop_explains_the_user_action_without_dashboard_jargon(self):
        state = {"status": "PAUSED_TRANSPORT_CHANGED", "task": "Make it friendlier"}
        result = self.project(state, need="resume")
        self.assertEqual(
            "Your model connection settings changed. Check Connections before continuing.", result["notice"]
        )
        self.assertIsNone(result["input"])
        for status in ("PAUSED_JOB_FAILURE", "PAUSED_PROVIDER_UNCERTAIN"):
            with self.subTest(status=status):
                text = chat.notice({"status": status})
                self.assertTrue(text)
                self.assertNotIn("Paused", text)
                self.assertNotIn("no task list", text)

    def test_running_notice_gates_on_live_supervision_only(self):
        self.assertEqual(
            "Working on your request…", chat.notice({"status": "RUNNING", "liveness": {"kind": "supervised"}})
        )
        self.assertEqual(
            "Working on your request…", chat.notice({"status": "RUNNING", "liveness": {"kind": "unsupervised"}})
        )
        self.assertIsNone(chat.notice({"status": "RUNNING", "liveness": {"kind": "unknown"}}))
        self.assertIsNone(chat.notice({"status": "RUNNING", "liveness": {"kind": "stopped"}}))
        self.assertIsNone(chat.notice({"status": "RUNNING"}))
        state = {"status": "RUNNING", "task": "Build it"}
        result = self.project(state, need="continue")
        self.assertIsNone(result["notice"])

    def project(self, state, need="approve_plan", done=False):
        return chat.project(
            state,
            {"status": state["status"], "done": done, "needs": {"kind": need}},
            workspace="/a project",
            run_dir="/a project/.autocode/run",
        )

    def test_plan_feedback_is_named_without_approval_or_provider_flags(self):
        state = {"status": "AWAITING_GOAL_APPROVAL", "task": "Build it", "user_events": []}
        before = copy.deepcopy(state)
        result = self.project(state)
        self.assertEqual("feedback", result["input"]["kind"])
        self.assertEqual(
            ["autocode", "--workspace", "/a project", "--run-dir", "/a project/.autocode/run", "--feedback", "MESSAGE"],
            shlex.split(result["input"]["next_command"]),
        )
        self.assertEqual(before, state)
        self.assertEqual(result, self.project(state))

    def test_completed_run_names_follow_up_only_for_real_completion(self):
        state = {"status": "TASK_COMPLETE", "task": "Build it"}
        self.assertEqual("follow_up", self.project(state, "done", True)["input"]["kind"])
        self.assertIsNone(self.project(state, "done", False)["input"])
        state["status"] = "RUNNING"
        self.assertIsNone(self.project(state, "continue")["input"])

    def test_operational_pause_and_human_review_cannot_be_bypassed_with_feedback(self):
        for request in (
            {"kind": "human_review"},
            {"kind": "blocker", "origin": {"pause_status": "PAUSED_PROVIDER_UNCERTAIN"}},
        ):
            state = {"status": "WAITING_FOR_USER", "user_request": request}
            public = {
                "status": state["status"],
                "needs": {"kind": "answer", "resolver_scope": "operational_exhaustion"},
            }
            self.assertIsNone(chat.project(state, public, workspace="/ws", run_dir="/run")["input"])

    def test_pending_operational_request_offers_bound_reply_only(self):
        state = {"status": "WAITING_FOR_USER", "user_request": {"kind": "blocker"}}
        need = {
            "kind": "answer",
            "resolver_scope": "blocker",
            "resolver_request_id": "req-1",
            "resolver_token": "current-token",
            "questions": [
                {"id": "q1", "question": "What changed?", "why": "Check the cause", "options": ["Leave blocked"]}
            ],
        }
        public = {"status": state["status"], "needs": need}
        result = chat.project(state, public, workspace="/ws", run_dir="/run")
        self.assertEqual("resolver_response", result["input"]["kind"])
        schema = json.loads((Path(__file__).resolve().parents[1] / "docs/schemas/status-view.schema.json").read_text())
        util.validate_schema(result, schema["properties"]["view"]["properties"]["chat"])
        self.assertIn("What changed?", result["messages"][-1]["text"])
        self.assertIn("Leave blocked", result["messages"][-1]["text"])
        self.assertEqual(
            [
                "AUTOCODE_RESOLVER_TOKEN=current-token",
                "autocode",
                "--workspace",
                "/ws",
                "--run-dir",
                "/run",
                "--resolver-request",
                "req-1",
                "--resolver-token",
                "-",
                "--resolver-response",
                "provide_information",
                "--resolver-message",
                "MESSAGE",
            ],
            shlex.split(result["input"]["next_command"]),
        )
        for missing in ("resolver_request_id", "resolver_token"):
            invalid = {**public, "needs": {k: v for k, v in need.items() if k != missing}}
            self.assertIsNone(chat.project(state, invalid, workspace="/ws", run_dir="/run")["input"])
        for change in ({"route": {"role": "sol"}}, {"resolver_scope": "requirements"}):
            invalid = {**public, "needs": {**need, **change}}
            self.assertIsNone(chat.project(state, invalid, workspace="/ws", run_dir="/run")["input"])
        human = {**state, "user_request": {"kind": "human_review"}}
        self.assertIsNone(chat.project(human, public, workspace="/ws", run_dir="/run")["input"])

    def test_saved_operational_reply_remains_visible_after_request_is_consumed(self):
        state = {
            "status": "PAUSED_COMPLETION_GATE",
            "task": "Build it",
            "resolver": {
                "human_responses": [{"request_id": "r1", "text": "Leave this task blocked", "actor": "user_cli"}],
                "human_escalations": {
                    "r1": {
                        "identity": {"proposal": {"request": {"questions": [{"question": "How should we proceed?"}]}}}
                    }
                },
            },
        }
        rows = self.project(state, "resume")["messages"]
        self.assertEqual(["Build it", "How should we proceed?", "Leave this task blocked"], [r["text"] for r in rows])

    def test_history_preserves_original_request_answers_feedback_and_followups(self):
        state = {
            "status": "TASK_COMPLETE",
            "task": "rewritten request with context",
            "turns": [{"previous": {"task": "Original request"}}],
            "user_events": [
                {"kind": "answer", "question": {"question": "Which language?"}, "text": "Python"},
                {"kind": "brief_feedback", "id": "f1", "text": "Keep dependencies out"},
                {"kind": "brief_feedback", "id": "f2", "text": "Add a follow-up"},
            ],
        }
        result = self.project(state, "done", True)
        self.assertEqual(
            [
                "Original request",
                "Which language?",
                "Python",
                "Keep dependencies out",
                "Add a follow-up",
                "The requested task is complete.",
            ],
            [row["text"] for row in result["messages"]],
        )
        self.assertEqual(result["messages"], self.project(copy.deepcopy(state), "done", True)["messages"])
        state["user_events"].append({"kind": "brief_feedback", "text": "New request"})
        self.assertNotEqual(result["context"], self.project(state, "done", True)["context"])

    def test_saved_discussion_answer_and_previous_response_are_visible(self):
        state = {
            "status": "TASK_COMPLETE",
            "task": "Explain it",
            "workflow": {"kind": "discuss"},
            "answer": {"answer": "It uses a queue.", "evidence": [{"claim": "FIFO ordering", "source": "queue.py:3"}]},
        }
        self.assertIn("It uses a queue.", self.project(state, "done", True)["messages"][-1]["text"])
        state.update(
            status="RUNNING",
            turns=[{"event_id": "f1", "previous": {"task": "Explain it", "chat_response": "It uses a queue."}}],
            user_events=[{"kind": "brief_feedback", "id": "f1", "text": "Add a test"}],
        )
        rows = self.project(state, "continue")["messages"]
        self.assertEqual(["Explain it", "It uses a queue.", "Add a test"], [row["text"] for row in rows])


if __name__ == "__main__":
    unittest.main()
