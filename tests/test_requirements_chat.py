"""Conversation policy and bounded, assistant-only streaming projection."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import autocode_chat_view as view
import autocode_model_preflight as preflight
import autocode_requirements_chat as chat
import autocode_role_transport as transport


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
