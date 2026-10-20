"""Conversation policy and bounded, assistant-only streaming projection."""

import copy
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
import autocode_requirements_review as review
import autocode_role_transport as transport
import autocode_source_log as source_log
import autocode_task_input as task_input
import autocode_workflows as workflows


class RequirementsChatTests(unittest.TestCase):
    def test_malformed_active_or_repair_stage_has_no_streamed_reply(self):
        for active in (None, "terra", ["requirements_gather"], True, 42):
            for repair in (False, True):
                state = {"settings": {"spec_first": True}, "active_stage": active}
                if repair:
                    state["active_stage"] = {"stage": chat.STAGE + "_report_repair"}
                    state["pending_report_repair"] = {"original": active}
                before = copy.deepcopy(state)
                with self.subTest(active=active, repair=repair):
                    self.assertEqual("", chat.streaming_reply(state, "/run"))
                    self.assertEqual(before, state)

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

    def test_a_chat_run_talks_with_requirements_before_recognizing_the_job(self):
        for opener in ("hi", "What is your name?", "What's the weather today?", "Add a CSV export"):
            with self.subTest(opener=opener):
                state = {"task": opener, "settings": {"spec_first": True}}
                workflows.begin(state, chat.STAGE)
                self.assertTrue(chat.gather_first(state))
                self.assertEqual(chat.STAGE, state["next_stage"])
                self.assertIsNone(state["workflow"]["kind"])
        pinned = {"task": "Look over my change", "settings": {"spec_first": True}}
        workflows.begin(pinned, chat.STAGE)
        workflows.pin(pinned, "review")
        self.assertTrue(chat.gather_first(pinned))
        self.assertEqual((chat.STAGE, "review"), (pinned["next_stage"], pinned["workflow"]["kind"]))
        for state in (
            {"task": "hi", "settings": {}},
            {"task": "hi", "settings": {"spec_first": True}, "turns": [{"say": "hi"}]},
        ):
            with self.subTest(state=state):
                workflows.begin(state, chat.STAGE)
                self.assertFalse(chat.gather_first(state))
                self.assertEqual(workflows.STAGE, state["next_stage"])

    def test_recognition_does_not_probe_other_models_in_a_conversation(self):
        state = {"settings": {"spec_first": True}, "next_stage": workflows.STAGE}
        with mock.patch.object(preflight, "_billing_groups", return_value=[]) as groups:
            self.assertFalse(preflight.check(SimpleNamespace(), state, "/run", "/project"))
            groups.assert_not_called()

    @staticmethod
    def conversation(*replies, requirements=()):
        """A chat run whose Requirements reply found no work yet, then the person's replies."""
        return {
            "task": "What is your name?",
            "task_sha256": task_input.digest("What is your name?"),
            "settings": {"spec_first": True},
            "workflow": {"kind": None, "then": chat.STAGE},
            "next_stage": chat.STAGE,
            "requirements_handoff": {
                "report": {
                    "requirements": list(requirements),
                    "open_questions": [{"id": "Q1", "question": "What would you like to build or change?"}],
                }
            },
            "user_events": [
                {"kind": "brief_feedback", "actor": "user_cli", "id": f"feedback-{index}", "text": text}
                for index, text in enumerate(replies)
            ],
        }

    def test_until_requirements_finds_the_work_each_new_reply_becomes_the_task(self):
        state = self.conversation("What's the weather today?")
        self.assertTrue(chat.adopt_reply(state))
        self.assertEqual("What's the weather today?", state["task"])
        self.assertEqual(task_input.digest("What's the weather today?"), state["task_sha256"])
        self.assertEqual(chat.STAGE, state["next_stage"])
        self.assertFalse(chat.adopt_reply(state), "A reply becomes the task once")
        reply = "Add a CSV export to the reports page."
        state["user_events"].append({"kind": "brief_feedback", "actor": "user_cli", "id": "feedback-9", "text": reply})
        self.assertTrue(chat.adopt_reply(state))
        self.assertEqual(reply, state["task"])
        answered = self.conversation()
        answered["user_events"] = [{"kind": "answer", "actor": "user_cli", "question_id": "Q1", "text": reply + "\n"}]
        self.assertTrue(chat.adopt_reply(answered))
        self.assertEqual(reply, answered["task"])

    def test_chat_keeps_the_opening_message_after_a_reply_becomes_the_task(self):
        reply = "Add a CSV export to the reports page."
        spoken = "I'm AutoCode's requirements assistant. What would you like to build or change?"
        state = self.conversation()
        state["requirements_handoff"]["report"]["assistant_reply"] = spoken
        source_log.seed(state)
        source_log.save_feedback(
            state, {"kind": "brief_feedback", "actor": "user_cli", "id": "feedback-1", "text": reply}
        )
        self.assertTrue(chat.adopt_reply(state))
        rows = [(row["role"], row["text"]) for row in history.messages(state)]
        self.assertEqual([("user", "What is your name?"), ("assistant", spoken), ("user", reply)], rows)

    def test_reply_is_not_adopted_once_requirements_has_found_the_work(self):
        reply = "Use JSON instead."
        found = {"id": "R1", "text": "Export CSV", "source_quote": "Add a CSV export"}
        for change in (
            {"requirements_handoff": {"report": {"requirements": [found]}}},
            {"requirements_handoff": None},
            {"user_events": []},
            {"user_events": [{"kind": "brief_feedback", "actor": "user_intervention", "id": "f", "text": reply}]},
            {
                "user_events": [
                    {"kind": "answer", "actor": "user_cli", "question_id": review.PREFIX + "x", "text": "plan"}
                ]
            },
            {"settings": {}},
            {"goal_contract": {"body": {}}},
            {"turns": [{"say": "earlier"}]},
            # An answer to one of several questions waits for the rest; adopting it would move
            # the run off the stage the remaining questions belong to.
            {"pending_questions": [{"id": "Q2", "question": "Which reports?"}]},
        ):
            with self.subTest(change=change):
                state = {**self.conversation(reply), **change}
                before = copy.deepcopy(state)
                self.assertFalse(chat.adopt_reply(state))
                self.assertEqual(before, state)

    @staticmethod
    def approved(answer="plan", **change):
        state = {
            "task": "Add a CSV export",
            "settings": {"spec_first": True},
            "workflow": {"kind": None, "then": chat.STAGE},
            "next_stage": review.NEXT,
            "pending_questions": [],
            "requirements_handoff": {"report": {"intended_outcome": "CSV export", "requirements": [{"id": "R1"}]}},
        }
        state["answers"] = {review.review_question(state)["id"]: {"text": answer}}
        state.update(change)
        return state

    def test_the_job_is_recognized_once_the_person_approves_the_understanding(self):
        state = self.approved(" Plan ")
        self.assertTrue(chat.recognize_after_approval(state))
        self.assertEqual(workflows.STAGE, state["next_stage"])
        self.assertEqual((None, chat.STAGE), (state["workflow"]["kind"], state["workflow"]["then"]))
        pinned = self.approved(workflow={"kind": "review", "then": chat.STAGE})
        self.assertTrue(chat.recognize_after_approval(pinned))
        self.assertEqual(workflows.REVIEW_STAGE, pinned["next_stage"])
        for state in (
            self.approved("Also add a total row", next_stage=chat.STAGE),
            self.approved(workflow={"kind": "build", "then": chat.STAGE}),
            self.approved(pending_questions=[{"id": "Q2"}]),
            self.approved(settings={}),
            self.approved(turns=[{"say": "earlier"}]),
        ):
            with self.subTest(state=state):
                before = copy.deepcopy(state)
                self.assertFalse(chat.recognize_after_approval(state))
                self.assertEqual(before, state)
        legacy = self.approved()
        legacy.pop("workflow")
        self.assertFalse(chat.recognize_after_approval(legacy))

    def test_an_approved_build_is_planned_and_a_later_request_is_gathered_again(self):
        build = {"workflow": "build", "reason": "Adds a feature", "signals": []}
        state = self.approved()
        self.assertTrue(chat.recognize_after_approval(state))
        workflows.apply(state, build, {"output": None})
        self.assertEqual(review.NEXT, state["next_stage"], "Requirements already ran and was approved")
        self.assertFalse(state["workflow"].get("requirements_skipped"))
        # A follow-up starts from the saved pipeline entry (autocode_follow_up reads workflow.then).
        self.assertEqual(chat.STAGE, state["workflow"]["then"])
        workflows.begin(state, state["workflow"]["then"])
        workflows.apply(state, build, {"output": None})
        self.assertEqual(chat.STAGE, state["next_stage"])

    def test_an_approval_starts_a_job_with_its_own_first_stage_only_once(self):
        design = workflows.FIRST_STAGE["design"]
        pinned = self.approved(workflow={"kind": "design", "then": chat.STAGE}, stages=[{"stage": chat.STAGE}])
        self.assertTrue(chat.recognize_after_approval(pinned))
        self.assertEqual(design, pinned["next_stage"])
        for kind, ran in (
            ("design", [chat.STAGE, design, chat.STAGE]),
            ("bugfix", [chat.STAGE, workflows.STAGE, workflows.FIRST_STAGE["bugfix"]]),
        ):
            with self.subTest(kind=kind):
                later = self.approved(
                    workflow={"kind": kind, "then": chat.STAGE}, stages=[{"stage": stage} for stage in ran]
                )
                before = copy.deepcopy(later)
                self.assertFalse(chat.recognize_after_approval(later), "The job's first stage already ran")
                self.assertEqual(before, later)

    def test_the_recognizer_judges_the_job_from_the_confirmed_understanding(self):
        outcome = "Reports can be exported as CSV with a total row"
        state = {
            "task": "Add a CSV export",
            "settings": {"spec_first": True},
            "workflow": {"kind": None},
            "requirements_handoff": {"report": {"intended_outcome": outcome}},
        }
        text, _ = workflows.prompt(state)
        rules, data = text.split("\nCURRENT HANDOFF DATA\n", 1)
        self.assertEqual(outcome, json.loads(data)["confirmed_understanding"])
        self.assertIn("confirmed_understanding", rules)
        self.assertNotIn("greeting", rules.lower())
        for other in ({**state, "settings": {}}, {**state, "turns": [{"say": "x", "previous": {}}]}):
            with self.subTest(other=other):
                text, _ = workflows.prompt(other)
                self.assertNotIn("confirmed_understanding", json.loads(text.split("\nCURRENT HANDOFF DATA\n", 1)[1]))

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

    def test_malformed_stream_or_repair_identity_has_no_provisional_reply(self):
        for invalid in (None, "requirements_gather", ["requirements_gather"], True, 42):
            with self.subTest(invalid=invalid):
                state = {"settings": {"spec_first": True}, "active_stage": invalid}
                self.assertEqual("", chat.streaming_reply(state, Path("/unused-run")))
                state["active_stage"] = {"stage": chat.STAGE + "_report_repair"}
                state["pending_report_repair"] = invalid
                self.assertEqual("", chat.streaming_reply(state, Path("/unused-run")))
                state["pending_report_repair"] = {"original": invalid}
                self.assertEqual("", chat.streaming_reply(state, Path("/unused-run")))


if __name__ == "__main__":
    unittest.main()
