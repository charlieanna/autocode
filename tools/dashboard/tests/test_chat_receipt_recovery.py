"""Task chat crash windows against durable journals, without providers or waits."""

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(TOOLS / "dashboard"), str(TOOLS)]
import autocode_conversation as protocol
from dashboard_chat import ConversationMixin


class ProcessLost(BaseException):
    pass


class TaskInterface:
    def __init__(self, workspace, run, evidence):
        self.workspace, self.run, self.evidence = workspace, run, evidence
        self.submissions = 0

    def workspace_for(self, raw):
        return self.workspace

    def run_for(self, workspace, raw):
        return self.run

    def action_log(self, *args):
        return []

    def view(self, workspace, run, state=None):
        return {"goal_token": "r1:prior", "interventions": {"entries": deepcopy(self.evidence)}}

    def intervene(self, workspace, run, kind, text, ident):
        self.submissions += 1
        self.evidence.append(
            {
                "id": ident,
                "kind": kind,
                "text": text,
                "durable": True,
                "status": "queued",
                "observed_goal_token": "r1:prior",
            }
        )
        raise ProcessLost()


class Chat(ConversationMixin, TaskInterface):
    pass


class ChatReceiptRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "project"
        self.run = self.workspace / ".autocode/runs/task"
        self.run.mkdir(parents=True)
        self.state = {"workspace": str(self.workspace)}
        self.save_state()
        doc = {
            "id": "a" * 32,
            "title": "Saved task",
            "created_at": protocol.now(),
            "models": {"glm_model": "openai/gpt-6-sol"},
            "messages": [
                {
                    "id": "initial",
                    "role": "user",
                    "speaker": "You",
                    "text": "Build a CLI",
                    "status": "received",
                    "created_at": protocol.now(),
                }
            ],
        }
        protocol.ingest_handoff(self.run, protocol.handoff_from_document(doc))
        self.evidence = []

    def save_state(self):
        (self.run / "state.json").write_text(json.dumps(self.state))

    def open(self):
        return Chat(self.workspace, self.run, self.evidence, conversation_root=self.root / "conversations")

    def request(self):
        return {
            "workspace": str(self.workspace),
            "run": str(self.run),
            "request_id": "stable-request",
            "text": "Add JSON output",
        }

    def messages(self):
        return protocol.read_journal(self.run)["conversation"]["messages"]

    def saved_row(self, **changes):
        return {
            "id": "stable-request",
            "role": "user",
            "speaker": "You",
            "text": "Add JSON output",
            "submitted_text": "Add JSON output",
            "question_id": None,
            "delegate": False,
            "status": "saved",
            "error": None,
            "prior_goal_token": "r1:prior",
            **changes,
        }

    def test_crash_after_durable_acceptance_repairs_on_restart_without_resubmit(self):
        first = self.open()
        proposal = first.chat(self.request())
        with self.assertRaises(ProcessLost):
            first.chat({**self.request(), "decision": "confirm", "decision_token": proposal["confirmation"]["token"]})
        self.assertEqual(1, first.submissions)
        self.assertEqual("r1:prior", first._chat_rows(self.run)[0]["prior_goal_token"])
        self.assertEqual(1, len(self.messages()))
        restored = self.open()
        # Polling an attached task needs no provider store initialization.
        view = restored.view(self.workspace, self.run)
        self.assertIsNone(restored._conversation_store)
        self.assertEqual("received", view["chat_messages"][0]["status"])
        self.assertTrue(view["conversation"]["plan_gate"]["pending_product_change"])
        self.assertEqual("r1:prior", view["conversation"]["pending_product_change"]["prior_goal_token"])
        self.assertEqual("received", restored.chat({**self.request(), "retry": True})["status"])
        self.assertEqual(0, restored.submissions)
        self.assertEqual(2, len(self.messages()))

    def test_same_request_reconciles_applied_receipt_before_return(self):
        chat = self.open()
        chat._save_chat(self.run, self.saved_row())
        self.evidence.append(
            {
                "id": "stable-request",
                "kind": "feedback",
                "text": "Add JSON output",
                "durable": True,
                "status": "applied",
            }
        )
        response = chat.chat(self.request())
        self.assertEqual("applied", response["status"])
        self.assertEqual(0, chat.submissions)
        self.assertEqual(2, len(self.messages()))

    def test_mismatched_or_legacy_evidence_does_not_repair_or_resubmit(self):
        chat = self.open()
        chat._save_chat(self.run, self.saved_row())
        for patch in (
            {"text": "Different payload"},
            {"kind": "pause"},
            {"durable": False},
            {"legacy": True},
            {"status": "uncertain"},
            {"id": "different-request"},
        ):
            with self.subTest(patch=patch):
                self.evidence[:] = [
                    {
                        "id": "stable-request",
                        "kind": "feedback",
                        "text": "Add JSON output",
                        "durable": True,
                        "status": "queued",
                        **patch,
                    }
                ]
                response = chat.chat({**self.request(), "retry": True})
                self.assertEqual("saved", response["status"])
                self.assertEqual(1, len(self.messages()))
        self.assertEqual(0, chat.submissions)

    def test_matching_answer_recovers_journal_and_receipt_without_continue(self):
        chat = self.open()
        chat._save_chat(self.run, self.saved_row(question_id="q1", action_id="lost-action"))
        self.state["answers"] = {"q1": {"question_id": "q1", "text": "Add JSON output", "kind": "answer"}}
        self.save_state()
        result = self.open().view(self.workspace, self.run)
        self.assertEqual("received", result["chat_messages"][0]["status"])
        self.assertEqual("received", chat._chat_rows(self.run)[0]["status"])
        self.assertEqual(2, len(self.messages()))
        self.assertNotIn("pending_product_change", result["conversation"])

    def test_delegated_answer_requires_saved_default_and_delegated_evidence(self):
        chat = self.open()
        chat._save_chat(
            self.run,
            self.saved_row(
                question_id="q1",
                delegate=True,
                submitted_text="",
                delegated_default="JSON",
                text="Use the suggested default: JSON",
            ),
        )
        self.state["answers"] = {"q1": {"text": "JSON", "kind": "answer"}}
        self.save_state()
        chat.view(self.workspace, self.run)
        self.assertEqual(1, len(self.messages()))
        self.state["answers"]["q1"]["kind"] = "delegated"
        self.save_state()
        self.assertEqual("received", chat.view(self.workspace, self.run)["chat_messages"][0]["status"])
        self.assertEqual("Use the suggested default: JSON", self.messages()[-1]["text"])

    def test_unmatched_answer_does_not_enter_journal(self):
        chat = self.open()
        chat._save_chat(self.run, self.saved_row(question_id="q1"))
        for answer in ({"text": "Different"}, {"text": "Add JSON output", "question_id": "q2"}):
            self.state["answers"] = {"q1": answer}
            self.save_state()
            chat.view(self.workspace, self.run)
            self.assertEqual(1, len(self.messages()))


if __name__ == "__main__":
    unittest.main()
