"""Conversation source, revision and saved-action policy without a controller."""

import copy
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import autocode_conversation as protocol
import autocode_conversation_ingress as ingress
import autocode_conversation_policy as policy
import autocode_conversation_projection as projection
import autocode_conversation_session as session


def journal_at(run_dir):
    handoff = protocol.handoff_from_document(
        {
            "id": "a" * 32,
            "title": "Greeting",
            "created_at": "now",
            "messages": [
                {
                    "id": "first",
                    "role": "user",
                    "speaker": "User",
                    "text": "Build a greeting.",
                    "created_at": "now",
                    "status": "saved",
                    "client_request_id": "start",
                    "logical_turn_id": "first",
                    "in_reply_to": None,
                }
            ],
            "configured_routes": {
                "requirements_gatherer": {
                    "engine": "codex",
                    "provider": None,
                    "model": "declared-only",
                    "reasoning_effort": "low",
                }
            },
        }
    )
    protocol.ingest_handoff(run_dir, handoff)
    return policy.initialize(run_dir)


def pointer(journal):
    return {"conversation_id": journal["conversation_id"], "digest": journal["handoff_digest"]}


class ConversationPolicyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.run_dir = Path(self.directory.name) / "run"
        self.journal = journal_at(self.run_dir)

    def queue(self, request="message", text="Use Python.", expected=1):
        return policy.queue(self.run_dir, text, request, expected)

    def test_core_ingress_requires_one_human_seed_before_journal_creation_but_legacy_context_survives(self):
        workspace = Path(self.directory.name) / "project"
        workspace.mkdir()
        seed = copy.deepcopy(self.journal["conversation"])
        human = seed["messages"][0]
        second = {**human, "id": "second", "logical_turn_id": "second", "client_request_id": "second"}
        assistant = {**second, "role": "assistant", "speaker": "Requirements", "in_reply_to": human["logical_turn_id"]}
        route = seed["configured_routes"]["requirements_gatherer"]
        dispatch = protocol.new_dispatch(
            logical_turn_id=human["logical_turn_id"], client_request_id=human["client_request_id"], route=route
        )
        plan = {
            "id": "plan",
            "revision": 1,
            "created_at": "now",
            "status": "current",
            "goal": "Greeting",
            "freshness": {"state": "fresh", "updated_at": "now"},
        }
        variants = {
            "two-human-turns": {"messages": [human, second]},
            "assistant-context": {"messages": [human, assistant]},
            "unsaved-human": {"messages": [{**human, "status": "draft"}]},
            "unidentified-human-request": {"messages": [{**human, "client_request_id": None}]},
            "attributed-human": {"messages": [{**human, "execution": {"role": "requirements", "model": "old"}}]},
            "draft-context": {
                "drafts": [{"id": "draft", "tab_id": "main", "text": "Old draft", "revision": 1, "updated_at": "now"}]
            },
            "requirements-context": {"requirements": {"revisions": [{"id": "R1"}], "provenance": []}},
            "accepted-plan": {"plan_drafts": [plan]},
            "gatherer-dispatch": {"_dispatches": {human["logical_turn_id"]: dispatch}},
            "planner-dispatch": {
                "_planner_dispatches": {
                    human["logical_turn_id"]: {**dispatch, "role": "planner", "requirements_revision": 1}
                }
            },
        }
        for label, changes in variants.items():
            with self.subTest(label=label):
                handoff = protocol.handoff_from_document({**seed, **changes})
                source = protocol.stage_handoff(workspace, handoff)
                run_dir = Path(self.directory.name) / label
                state = {}
                with self.assertRaisesRegex(protocol.ConversationProtocolError, "exactly one saved human seed"):
                    ingress.ingest(state, run_dir, workspace, source, core_session=True)
                self.assertEqual({}, state)
                self.assertFalse(protocol.journal_file(run_dir).exists())
                self.assertTrue(ingress.ingest(state, run_dir, workspace, source))
                legacy = protocol.read_journal(run_dir)
                self.assertEqual(handoff["digest"], legacy["handoff_digest"])
                self.assertNotIn("session", legacy)
        valid = protocol.handoff_from_document(seed)
        run_dir = Path(self.directory.name) / "fresh-core"
        self.assertTrue(
            ingress.ingest({}, run_dir, workspace, protocol.stage_handoff(workspace, valid), core_session=True)
        )
        self.assertEqual(1, policy.session(protocol.read_journal(run_dir))["revision"])

    def test_message_retry_is_identical_but_changed_content_and_stale_revision_refuse(self):
        queued = self.queue()
        self.assertEqual(2, policy.session(queued)["revision"])
        self.assertEqual(queued, self.queue())
        for request, text in (("message", "Use Rust."), ("another", "Use Python.")):
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.queue(request, text)
        self.assertEqual(queued, protocol.read_journal(self.run_dir))
        self.assertEqual(self.journal["handoff_digest"], queued["handoff_digest"])
        self.assertEqual(["user", "user"], [row["role"] for row in queued["conversation"]["messages"]])

    def test_no_pending_refresh_is_a_noop_without_a_sealed_action(self):
        self.assertEqual({"noop": True}, policy.prepare_refresh(self.run_dir, "refresh", 1))
        self.assertFalse(policy.request_path(self.run_dir, "refresh").exists())
        self.assertEqual(self.journal, protocol.read_journal(self.run_dir))

    def test_pending_batch_bound_refuses_without_write_and_releases_space_after_saved_ack(self):
        first = self.queue("first", "a" * 25_000)
        for text in ("b" * 25_000, "b" * 14_999):
            with self.subTest(length=len(text)), self.assertRaisesRegex(ValueError, "text"):
                self.queue("second", text, 2)
            self.assertEqual(first, protocol.read_journal(self.run_dir))
        accepted = self.queue("second", "b" * 14_998, 2)
        self.assertEqual(accepted, self.queue("second", "b" * 14_998, 2))
        action = policy.prepare_refresh(self.run_dir, "batch", 3)
        self.assertEqual(40_000, len(action["text"]))
        self.assertEqual("a" * 25_000 + "\n\n" + "b" * 14_998, action["text"])
        state = {"conversation_handoff": pointer(accepted), "user_events": []}
        candidate = copy.deepcopy(state)
        candidate["user_events"].append({"kind": "brief_feedback", "id": "saved-batch", "text": action["text"]})
        policy.acknowledge(state, candidate, action)
        policy.reconcile(self.run_dir, policy.acknowledgements(candidate, accepted["conversation_id"]))
        later = self.queue("later", "c" * 25_000, 3)
        answer = policy.prepare_refresh(self.run_dir, "later-answer", 4, question_id="Q1", resolver_token="current")
        self.assertEqual("c" * 25_000, answer["text"])
        policy.reject(self.run_dir, answer, ValueError("Wrong offered answer"))
        rejected = protocol.read_journal(self.run_dir)
        corrected = policy.queue(self.run_dir, "d" * 25_000, "corrected", 4, supersedes_request_id="later")
        self.assertEqual(rejected["session"]["rejections"], corrected["session"]["rejections"])
        self.assertEqual(
            later["session"]["messages"]["later"]["text"], corrected["session"]["messages"]["later"]["text"]
        )
        replacement = policy.prepare_refresh(
            self.run_dir, "corrected-answer", 5, question_id="Q1", resolver_token="fresh"
        )
        self.assertEqual("d" * 25_000, replacement["text"])

    def test_exact_batch_source_and_acknowledgement_close_the_save_gap(self):
        queued = self.queue()
        self.queue("second", "Keep it small.", 2)
        sealed = policy.prepare_refresh(self.run_dir, "refresh", 3)
        args = SimpleNamespace(
            conversation_request=policy.request_path(self.run_dir, "refresh"),
            feedback="Use Python.\n\nKeep it small.",
            answer=[],
        )
        before = {"conversation_handoff": pointer(queued), "user_events": []}
        action = policy.load_action(before, self.run_dir, args)
        self.assertEqual(sealed["message_ids"], action["message_ids"])
        candidate = copy.deepcopy(before)
        candidate["user_events"].append({"kind": "brief_feedback", "id": "feedback-real", "text": args.feedback})
        policy.acknowledge(before, candidate, action)
        acknowledged = policy.load_action(candidate, self.run_dir, args)
        self.assertTrue(acknowledged["acknowledged"])
        projected = projection.project(candidate, protocol.read_journal(self.run_dir))
        self.assertEqual([], projected["pending_message_ids"])
        self.assertEqual(3, projected["applied_revision"])
        policy.reconcile(self.run_dir, projected["actions"])
        retained = policy.session(protocol.read_journal(self.run_dir))["refreshes"]["refresh"]
        self.assertEqual("applied", retained["delivery"])
        self.assertEqual("feedback-real", projected["actions"][0]["source_events"][0]["id"])

    def test_rejected_answer_can_be_corrected_but_unattempted_or_saved_messages_cannot(self):
        queued = self.queue()
        action = policy.prepare_refresh(self.run_dir, "wrong-answer", 2, question_id="Q1", resolver_token="token")
        with self.assertRaisesRegex(ValueError, "definitive runner rejection"):
            policy.queue(self.run_dir, "Correct answer", "correction", 2, supersedes_request_id="message")
        policy.reject(self.run_dir, action, ValueError("The answer was refused"))
        rejected = protocol.read_journal(self.run_dir)
        with self.assertRaisesRegex(ValueError, "unapplied"):
            policy.queue(self.run_dir, "Correct answer", "correction", 2, supersedes_request_id="message", active=True)
        corrected = policy.queue(self.run_dir, "Correct answer", "correction", 2, supersedes_request_id="message")
        self.assertEqual(
            rejected["session"]["refreshes"]["wrong-answer"], corrected["session"]["refreshes"]["wrong-answer"]
        )
        self.assertEqual(rejected["session"]["rejections"], corrected["session"]["rejections"])
        next_action = policy.prepare_refresh(
            self.run_dir, "correct-answer", 3, question_id="Q1", resolver_token="fresh"
        )
        self.assertEqual([corrected["session"]["messages"]["correction"]["message_id"]], next_action["message_ids"])
        self.assertEqual("Correct answer", next_action["text"])
        state = {"conversation_handoff": pointer(queued), "user_events": []}
        candidate = copy.deepcopy(state)
        candidate["user_events"].append({"kind": "answer", "question_id": "Q1", "text": "Correct answer"})
        policy.acknowledge(state, candidate, next_action)
        with self.assertRaisesRegex(ValueError, "unapplied"):
            policy.queue(
                self.run_dir,
                "Replay answer",
                "another",
                3,
                supersedes_request_id="correction",
                actions=policy.acknowledgements(candidate, queued["conversation_id"]),
            )
        projected = projection.project(candidate, protocol.read_journal(self.run_dir))
        self.assertEqual([], projected["pending_message_ids"])
        self.assertEqual("correction", projected["messages"][1]["superseded_by"])

    def test_saved_transaction_rechecks_revision_before_any_candidate_commit(self):
        queued = self.queue()
        policy.prepare_refresh(self.run_dir, "refresh", 2)
        state = {"conversation_handoff": pointer(queued), "user_events": []}
        args = SimpleNamespace(
            conversation_request=policy.request_path(self.run_dir, "refresh"), feedback="Use Python.", answer=[]
        )
        action = policy.load_action(state, self.run_dir, args)
        self.queue("concurrent-message", "A later correction", 2)
        with self.assertRaisesRegex(ValueError, "changed"):
            with policy.saved_action(state, self.run_dir, args, action):
                self.fail("A stale source must never reach candidate commit")
        self.assertEqual([], state["user_events"])

    def test_same_human_batch_cannot_be_saved_under_two_refresh_ids(self):
        queued = self.queue()
        first = policy.prepare_refresh(self.run_dir, "first", 2)
        policy.prepare_refresh(self.run_dir, "second", 2)
        state = {"conversation_handoff": pointer(queued), "user_events": []}
        candidate = copy.deepcopy(state)
        candidate["user_events"].append({"kind": "brief_feedback", "text": "Use Python."})
        policy.acknowledge(state, candidate, first)
        with self.assertRaisesRegex(ValueError, "already applied"):
            policy.load_action(
                candidate,
                self.run_dir,
                SimpleNamespace(
                    conversation_request=policy.request_path(self.run_dir, "second"), feedback="Use Python.", answer=[]
                ),
            )

    def test_sealed_action_cannot_change_human_text_or_authorize_approval(self):
        journal = self.queue()
        policy.prepare_refresh(self.run_dir, "refresh", 2)
        state = {"conversation_handoff": pointer(journal)}
        path = policy.request_path(self.run_dir, "refresh")
        for args in (
            SimpleNamespace(conversation_request=path, feedback="Use Rust.", answer=[]),
            SimpleNamespace(conversation_request=path, feedback="Use Python.", answer=[], approve_goal="token"),
        ):
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, "only its exact"):
                policy.load_action(state, self.run_dir, args)
        alien = {"conversation_handoff": {"conversation_id": "b" * 32, "digest": "alien"}}
        with self.assertRaisesRegex(ValueError, "does not belong"):
            policy.load_action(
                alien, self.run_dir, SimpleNamespace(conversation_request=path, feedback="Use Python.", answer=[])
            )

    def test_answer_requires_one_pending_message_and_exact_token(self):
        journal = self.queue()
        policy.prepare_refresh(self.run_dir, "answer", 2, question_id="Q1", resolver_token="current")
        path = policy.request_path(self.run_dir, "answer")
        state = {"conversation_handoff": pointer(journal)}
        good = SimpleNamespace(conversation_request=path, answer=["Q1=Use Python."], resolver_token="current")
        self.assertEqual("Q1", policy.load_action(state, self.run_dir, good)["question_id"])
        good.resolver_token = "stale"
        with self.assertRaises(ValueError):
            policy.load_action(state, self.run_dir, good)
        self.queue("other", "One more thing.", 2)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            policy.prepare_refresh(self.run_dir, "another-answer", 3, question_id="Q1", resolver_token="current")

    def test_owned_records_refuse_fifo_symlink_and_hardlink_without_opening_them(self):
        parent = self.run_dir / "records"
        parent.mkdir()
        fifo = parent / "pipe"
        os.mkfifo(fifo)
        with self.assertRaisesRegex(ValueError, "regular"):
            policy.read_record(fifo, parent)
        target = parent / "data"
        target.write_text("{}")
        link = parent / "alias"
        link.symlink_to(target)
        with self.assertRaises(ValueError):
            policy.read_record(link, parent)
        link.unlink()
        os.link(target, link)
        with self.assertRaisesRegex(ValueError, "regular"):
            policy.read_record(target, parent)

    def test_python_and_cli_creation_authority_has_no_action_or_controller_escape(self):
        self.assertEqual(
            ("--glm-model", "model", "--planning-v2"), policy.creation_options(("--glm-model=model", "--planning-v2"))
        )
        for flag in ("--auto-approve", "--run-dir", "--unit", "--answer", "--single-model", "--workflow"):
            with self.subTest(flag=flag), self.assertRaisesRegex(ValueError, "Unsupported"):
                policy.creation_options((flag, "value"))

    def test_legacy_journal_is_not_silently_upgraded_by_projection(self):
        legacy = copy.deepcopy(self.journal)
        legacy.pop("session")
        self.assertIsNone(projection.project({"conversation_handoff": pointer(legacy)}, legacy))
        self.assertNotIn("session", legacy)
        self.assertEqual("unavailable", projection.project({}, ["malformed"])["availability"])

    def test_current_review_needs_actual_saved_attribution_and_does_not_promote_assistant_text(self):
        state = {
            "conversation_handoff": pointer(self.journal),
            "goal_contract": {"revision": 1, "hash": "hash", "body": {"objective": "Greet"}},
            "planning": {
                "final_token": "r1:hash",
                "reports": {
                    "plan": {"output": "plan.json", "report": {"summary": "Assistant suggestion"}},
                    "plan_review": {"output": "review.json", "report": {"summary": "Checked", "concerns": []}},
                },
            },
            "stages": [
                {
                    "stage": "plan",
                    "output": "plan.json",
                    "reasoning_effort": "high",
                    "launch_route": {"model": "planner", "engine": "codex"},
                },
                {
                    "stage": "plan_review",
                    "output": "review.json",
                    "reasoning_effort": "medium",
                    "launch_route": {"model": "reviewer", "engine": "codex"},
                },
            ],
        }
        original = copy.deepcopy(state)
        value = projection.project(
            state, self.journal, displayed_plan={"token": "r1:hash"}, needs={"kind": "approve_plan"}
        )
        self.assertTrue(value["handoff_ready"])
        self.assertEqual(["Planner", "Plan Reviewer"], [row["role"] for row in value["planning_records"]])
        self.assertEqual(["Build a greeting."], [row["text"] for row in value["messages"]])
        self.assertEqual(original, state)
        public_need = {
            "kind": "answer",
            "resolver_token": "public-token",
            "questions": [{"id": "Q1", "options": ["A"]}],
        }
        canonical = [{"id": "F1", "status": "resolved", "severity": "blocking", "finding": "Addressed"}]
        actionable = projection.project(
            state,
            self.journal,
            needs=public_need,
            public_status={"status": "WAITING_FOR_USER", "done": False},
            review_findings=canonical,
        )
        self.assertEqual(public_need, actionable["needs"])
        self.assertEqual("WAITING_FOR_USER", actionable["status"])
        self.assertEqual(canonical, actionable["review"]["findings"])
        actionable["needs"]["questions"].clear()
        actionable["review"]["findings"].clear()
        self.assertEqual(1, len(public_need["questions"]))
        self.assertEqual(1, len(canonical))
        missing = copy.deepcopy(state)
        missing["stages"].pop()
        self.assertFalse(
            projection.project(
                missing, self.journal, displayed_plan={"token": "r1:hash"}, needs={"kind": "approve_plan"}
            )["handoff_ready"]
        )
        queued = self.queue()
        self.assertFalse(
            projection.project(state, queued, displayed_plan={"token": "r1:hash"}, needs={"kind": "approve_plan"})[
                "handoff_ready"
            ]
        )


