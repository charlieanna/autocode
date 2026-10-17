"""The chat surface is a projection of public task history and admitted actions."""

import copy
import shlex
import unittest

import autocode_chat_view as chat


class ChatViewTests(unittest.TestCase):
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
