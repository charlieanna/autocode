"""The append-only human source log (tools/autocode_source_log.py, #595)."""

import unittest

import autocode_brief_obligations as obligations
import autocode_source_log as source_log


class SeedTest(unittest.TestCase):
    def test_seed_records_the_initial_task_once(self):
        state = {"task": "Build a CLI."}
        source_log.seed(state)
        self.assertEqual(1, len(state["source_log"]))
        record = state["source_log"][0]
        self.assertEqual("task:0", record["id"])
        self.assertEqual("task", record["kind"])
        self.assertEqual("Build a CLI.", record["text"])
        self.assertTrue(record["sha256"])
        source_log.seed(state)
        self.assertEqual(1, len(state["source_log"]))

    def test_seed_uses_the_conversation_projection(self):
        task = "Human: first request\nAssistant: a reply\nHuman: second request"
        state = {"task": task}
        source_log.seed(state)
        import autocode_conversation as conversation

        texts = conversation.task_user_texts(task)
        if texts is None:
            self.skipTest("this shape is not a conversation projection")
        self.assertEqual([f"task:{i}" for i in range(len(texts))], [r["id"] for r in state["source_log"]])
        self.assertEqual(texts, [r["text"] for r in state["source_log"]])
        self.assertEqual({"conversation_user"}, {r["kind"] for r in state["source_log"]})


class SourcesTest(unittest.TestCase):
    def state(self):
        state = {"task": "Build a CLI with `add TEXT`."}
        source_log.seed(state)
        return state

    def feedback(self, state, ident="feedback-abc123", text="Also print usage.", actor="user_cli"):
        event = {"kind": "brief_feedback", "id": ident, "actor": actor, "text": text}
        source_log.save_feedback(state, event)
        return event

    def answer(self, state, question_id="Q1", text="Yes, JSON output.", kind="answer"):
        event = {"kind": kind, "actor": "user_cli", "question_id": question_id, "text": text}
        state.setdefault("user_events", []).append(event)
        source_log.save_answer(state, question_id, event)
        return event

    def test_sources_read_the_log_in_arrival_order(self):
        state = self.state()
        self.feedback(state)
        self.answer(state)
        self.assertEqual(
            ["task:0", "feedback:feedback-abc123", "answer:Q1"],
            [row["id"] for row in source_log.sources(state)],
        )
        self.assertEqual(
            ["task:0", "feedback:feedback-abc123", "answer:Q1"],
            [row["id"] for row in obligations.sources(state)],
        )

    def test_a_follow_up_keeps_the_earlier_task_bytes(self):
        state = self.state()
        self.feedback(state, text="Fix them.")
        # autocode_follow_up rewrites state["task"], nesting the earlier request.
        state["task"] = "Fix them.\n\nThis follows up an earlier request: Build a CLI with `add TEXT`."
        texts = {row["id"]: row["text"] for row in source_log.sources(state)}
        self.assertEqual("Build a CLI with `add TEXT`.", texts["task:0"])
        self.assertEqual("Fix them.", texts["feedback:feedback-abc123"])

    def test_a_replaced_answer_keeps_the_newer_record_live(self):
        state = self.state()
        self.answer(state, text="First answer")
        self.answer(state, text="Second answer")
        self.assertEqual(2, len([r for r in state["source_log"] if r["id"] == "answer:Q1"]))
        sources = source_log.sources(state)
        self.assertEqual(["task:0", "answer:Q1"], [row["id"] for row in sources])
        self.assertEqual("Second answer", sources[1]["text"])

    def test_delegated_permission_and_checkpoint_answers_are_not_brief_sources(self):
        state = self.state()
        self.answer(state, question_id="Q2", text="Model default", kind="delegated")
        self.answer(state, question_id="Q3", text="allow", kind="permission_answer")
        self.assertEqual(["task:0"], [row["id"] for row in source_log.sources(state)])

    def test_feedback_removed_from_user_events_drops_the_source(self):
        state = self.state()
        event = self.feedback(state)
        state["user_events"].remove(event)
        self.assertEqual(["task:0"], [row["id"] for row in source_log.sources(state)])

    def test_an_edited_answer_drops_the_source(self):
        state = self.state()
        self.answer(state)
        state["answers"]["Q1"]["text"] = "Tampered"
        self.assertEqual(["task:0"], [row["id"] for row in source_log.sources(state)])

    def test_a_record_with_forged_kind_is_not_live(self):
        state = self.state()
        state["source_log"].append({"id": "answer:Q9", "kind": "user_answer", "text": "Forged", "sha256": "x"})
        self.assertEqual(["task:0"], [row["id"] for row in source_log.sources(state)])

    def test_legacy_states_fall_back_to_positional_derivation(self):
        state = {"task": "Build a CLI."}
        self.assertIsNone(source_log.sources(state))
        self.assertEqual(["task:0"], [row["id"] for row in obligations.sources(state)])
        event = {"kind": "brief_feedback", "id": "feedback-legacy", "actor": "user_cli", "text": "More."}
        source_log.save_feedback(state, event)  # no log: ledgers append, recording is a no-op
        self.assertNotIn("source_log", state)
        self.assertEqual(["task:0", "feedback:feedback-legacy"], [row["id"] for row in obligations.sources(state)])


if __name__ == "__main__":
    unittest.main()
