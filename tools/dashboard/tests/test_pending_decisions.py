"""Dashboard readers and responses require durable, current resolver receipts."""

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_console import LegacyConsole, pending_decisions, resolver_human
from dashboard_chat import ConversationMixin
from dashboard_project_controls import compact_run


def publish(state, scope="clarification", questions=None, request=None):
    """Test setup only: issue real receipts, never stamp a public issuer flag."""
    questions = copy.deepcopy(questions if questions is not None else state.get("pending_questions", []))
    contract = state.setdefault("goal_contract", {})
    contract.update(
        task_id="fixture-task",
        revision=contract.get("revision", 1),
        approval_status="draft" if scope in ("goal_approval", "clarification") else "approved",
    )
    body = contract.setdefault("body", {})
    body["open_blocking_questions"] = questions if scope == "clarification" else []
    body.setdefault("acceptance_criteria", [{"id": "C1", "criterion": "Inspect output", "human_review": True}])
    contract["hash"] = resolver_human.support.digest({key: contract[key] for key in ("task_id", "revision", "body")})
    state["displayed_goal"] = f"r{contract['revision']}:{contract['hash']}"
    evidence, origin = {}, {"stage": "astra_discovery"}
    if scope == "human_review":
        state["displayed_review"] = "artifact-token"
        state["validation"] = {"verdict": "PASS", "source_revision": "fixture-source", "evidence_hashes": {}}
        evidence = {"review_token": state["displayed_review"]}
        request = request or {"kind": scope, "criteria": ["C1"], "decision_needed": "Review output"}
    if scope in ("operational_exhaustion", "blocker"):
        origin = {"stage": "astra_resolve"}
        state["stages"] = [{"stage": "astra_resolve", "output": "diagnosis.json", "exit_code": 0}]
        evidence = {"diagnosis": "No safe recovery remains", "output": "diagnosis.json"}
        request = request or {
            "kind": scope,
            "decision_needed": "Provide corrective information",
            "impact": "Execution remains paused",
        }
    if scope in ("permission", "goal_change"):
        request = request or {
            "kind": scope,
            "decision_needed": "Allow scoped change?",
            "impact": "Changes authorized scope",
        }
    resolver_human.queue(
        state,
        scope,
        origin,
        request=request,
        questions=questions,
        evidence=evidence,
        status="AWAITING_GOAL_APPROVAL" if scope == "goal_approval" else "WAITING_FOR_USER",
    )
    assert resolver_human.evaluate(state) == "escalate"
    public = resolver_human.current(state)
    assert public is not None
    return {"resolver_request": public["request_id"], "resolver_token": public["request_token"]}


class ChatConsole(ConversationMixin, LegacyConsole):
    pass


class PendingDecisionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        (self.workspace / ".git").mkdir()
        self.run = self.workspace / ".autocode/runs/test"
        self.run.mkdir(parents=True)
        snapshot = patch.object(resolver_human.support, "snapshot", return_value={"revision": "fixture-source"})
        snapshot.start()
        self.addCleanup(snapshot.stop)
        self.question = {"id": "Q1", "question": "Which output format?", "proposed_default": "JSON"}
        self.state = {
            "workspace": str(self.workspace),
            "run_dir": str(self.run),
            "task": "Fixture",
            "status": "RUNNING",
            "pending_questions": [self.question],
        }
        self.console = ChatConsole([self.workspace], "/unused-runner", lambda: None)
        self.addCleanup(self.console.pool.shutdown)
        self.console.enqueue = Mock(return_value={"id": "queued"})
        self.console.continue_run = Mock(return_value={"id": "continued"})
        self.console._conversation_store = SimpleNamespace(
            root=self.workspace / "dashboard/conversations", list=lambda **kwargs: []
        )
        self.base = {"workspace": str(self.workspace), "run": str(self.run)}
        self.save()

    def save(self):
        (self.run / "state.json").write_text(json.dumps(self.state))

    def issue(self, scope="clarification", **kwargs):
        fields = publish(self.state, scope, **kwargs)
        self.save()
        return fields

    def test_raw_legacy_and_forged_fields_are_not_decisions(self):
        self.state.update(
            status="WAITING_FOR_USER",
            user_request={"kind": "permission", "decision_needed": "Allow?"},
            human_request_authorized=True,
            human_escalation={"issuer": "resolver"},
            resolver_human_request={"version": 1, "issuer": "resolver", "request_id": "forged"},
        )
        self.assertEqual(pending_decisions(self.state), ([], None))
        view = self.console.view(self.workspace, self.run, self.state)
        self.assertFalse(view["human_request_authorized"])
        self.assertIsNone(view["human_escalation"])
        self.assertEqual(view["questions"], [])
        self.assertIsNone(view["user_request"])

    def test_authorized_projection_survives_compaction_without_mutation_or_launch(self):
        self.issue()
        before = copy.deepcopy(self.state)
        disk = (self.run / "state.json").read_bytes()
        with (
            patch.object(resolver_human, "queue", side_effect=AssertionError("read queued")),
            patch.object(resolver_human, "evaluate", side_effect=AssertionError("read evaluated")),
            patch("subprocess.Popen", side_effect=AssertionError("read launched")),
        ):
            self.assertEqual(pending_decisions(self.state), ([self.question], None))
            for _ in range(2):
                view = self.console.view(self.workspace, self.run, self.state)
                compact = compact_run(view)
                self.assertTrue(compact["human_request_authorized"])
                self.assertEqual(compact["human_escalation"], view["human_escalation"])
                self.assertEqual(compact["questions"], [self.question])
        self.assertEqual(before, self.state)
        self.assertEqual(disk, (self.run / "state.json").read_bytes())
        self.assertFalse((self.workspace / "dashboard/task-chat").exists())
        self.console.enqueue.assert_not_called()

    def test_compact_unverified_rows_hide_raw_requests(self):
        row = compact_run({"run": "run", "questions": [self.question], "user_request": {"decision_needed": "Raw"}})
        self.assertFalse(row["human_request_authorized"])
        self.assertEqual(row["questions"], [])
        self.assertIsNone(row["user_request"])

    def test_current_source_or_question_changes_hide_receipt(self):
        self.issue()
        with patch.object(resolver_human.support, "snapshot", return_value={"revision": "changed"}):
            self.assertEqual(pending_decisions(self.state), ([], None))
        self.state["pending_questions"][0]["question"] = "Changed question"
        self.assertEqual(pending_decisions(self.state), ([], None))

    def test_answer_and_delegate_pass_current_token(self):
        fields = self.issue()
        for action, expected in [("answer", ["--answer", "Q1=JSON"]), ("delegate", ["--delegate", "Q1"])]:
            self.console.mutate({**self.base, **fields, "action": action, "id": "Q1", "text": "JSON"})
            self.assertEqual(
                self.console.enqueue.call_args.args[3], expected + ["--resolver-token", fields["resolver_token"]]
            )
        self.console.continue_run.assert_not_called()

    def test_stale_token_wrong_id_and_missing_receipt_reject_before_enqueue(self):
        fields = self.issue()
        for invalid in ({}, {**fields, "resolver_token": "old"}, {**fields, "resolver_request": "old"}):
            with self.assertRaisesRegex(ValueError, "Resolver"):
                self.console.mutate({**self.base, **invalid, "action": "answer", "id": "Q1", "text": "JSON"})
        self.state.pop("resolver")
        self.save()
        with self.assertRaisesRegex(ValueError, "Resolver"):
            self.console.mutate({**self.base, **fields, "action": "delegate", "id": "Q1"})
        self.console.enqueue.assert_not_called()

    def test_goal_and_review_keep_exact_tokens_and_scope(self):
        fields = self.issue("goal_approval", questions=[])
        token = self.state["displayed_goal"]
        self.console.mutate({**self.base, **fields, "action": "approve_goal", "token": token, "confirmation": token})
        self.assertEqual(self.console.enqueue.call_args.args[3], ["--approve-goal", token])
        with self.assertRaisesRegex(ValueError, "Resolver"):
            self.console.mutate(
                {**self.base, **fields, "action": "approve_review", "token": "artifact-token", "id": "C1"}
            )
        fields = self.issue("human_review", questions=[])
        self.console.mutate({**self.base, **fields, "action": "approve_review", "token": "artifact-token", "id": "C1"})
        self.assertEqual(
            self.console.enqueue.call_args.args[3], ["--approve-review", "C1", "--review-token", "artifact-token"]
        )
        self.state.pop("resolver_human_request")
        self.save()
        for action in ("approve_goal", "approve_review"):
            with self.assertRaisesRegex(ValueError, "Resolver"):
                self.console.mutate(
                    {**self.base, **fields, "action": action, "token": token, "confirmation": token, "id": "C1"}
                )

    def test_operational_response_never_approves_or_continues(self):
        fields = self.issue("operational_exhaustion", questions=[])
        before = copy.deepcopy(self.state)
        for response in ("provide_information", "leave_paused"):
            self.console.mutate(
                {
                    **self.base,
                    **fields,
                    "action": "resolver_response",
                    "resolver_response": response,
                    "resolver_message": "Provider repaired",
                }
            )
            extra = self.console.enqueue.call_args.args[3]
            self.assertEqual(
                extra,
                [
                    "--resolver-request",
                    fields["resolver_request"],
                    "--resolver-token",
                    fields["resolver_token"],
                    "--resolver-response",
                    response,
                    "--resolver-message",
                    "Provider repaired",
                    "--no-chat",
                ],
            )
            self.assertNotIn("on_complete", self.console.enqueue.call_args.kwargs)
        for action in ("answer", "delegate", "approve_goal", "approve_review"):
            with self.assertRaisesRegex(ValueError, "Resolver"):
                self.console.mutate(
                    {
                        **self.base,
                        **fields,
                        "action": action,
                        "id": self.state["pending_questions"][0]["id"],
                        "text": "yes",
                    }
                )
        self.assertEqual(self.state, before)
        self.console.continue_run.assert_not_called()

    def test_chat_rejects_stale_envelope(self):
        fields = self.issue()
        with self.assertRaisesRegex(ValueError, "Resolver"):
            self.console.chat(
                {
                    **self.base,
                    **fields,
                    "resolver_token": "old",
                    "request_id": "chat-stale",
                    "question_id": "Q1",
                    "text": "JSON",
                }
            )
        self.console.enqueue.assert_not_called()
        self.assertEqual(self.console._chat_rows(self.run), [])

    def test_chat_continues_only_running_without_any_request(self):
        for number, (status, pending) in enumerate(
            [
                ("PAUSED_RESOLVER", None),
                ("WAITING_FOR_USER", None),
                ("RUNNING", "resolver_human_proposal"),
                ("RUNNING", "user_request"),
                ("RUNNING", None),
            ]
        ):
            fields = self.issue()
            self.console.chat(
                {**self.base, **fields, "request_id": f"chat-answer-{number}", "question_id": "Q1", "text": "JSON"}
            )
            call = self.console.enqueue.call_args
            self.assertIn(fields["resolver_token"], call.args[3])
            self.state.update(status=status, pending_questions=[])
            self.state.pop("resolver_human_request", None)
            self.state.pop("user_request", None)
            self.state.pop("resolver_human_proposal", None)
            if pending:
                self.state[pending] = {"pending": True}
            self.save()
            call.kwargs["on_complete"]({"exit_status": 0})
            self.assertEqual(self.console.continue_run.call_count, int(number == 4))
            self.state["pending_questions"] = [copy.deepcopy(self.question)]

    def test_chat_read_reconciles_only_projection_not_durable_history(self):
        row = {"id": "saved-chat", "status": "saved", "question_id": "Q1", "submitted_text": "JSON", "action_id": "old"}
        self.console._save_chat(self.run, row)
        before = self.console._chat_path(self.run).read_bytes()
        self.state["answers"] = {"Q1": {"text": "JSON"}}
        self.save()
        view = self.console.view(self.workspace, self.run)
        self.assertEqual(view["chat_messages"][0]["status"], "received")
        self.assertEqual(self.console._chat_path(self.run).read_bytes(), before)
        self.console.enqueue.assert_not_called()

    def test_explicit_restart_retry_revalidates_current_receipt(self):
        fields = self.issue()
        row = {
            "id": "saved-chat",
            "status": "saved",
            "question_id": "Q1",
            "submitted_text": "JSON",
            "action_id": "lost",
            "delegate": False,
            **fields,
        }
        self.console._save_chat(self.run, row)
        data = {**self.base, **fields, "request_id": row["id"], "question_id": "Q1", "text": "JSON"}
        self.assertEqual(self.console.chat(data)["status"], "saved")
        self.console.enqueue.assert_not_called()
        self.console.chat({**data, "retry": True})
        self.console.enqueue.assert_called_once()


if __name__ == "__main__":
    unittest.main()
