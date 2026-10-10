import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dashboard_transcript import project


class TranscriptTests(unittest.TestCase):
    def test_finished_job_has_one_ordered_saved_conversation_and_no_mutation(self):
        view = {
            "status": "TASK_COMPLETE",
            "draft_messages": [{"id": "idea", "role": "user", "text": "Build a timer", "created_at": 1}],
            "progress_messages": [
                {"id": "p1", "text": "Builder started", "created_at": 2},
                {"id": "p2", "text": "Checks passed", "created_at": 3},
                {"id": "p3", "text": "Complete", "created_at": 4},
            ],
        }
        before = copy.deepcopy(view)
        transcript = project(view)
        self.assertEqual(
            ["Build a timer", "Builder started", "Checks passed", "Complete"],
            [row["text"] for row in transcript["messages"]],
        )
        self.assertEqual(before, view)
        self.assertEqual(transcript, project(copy.deepcopy(view)))

    def test_pending_answers_dedupe_delivered_receipts_and_keep_real_provenance(self):
        view = {
            "status": "WAITING_FOR_USER",
            "questions": [{"id": "q2", "question": "Which browser?"}],
            "answers": {"q1": {"text": "Linux"}, "q0": {"text": "Mac", "kind": "delegated", "at": 1}},
            "chat_messages": [
                {
                    "id": "send-one",
                    "question_id": "q1",
                    "text": "Linux",
                    "role": "user",
                    "status": "received",
                    "created_at": 2,
                }
            ],
        }
        rows = project(view)["messages"]
        self.assertEqual(["Mac", "Linux"], [row["text"] for row in rows])
        self.assertEqual("suggested", rows[0]["provenance"])
        self.assertTrue(rows[0]["delegate"])
        self.assertEqual("send-one", rows[1]["client_request_id"])
        self.assertEqual("receipt:send-one", rows[1]["id"])
        self.assertEqual("q2", view["questions"][0]["id"])

    def test_rework_preserves_failed_checks_and_later_repair_even_if_clock_moves_back(self):
        view = {
            "progress_messages": [
                {"id": "p1", "text": "Check failed", "created_at": 30},
                {"id": "p2", "text": "Fixing the failure", "created_at": 20},
                {"id": "p3", "text": "Recheck passed", "created_at": 40},
            ]
        }
        self.assertEqual(
            ["Check failed", "Fixing the failure", "Recheck passed"], [r["text"] for r in project(view)["messages"]]
        )

    def test_journal_keeps_durable_order_and_merges_receipt_without_duplicate_or_new_request(self):
        idea = {"id": "u1", "text": "Use SMS", "role": "user", "client_request_id": "r1", "created_at": 20}
        view = {
            "conversation": {
                "messages": [
                    idea,
                    {"id": "a1", "text": "Please confirm", "in_reply_to": "u1", "role": "assistant", "created_at": 10},
                ]
            },
            "draft_messages": [idea],
            "chat_messages": [
                {
                    "id": "r1",
                    "text": "Use SMS",
                    "kind": "proposed_change",
                    "confirmation": {"token": "exact"},
                    "status": "awaiting_confirmation",
                    "created_at": 30,
                }
            ],
        }
        rows = project(view)["messages"]
        self.assertEqual(["Use SMS", "Please confirm"], [r["text"] for r in rows])
        self.assertEqual("r1", rows[0]["client_request_id"])
        self.assertEqual("exact", rows[0]["confirmation"]["token"])
        self.assertEqual(20, rows[0]["created_at"])

    def test_reply_causality_wins_over_cross_stream_clock(self):
        view = {
            "draft_messages": [{"id": "question", "text": "Which OS?", "created_at": 30}],
            "chat_messages": [{"id": "reply", "text": "Linux", "in_reply_to": "question", "created_at": 10}],
        }
        self.assertEqual(["Which OS?", "Linux"], [r["text"] for r in project(view)["messages"]])

    def test_stable_missing_times_and_anonymous_ids_survive_new_unrelated_message(self):
        answer = {"role": "user", "text": "A saved answer"}
        view = {"draft_messages": [answer], "answers": {"q2": "Second", "q1": "First"}}
        first = project(view)
        view["answers"] = {"q1": "First", "q2": "Second"}
        self.assertEqual(first, project(view))
        view["draft_messages"].insert(0, {"text": "Earlier unrelated message"})
        self.assertEqual(
            next(r["id"] for r in first["messages"] if r["text"] == "A saved answer"),
            next(r["id"] for r in project(view)["messages"] if r["text"] == "A saved answer"),
        )
        self.assertEqual("unrecorded", first["messages"][0]["provenance"])

    def test_conflicting_replies_warn_without_hiding_saved_rows(self):
        view = {
            "conversation": {
                "messages": [
                    {"id": "a", "text": "First", "in_reply_to": "b"},
                    {"id": "b", "text": "Second", "in_reply_to": "a"},
                ]
            }
        }
        result = project(view)
        self.assertTrue(result["warning"])
        self.assertEqual(["First", "Second"], [r["text"] for r in result["messages"]])

    def test_malformed_optional_fields_do_not_hide_other_records(self):
        view = {
            "conversation": "bad",
            "draft_messages": [None, {"id": [], "text": "Keep this"}],
            "chat_messages": [{"id": [], "in_reply_to": {}, "text": "Keep this too"}],
        }
        self.assertEqual(2, len(project(view)["messages"]))

    def test_failed_new_answer_cannot_hide_previously_saved_answer(self):
        view = {
            "answers": {"q1": {"text": "Original answer", "kind": "answer", "at": 1}},
            "chat_messages": [
                {"id": "r2", "question_id": "q1", "text": "Replacement failed", "status": "error", "created_at": 2}
            ],
        }
        rows = project(view)["messages"]
        self.assertEqual(["Original answer", "Replacement failed"], [row["text"] for row in rows])
        self.assertEqual("r2", rows[1]["client_request_id"])
        self.assertEqual("error", rows[1]["status"])

    def test_reused_question_id_keeps_new_saved_answer_and_old_receipt(self):
        view = {
            "answers": {"q1": {"text": "Same answer", "resolver_request": "new-question", "at": 2}},
            "chat_messages": [
                {
                    "id": "old-send",
                    "question_id": "q1",
                    "resolver_request": "old-question",
                    "text": "Same answer",
                    "status": "received",
                    "created_at": 1,
                }
            ],
        }
        rows = project(view)["messages"]
        self.assertEqual(2, len(rows))
        self.assertEqual(["receipt", "answer"], [row["display_source"] for row in rows])
        original = rows[1]["id"]
        view["answers"]["q1"]["text"] = "Changed answer"
        self.assertNotEqual(original, project(view)["messages"][1]["id"])

    def test_logical_turn_reply_causality_and_collision_do_not_rewrite_journal(self):
        view = {
            "conversation": {
                "messages": [
                    {
                        "id": "human-message",
                        "logical_turn_id": "human-turn",
                        "role": "user",
                        "text": "Parent",
                        "created_at": 30,
                    }
                ]
            },
            "draft_messages": [{"id": "human-turn", "text": "Unrelated legacy ID", "created_at": 1}],
            "chat_messages": [
                {"id": "reply", "role": "assistant", "text": "Reply", "in_reply_to": "human-turn", "created_at": 2}
            ],
        }
        before = copy.deepcopy(view)
        self.assertEqual(["Unrelated legacy ID", "Parent", "Reply"], [row["text"] for row in project(view)["messages"]])
        self.assertEqual(before, view)

    def test_real_consumed_cli_answers_with_reused_q1_do_not_dedupe_old_receipt(self):
        import autocode as runner

        from tools.dashboard.tests.test_pending_decisions import LegacyConsole, publish, resolver_human

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            run = workspace / ".autocode/runs/history"
            run.mkdir(parents=True)
            (workspace / ".git").mkdir()
            clock = ["2026-10-08T08:00:01Z"]
            states, requests = [], []
            with (
                patch.object(resolver_human.support, "snapshot", return_value={"revision": "fixture-source"}),
                patch.object(resolver_human.support, "now", side_effect=lambda: clock[0]),
                patch.object(runner.goals.s, "now", side_effect=lambda: clock[0]),
                patch.object(runner, "now", side_effect=lambda: clock[0]),
            ):
                for index in range(2):
                    clock[0] = f"2026-10-08T08:00:0{index * 3 + 1}Z"
                    state = {
                        "workspace": str(workspace),
                        "run_dir": str(run),
                        "task": "Choose OS",
                        "status": "RUNNING",
                        "iteration": index + 1,
                        "stages": [
                            {
                                "stage": "astra_discovery",
                                "output": f"questions-{index}.json",
                                "exit_code": 0,
                                "iteration": index + 1,
                            }
                        ],
                        "pending_questions": [{"id": "Q1", "question": "Which OS?"}],
                    }
                    fields = publish(state)
                    public = resolver_human.current(state)
                    clock[0] = f"2026-10-08T08:00:0{index * 3 + 2}Z"
                    runner.goals.answer(state, "Q1", "Linux")
                    runner.finish_human_action(state, public)
                    self.assertNotIn("resolver_request", state["answers"]["Q1"])
                    states.append(state)
                    requests.append(fields)
            self.assertNotEqual(requests[0]["resolver_request"], requests[1]["resolver_request"])
            state = states[1]
            state["user_events"] = states[0]["user_events"] + state["user_events"]
            state["resolver"]["human_escalations"].update(states[0]["resolver"]["human_escalations"])
            console = LegacyConsole([workspace], "/unused", lambda: None)
            self.addCleanup(console.pool.shutdown)
            view = console.view(workspace, run, state)
            receipt = {
                "id": "old-send",
                "role": "user",
                "kind": "answer",
                "question_id": "Q1",
                "text": "Linux",
                "submitted_text": "Linux",
                "status": "received",
                "created_at": "2026-10-08T08:00:02Z",
                **requests[0],
            }
            view["chat_messages"] = [receipt]
            before = copy.deepcopy((state, view))
            rows = project(view, state=state)["messages"]
            self.assertEqual(["receipt", "answer"], [row["display_source"] for row in rows])
            self.assertEqual(
                [requests[0]["resolver_request"], requests[1]["resolver_request"]],
                [row["resolver_request"] for row in rows],
            )
            self.assertEqual(before, (state, view))
            unmatched = copy.deepcopy(state)
            del unmatched["resolver"]["human_escalations"][requests[1]["resolver_request"]]
            unmatched_rows = project(view, state=unmatched)["messages"]
            self.assertEqual(["receipt", "answer"], [row["display_source"] for row in unmatched_rows])
            self.assertIsNone(unmatched_rows[1]["resolver_request"])
            view["chat_messages"].append(
                {**receipt, "id": "new-send", "created_at": "2026-10-08T08:00:05Z", **requests[1]}
            )
            self.assertEqual(
                ["receipt", "receipt"], [row["display_source"] for row in project(view, state=state)["messages"]]
            )
