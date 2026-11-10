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

    def test_a_chat_run_talks_with_requirements_before_the_job_is_named(self):
        for opener in ("hi", "What is your name?", "What's the weather today?", "Add a CSV export"):
            with self.subTest(opener=opener):
                state = {"task": opener, "settings": {"spec_first": True}}
                workflows.begin_requirements(state)
                self.assertTrue(chat.gather_first(state))
                self.assertEqual(chat.STAGE, state["next_stage"])
                self.assertIsNone(state["workflow"]["kind"])
        # #3010: every kind, including a pinned review, starts at Requirements first.
        for kind in ("review", "build", "discuss", "design", "bugfix"):
            state = {"task": "Work on this", "settings": {"spec_first": True}}
            workflows.begin_requirements(state)
            workflows.pin(state, kind)
            self.assertTrue(chat.gather_first(state), kind)
        for state in (
            {"task": "hi", "settings": {}},
            {"task": "hi", "settings": {"spec_first": True}, "turns": [{"say": "hi"}]},
        ):
            with self.subTest(state=state):
                workflows.begin_requirements(state)
                self.assertFalse(chat.gather_first(state))
                self.assertEqual(chat.STAGE, state["next_stage"])

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

    def test_the_job_starts_once_the_person_approves_the_understanding(self):
        state = self.approved(" Plan ")
        self.assertTrue(chat.start_after_approval(state))
        self.assertEqual(review.NEXT, state["next_stage"])
        # A report that names no job is planned as a build; a later request is gathered again.
        self.assertEqual(("build", chat.STAGE), (state["workflow"]["kind"], state["workflow"]["then"]))
        self.assertTrue(state["workflow"]["requirements_approved"])
        pinned = self.approved(workflow={"kind": "review", "then": chat.STAGE})
        self.assertTrue(chat.start_after_approval(pinned))
        # #3010: every kind goes through the full pipeline; the pinned kind is labeling only.
        self.assertTrue(pinned["workflow"]["requirements_approved"])
        self.assertEqual("review", pinned["workflow"]["kind"])
        # Every chat turn begins at Requirements (#1860), so a follow-up's approval starts its job too.
        follow_up = self.approved(turns=[{"say": "earlier"}])
        self.assertTrue(chat.start_after_approval(follow_up))
        self.assertEqual(review.NEXT, follow_up["next_stage"])
        # #3010: an already-pinned job's approval is still processed (marks approved).
        already = self.approved(workflow={"kind": "build", "then": chat.STAGE})
        self.assertTrue(chat.start_after_approval(already))
        self.assertTrue(already["workflow"]["requirements_approved"])
        for state in (
            self.approved("Also add a total row", next_stage=chat.STAGE),
            self.approved(pending_questions=[{"id": "Q2"}]),
            self.approved(settings={}),
        ):
            with self.subTest(state=state):
                before = copy.deepcopy(state)
                self.assertFalse(chat.start_after_approval(state))
                self.assertEqual(before, state)
        legacy = self.approved()
        legacy.pop("workflow")
        self.assertFalse(chat.start_after_approval(legacy))

    def named(self, job, **change):
        """An approved chat whose Requirements report named ``job`` (#1846)."""
        state = self.approved(**change)
        report = {**state["requirements_handoff"]["report"], "job": job, "design_document": ""}
        state["requirements_handoff"] = {"report": report, "output": None}
        state["answers"] = {review.review_question(state)["id"]: {"text": "plan"}}
        return state

    def test_an_approval_starts_the_job_requirements_named(self):
        for job, first in (
            ("build", review.NEXT),
            ("bugfix", review.NEXT),
            ("review", review.NEXT),
            ("design", review.NEXT),
        ):
            with self.subTest(job=job):
                state = self.named(job)
                self.assertTrue(chat.start_after_approval(state))
                self.assertEqual(first, state["next_stage"])
                self.assertEqual(
                    (job, "model", "requirements_gather"),
                    tuple(state["workflow"].get(key) for key in ("kind", "source", "named_by")),
                )
                self.assertIn("Requirements understood: CSV export", workflows.approval_note(state))
        # "none" (still chatting) or an unknown value names no job: it is planned as a build.
        for job in ("none", "deploy"):
            with self.subTest(job=job):
                state = self.named(job)
                self.assertTrue(chat.start_after_approval(state))
                self.assertEqual(("build", review.NEXT), (state["workflow"]["kind"], state["next_stage"]))
                self.assertEqual("Requirements named no job; planned as a build", state["workflow"]["reason"])

    def test_standard_bugfix_confirmation_starts_planner_for_named_and_pinned_jobs(self):
        for pinned in (False, True):
            with self.subTest(pinned=pinned):
                state = self.named("bugfix")
                state["settings"]["implementation_flow"] = "standard"
                if pinned:
                    state["workflow"]["kind"] = "bugfix"
                    state["workflow"]["source"] = "user"
                self.assertNotIn("investigate", review.review_question(state)["question"])
                chat.start_after_approval(state)
                self.assertEqual("astra_discovery", state["next_stage"])

    @staticmethod
    def discussion(**report):
        """A handoff that names a discussion: a question with nothing to write, unless ``report`` says so."""
        return {
            "task": "Why is the metadata cache in-process?",
            "settings": {"spec_first": True},
            "workflow": {"kind": None, "then": "astra_discovery"},
            "next_stage": "astra_discovery",
            "status": "RUNNING",
            "pending_questions": [],
            "user_events": [{"id": "feedback-1"}],
            "requirements_handoff": {
                "report": {
                    "job": "discuss",
                    "design_document": "",
                    "summary": "The user wants to know why the cache is in-process.",
                    "intended_outcome": "An explanation of the cache choice",
                    "assistant_reply": "It is in-process because each worker owns its own copy (app/metadata.py).",
                    "required_behaviors": [],
                    "acceptance_tests": [],
                    "source_refs": ["task", "feedback-1", "app/metadata.py", "app/metadata.py", "https://x.test"],
                    "open_questions": [],
                    **report,
                },
                "output": "/run/requirements-gather-01.json",
            },
        }

    def test_requirements_answers_a_discussion_and_finishes_the_turn(self):
        state = self.discussion()
        self.assertTrue(chat.answer_now(state, {"output": "/run/requirements-gather-01.json"}))
        self.assertEqual(("TASK_COMPLETE", "COMPLETE", None), (state["status"], state["phase"], state["next_stage"]))
        self.assertEqual(("discuss", "requirements_gather"), (state["workflow"]["kind"], state["workflow"]["named_by"]))
        answer = state["answer"]
        self.assertEqual("It is in-process because each worker owns its own copy (app/metadata.py).", answer["answer"])
        self.assertEqual("requirements_gather", answer["answered_by"])
        # The person's own messages and web links are not where the answer was read from; repeats count once.
        self.assertEqual(["app/metadata.py"], [row["source"] for row in answer["evidence"]])
        self.assertEqual(("", []), (answer["note_path"], answer["questions"]))
        self.assertIn("It is in-process because", history.response(state))
        # Without a chat, the summary carries the answer.
        plain = self.discussion(assistant_reply="")
        self.assertTrue(chat.answer_now(plain, {"output": None}))
        self.assertEqual("The user wants to know why the cache is in-process.", plain["answer"]["answer"])
        # A pinned discussion is answered the same way and keeps its pin.
        pinned = self.discussion()
        pinned["workflow"].update(kind="discuss", source="user")
        self.assertTrue(chat.answer_now(pinned, {"output": None}))
        self.assertEqual(("user", "TASK_COMPLETE"), (pinned["workflow"]["source"], pinned["status"]))

    def test_a_discussion_that_writes_a_file_or_waits_on_a_question_is_not_answered_at_once(self):
        cases = {
            "writes a note": self.discussion(required_behaviors=["Write docs/notes/caches.json"]),
            "tests a note": self.discussion(acceptance_tests=["docs/notes/caches.json parses"]),
            "open question": self.discussion(open_questions=[{"id": "Q1", "question": "Which host?"}]),
            "a build": self.discussion(job="build"),
            "no job yet": self.discussion(job="none"),
        }
        recognized = self.discussion()
        recognized["workflow"]["kind"] = "build"
        cases["already a build"] = recognized
        cases["no workflow record"] = {**self.discussion(), "workflow": None}
        cases["no workflow record"].pop("workflow")
        for name, state in cases.items():
            with self.subTest(name):
                before = copy.deepcopy(state)
                self.assertFalse(chat.answer_now(state, {"output": None}))
                self.assertEqual(before, state)
        answered = self.discussion(open_questions=[{"id": "Q1", "question": "Which host?"}])
        answered["answers"] = {"Q1": {"text": "One host"}}
        self.assertTrue(chat.answer_now(answered, {"output": None}))

    def test_only_jobs_that_build_code_need_behaviors_and_acceptance_tests(self):
        self.assertEqual(
            [True, True, False, False, False, True],
            [review.builds_code(job) for job in ("build", "bugfix", "review", "design", "discuss", None)],
        )
        state = {"workflow": {"kind": None}, "requirements_handoff": {"report": {"job": "discuss"}}}
        self.assertEqual("discuss", review.job(state))
        self.assertEqual("build", review.job(state, {"job": "build"}))
        self.assertIsNone(review.job(state, {"job": "deploy"}))
        self.assertEqual("review", review.job({**state, "workflow": {"kind": "review"}}), "A known kind wins")

    def test_an_approved_build_is_planned_and_a_later_request_is_gathered_again(self):
        state = self.approved()
        self.assertTrue(chat.start_after_approval(state))
        self.assertEqual(review.NEXT, state["next_stage"], "Requirements already ran and was approved")
        self.assertFalse(state["workflow"].get("requirements_skipped"))
        # A follow-up starts from the saved pipeline entry (autocode_follow_up reads workflow.then).
        self.assertEqual(chat.STAGE, state["workflow"]["then"])
        state["settings"]["roles"] = {"requirements": {}}
        workflows.begin_requirements(state)
        self.assertEqual(chat.STAGE, state["next_stage"])

    def test_an_approval_starts_a_job_only_once(self):
        pinned = self.approved(workflow={"kind": "design", "then": chat.STAGE}, stages=[{"stage": chat.STAGE}])
        self.assertTrue(chat.start_after_approval(pinned))
        # #3010: every kind goes through the full pipeline after Requirements.
        self.assertTrue(pinned["workflow"]["requirements_approved"])
        for kind in ("design", "bugfix", "review"):
            with self.subTest(kind=kind):
                later = self.approved(
                    workflow={"kind": kind, "then": chat.STAGE, "requirements_approved": True},
                    stages=[{"stage": chat.STAGE}, {"stage": "astra_discovery"}],
                )
                before = copy.deepcopy(later)
                self.assertFalse(chat.start_after_approval(later), "already approved")
                self.assertEqual(before, later)

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
            # #1822: the prompt's source_refs rule must match the runner's complete-handoff check.
            instruction = prompt.split("\nCURRENT HANDOFF DATA\n", 1)[0]
            self.assertIn("clear, correctly spelled English", instruction)
            self.assertIn("instead of quoting their typos or shorthand", instruction)
            self.assertIn("source_quote evidence verbatim", instruction)
            self.assertIn("at least one existing workspace file", instruction)
            self.assertIn("Never cite .autocode/", instruction)
            self.assertIn("source_refs may be empty only when the inventory lists no file", instruction)

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

    def test_the_latest_assistant_reply_replaces_an_earlier_shorter_one(self):
        """Codex writes whole agent_message items; a later one is the refined reply."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = root / "requirements.jsonl"
            state = {"settings": {"spec_first": True}, "active_stage": {"stage": chat.STAGE, "events": str(events)}}
            first = {
                "type": "item.completed",
                "item": {"id": "item_0", "type": "agent_message", "text": '{"assistant_reply":"I’ll check…"}'},
            }
            second = {
                "type": "item.completed",
                "item": {
                    "id": "item_1",
                    "type": "agent_message",
                    "text": '{"assistant_reply":"What should this build?","acceptance_tests":[]}',
                },
            }
            events.write_text(json.dumps(first) + "\n")
            self.assertEqual("I’ll check…", chat.streaming_reply(state, root))
            with events.open("a") as target:
                target.write(json.dumps(second) + "\n")
            self.assertEqual("What should this build?", chat.streaming_reply(state, root))

    def test_a_growing_item_update_streams_its_decoded_reply(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = root / "requirements.jsonl"
            state = {"settings": {"spec_first": True}, "active_stage": {"stage": chat.STAGE, "events": str(events)}}
            for text, expected in (
                ('{"assistant_reply":"Hel', "Hel"),
                ('{"assistant_reply":"Hello the', "Hello the"),
                ('{"assistant_reply":"Hello there","acceptance_tests":[]}', "Hello there"),
            ):
                events.write_text(
                    json.dumps(
                        {"type": "item.updated", "item": {"id": "item_0", "type": "agent_message", "text": text}}
                    )
                    + "\n"
                )
                self.assertEqual(expected, chat.streaming_reply(state, root))


if __name__ == "__main__":
    unittest.main()
