"""Conversation policy and bounded, assistant-only streaming projection."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import autocode_chat_history as history
import autocode_chat_view as view
import autocode_model_preflight as preflight
import autocode_requirements_chat as chat
import autocode_role_transport as transport
import autocode_source_log as source_log
import autocode_task_input as task_input
import autocode_workflows as workflows


class RequirementsChatTests(unittest.TestCase):
    def test_requirements_uses_its_saved_session_without_reusing_independent_review_sessions(self):
        state = {"settings": {"spec_first": True}, "sessions": {"requirements": "saved-chat", "reviewer": "separate"}}
        self.assertEqual("saved-chat", transport.resume_session(state, "requirements", chat.STAGE, True, False, True))
        for stage, role, repair, supported in [
            ("astra_challenge", "reviewer", False, True),
            (chat.STAGE, "requirements", True, True),
            (chat.STAGE, "requirements", False, False),
        ]:
            self.assertIsNone(transport.resume_session(state, role, stage, True, repair, supported))

    def test_chat_turn_does_not_probe_other_models_but_planning_keeps_preflight(self):
        state = {"settings": {"spec_first": True}, "next_stage": chat.STAGE}
        with mock.patch.object(preflight, "_billing_groups", return_value=[]) as groups:
            self.assertFalse(preflight.check(SimpleNamespace(), state, "/run", "/project"))
            groups.assert_not_called()
            state["next_stage"] = "astra_discovery"
            self.assertFalse(preflight.check(SimpleNamespace(), state, "/run", "/project"))
            groups.assert_called_once()

    def test_recognition_runs_before_requirements_in_a_conversation(self):
        for settings in ({"spec_first": True}, {}):
            with self.subTest(settings=settings):
                state = {"settings": settings}
                workflows.begin(state, chat.STAGE)
                self.assertEqual(workflows.STAGE, state["next_stage"])
                self.assertEqual(chat.STAGE, state["workflow"]["then"])

    def test_recognition_does_not_probe_other_models_in_a_conversation(self):
        state = {"settings": {"spec_first": True}, "next_stage": workflows.STAGE}
        with mock.patch.object(preflight, "_billing_groups", return_value=[]) as groups:
            self.assertFalse(preflight.check(SimpleNamespace(), state, "/run", "/project"))
            groups.assert_not_called()

    def test_first_reply_to_the_opening_becomes_the_task_and_the_job_is_recognized(self):
        reply = "Add a CSV export to the reports page."
        for event in (
            {"kind": "answer", "actor": "user_cli", "question_id": chat.INTRO, "text": reply},
            {"kind": "brief_feedback", "actor": "user_cli", "id": "feedback-1", "text": reply + "\n"},
        ):
            with self.subTest(kind=event["kind"]):
                state = {
                    "task": "Hi!",
                    "task_sha256": task_input.digest("Hi!"),
                    "settings": {"spec_first": True},
                    "workflow": {"kind": None, "then": chat.STAGE},
                    "next_stage": chat.STAGE,
                    "user_events": [event],
                }
                self.assertTrue(chat.adopt_reply(state))
                self.assertEqual(reply, state["task"])
                self.assertEqual(task_input.digest(reply), state["task_sha256"])
                self.assertEqual(workflows.STAGE, state["next_stage"])
                self.assertFalse(chat.adopt_reply(state), "Only the first reply names the work")

    def test_chat_keeps_the_greeting_after_the_reply_becomes_the_task(self):
        reply = "Add a CSV export to the reports page."
        state = {"task": "hi", "settings": {"spec_first": True}, "workflow": {"kind": None}}
        source_log.seed(state)
        source_log.save_feedback(
            state, {"kind": "brief_feedback", "actor": "user_cli", "id": "feedback-1", "text": reply}
        )
        self.assertTrue(chat.adopt_reply(state))
        rows = [(row["role"], row["text"]) for row in history.messages(state)]
        self.assertEqual(("user", "hi"), rows[0])
        self.assertEqual(1, rows.count(("user", reply)), rows)

    def test_reply_is_not_adopted_once_the_work_is_named(self):
        event = {"kind": "brief_feedback", "actor": "user_cli", "id": "feedback-1", "text": "Use JSON instead."}
        seed = {
            "task": "Hi!",
            "settings": {"spec_first": True},
            "workflow": {"kind": None},
            "next_stage": chat.STAGE,
            "user_events": [event],
        }
        for change in (
            {"task": "Add a CSV export"},
            {"workflow": {"kind": "build"}},
            {"stages": [{"stage": workflows.STAGE}]},
            {"requirements_handoff": {"report": {}}},
            {"user_events": [event, {**event, "id": "feedback-2"}]},
            {"user_events": [{**event, "actor": "user_intervention"}]},
            {"settings": {}},
        ):
            with self.subTest(change=change):
                state = {**seed, **change}
                before = dict(state)
                self.assertFalse(chat.adopt_reply(state))
                self.assertEqual(before, state)

    def test_requirements_reads_the_workspace_once_the_request_is_named(self):
        from units import autoplanner

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "reports").mkdir()
            (root / "reports" / "export.py").write_text("def export():\n    pass\n")
            roles = ("astra", "glm", "terra", "sol", "requirements", "plan_reviewer", "resolver")
            state = {
                "version": 3,
                "task_id": "task-fixture",
                "task": "Add a CSV export to the reports page.",
                "workspace": str(root),
                "status": "RUNNING",
                "stages": [],
                "answers": {},
                "user_events": [],
                "settings": {
                    "spec_first": True,
                    "joint_planning": True,
                    "context_soft_tokens": 10000,
                    "roles": {name: {"model": "fixed-model", "engine": "codex"} for name in roles},
                },
            }
            prompt, _ = autoplanner.context(state, chat.STAGE, root / "state.json")
            packet = json.loads(prompt.split("\nCURRENT HANDOFF DATA\n", 1)[1])
            self.assertIn("reports/export.py", json.dumps(packet.get("workspace_inventory")))

    def test_opening_only_handles_a_bare_greeting_before_any_dispatch(self):
        seed = {"task": "Hi!", "settings": {"spec_first": True}}
        self.assertTrue(chat.opening(seed))
        for change in (
            {"task": "Hi, fix this bug"},
            {"settings": {}},
            {"stages": [{"stage": "terra"}]},
            {"workflow": {"kind": "review"}},
            {"user_events": [{"text": "Build a library app"}]},
        ):
            with self.subTest(change=change):
                self.assertFalse(chat.opening({**seed, **change}))

    def test_partial_reply_decodes_text_without_showing_json_or_other_fields(self):
        for raw, expected in [
            ('{"assistant_reply":"Hi! What', "Hi! What"),
            ('{"assistant_reply":"Line one\\nLine two\\u', "Line one\nLine two"),
            ('{"assistant_reply":"Hello \\u00eb","secret":"hidden"}', "Hello ë"),
            ('{"summary":"not a reply"}', ""),
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(expected, chat.partial_string(raw))

    def test_stream_is_bounded_to_the_current_requirements_assistant_events(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = root / "requirements.jsonl"
            rows = [
                {"type": "tool", "part": {"text": '{"assistant_reply":"tool output"}'}},
                {"type": "reasoning", "part": {"text": '{"assistant_reply":"private reasoning"}'}},
                {"type": "text", "part": {"id": "reply", "text": '{"assistant_reply":"Who will use'}},
            ]
            events.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            state = {"settings": {"spec_first": True}, "active_stage": {"stage": chat.STAGE, "events": str(events)}}
            self.assertEqual("Who will use", chat.streaming_reply(state, root))
            shown = view.project(state, {"status": "RUNNING", "needs": {"kind": "continue"}}, run_dir=root)
            self.assertTrue(shown["messages"][-1]["streaming"])
            self.assertEqual("Who will use", shown["messages"][-1]["text"])
            with events.open("a") as target:
                target.write(json.dumps({"type": "autocode_chat_reply", "reply": "Who will use it?"}) + "\n")
            shown = view.project(state, {"status": "RUNNING", "needs": {"kind": "continue"}}, run_dir=root)
            self.assertEqual("Who will use it?", shown["messages"][-1]["text"])
            state["pending_report_repair"] = {"original": dict(state["active_stage"])}
            state["active_stage"] = {"stage": chat.STAGE + "_report_repair", "events": str(root / "repair.jsonl")}
            self.assertEqual("Who will use it?", chat.streaming_reply(state, root))
            self.assertEqual("", chat.streaming_reply(state, root / "other-run"))
            state["active_stage"]["stage"] = "astra_challenge"
            self.assertEqual("", chat.streaming_reply(state, root))


if __name__ == "__main__":
    unittest.main()