class ConversationRefreshTests(unittest.TestCase):
    def test_stale_question_token_is_refused_before_sealing_and_current_token_can_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            journal = journal_at(run_dir)
            policy.queue(run_dir, "Python", "message", 1)
            state = {"conversation_handoff": pointer(journal)}

            def status():
                return {
                    "needs": {"kind": "answer", "questions": [{"id": "Q1"}], "resolver_token": "current"},
                    "conversation": projection.project(state, protocol.read_journal(run_dir)),
                }

            run = SimpleNamespace(
                run_dir=run_dir,
                status=status,
                apply_conversation_request=lambda *a, **k: self.fail("Stale token must not invoke CLI"),
            )
            before = protocol.read_journal(run_dir)
            with self.assertRaisesRegex(ValueError, "stale"):
                session.refresh(run, request_id="answer", expected_revision=2, question_id="Q1", resolver_token="old")
            self.assertEqual(before, protocol.read_journal(run_dir))
            self.assertFalse(policy.request_path(run_dir, "answer").exists())
            sealed = policy.prepare_refresh(run_dir, "answer", 2, question_id="Q1", resolver_token="current")
            self.assertEqual("current", sealed["resolver_token"])

    def test_unknown_advancing_delivery_cannot_launch_again(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            journal = journal_at(run_dir)
            policy.queue(run_dir, "Use Python.", "message", 1)
            action = policy.prepare_refresh(run_dir, "refresh", 2)
            state = {"conversation_handoff": pointer(journal), "user_events": []}
            candidate = copy.deepcopy(state)
            candidate["user_events"].append({"kind": "brief_feedback", "id": "feedback", "text": action["text"]})
            policy.acknowledge(state, candidate, action)
            policy.reconcile(run_dir, policy.acknowledgements(candidate, journal["conversation_id"]))
            policy.mark(run_dir, "refresh", "advancing")

            def status():
                return {
                    "needs": {"kind": "continue"},
                    "conversation": projection.project(candidate, protocol.read_journal(run_dir)),
                }

            run = SimpleNamespace(
                run_dir=run_dir, status=status, advance=lambda: self.fail("Unknown paid delivery must not replay")
            )
            with self.assertRaisesRegex(ValueError, "unresolved"):
                session.refresh(run, request_id="refresh", expected_revision=2)
            self.assertEqual(
                "advancing", policy.session(protocol.read_journal(run_dir))["refreshes"]["refresh"]["delivery"]
            )

    def test_identical_finished_question_refresh_does_not_require_a_new_question_token(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            journal = journal_at(run_dir)
            policy.queue(run_dir, "Python", "message", 1)
            action = policy.prepare_refresh(run_dir, "answer", 2, question_id="Q1", resolver_token="old-token")
            state = {"conversation_handoff": pointer(journal), "user_events": []}
            candidate = copy.deepcopy(state)
            candidate["user_events"].append({"kind": "answer", "question_id": "Q1", "text": "Python"})
            policy.acknowledge(state, candidate, action)
            policy.reconcile(run_dir, policy.acknowledgements(candidate, journal["conversation_id"]))
            policy.mark(run_dir, "answer", "advancing")
            policy.mark(run_dir, "answer", "finished")

            def status():
                return {
                    "needs": {"kind": "approve_plan", "token": "new"},
                    "conversation": projection.project(candidate, protocol.read_journal(run_dir)),
                }

            run = SimpleNamespace(
                run_dir=run_dir, status=status, advance=lambda: self.fail("Finished refresh must not dispatch")
            )
            self.assertTrue(
                session.refresh(run, request_id="answer", expected_revision=2, question_id="Q1")["refresh_noop"]
            )
            with self.assertRaisesRegex(ValueError, "different answer token"):
                session.refresh(run, request_id="answer", expected_revision=2, question_id="Q1", resolver_token="new")
