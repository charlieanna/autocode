"""A running chat offers a receipted message, never implicit feedback or approval."""

import copy
import json
import shlex
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import autocode_chat_messages as sending
import autocode_chat_view as chat_view
import autocode_client_actions as actions
import autocode_interventions as interventions
import autocode_util as util

from scenarios.harness.conformance import ConformanceDriver, ConformanceGap
from tests import ROOT
from tests import test_run_messages_boundary as boundary_fixture
from tests.test_chat_tui import screen_ui

WORKSPACE = Path("/project")
RUN = WORKSPACE / ".autocode/runs/chat"


def working_view():
    state = {
        "status": "RUNNING",
        "settings": {"spec_first": True},
        "intervention_capability": {"supports_messages": True},
    }
    view = {
        "workspace": str(WORKSPACE),
        "run_dir": str(RUN),
        "status": "RUNNING",
        "needs": {"kind": "continue"},
        "liveness": {"kind": "supervised"},
    }
    view["chat"] = chat_view.project(state, view, workspace=WORKSPACE, run_dir=RUN)
    return view


class MessageOfferTests(unittest.TestCase):
    def test_headless_client_can_take_exactly_the_same_message_offer(self):
        view = working_view()
        headless = object.__new__(ConformanceDriver)
        headless.project, headless.run_dir = WORKSPACE, RUN
        words, environment, source = headless.message_command(view, "Could we reuse it?", request_id="once")
        expected = actions.message(
            view, "Could we reuse it?", view["chat"]["context"], WORKSPACE, RUN, request_id="once"
        )
        self.assertEqual((expected.argv, expected.env, "chat.input.next_command"), (words, environment, source))
        with self.assertRaises(ConformanceGap):
            headless.message_command(view, "Could we reuse it?")

    def test_running_view_names_the_pending_only_action_with_no_approval_or_pause(self):
        view = working_view()
        offer = view["chat"]["input"]
        self.assertIsNotNone(offer)
        self.assertEqual("message", offer["kind"])
        self.assertEqual(
            [
                "autocode",
                "intervention",
                "submit",
                "--workspace",
                str(WORKSPACE),
                "--run-dir",
                str(RUN),
                "--request-id",
                "REQUEST_ID",
                "--kind",
                "message",
                "--text",
                "MESSAGE",
                "--json",
            ],
            shlex.split(offer["next_command"]),
        )
        schema = json.loads((ROOT / "docs/schemas/status-view.schema.json").read_text())
        util.validate_schema(view["chat"], schema["properties"]["view"]["properties"]["chat"])

    def test_no_offer_for_old_workers_nonchat_holds_or_individual_coordinated_members(self):
        for change, public in (
            ({"intervention_capability": {}}, {}),
            ({"settings": {"spec_first": False}}, {}),
            ({"settings": {"spec_first": True, "coordination_member": True}}, {}),
            ({}, {"coordination": {"phase": "fixing"}}),
            ({"applied_interventions": [{"kind": "stop"}]}, {}),
            ({}, {"status": "PAUSED_COST_LIMIT"}),
        ):
            with self.subTest(change=change, public=public):
                state = {
                    "status": "RUNNING",
                    "settings": {"spec_first": True},
                    "intervention_capability": {"supports_messages": True},
                    **change,
                }
                view = {"status": "RUNNING", "needs": {"kind": "continue"}, **public}
                before = copy.deepcopy((state, view))
                self.assertIsNone(chat_view.project(state, view, workspace=WORKSPACE, run_dir=RUN)["input"])
                self.assertEqual(before, (state, view))

    def test_client_fills_only_exact_offered_text_and_stable_request_identity(self):
        view = working_view()
        command = actions.message(
            view, "Could we reuse it?", view["chat"]["context"], WORKSPACE, RUN, request_id="typed-once"
        )
        self.assertEqual(
            [
                "intervention",
                "submit",
                "--request-id",
                "typed-once",
                "--kind",
                "message",
                "--text",
                "Could we reuse it?",
                "--json",
            ],
            command.argv,
        )
        self.assertEqual({}, command.env)
        for mutation in ("context", "path", "kind", "missing_id"):
            altered = copy.deepcopy(view)
            token = view["chat"]["context"]
            if mutation == "context":
                token = "stale"
            if mutation == "path":
                altered["chat"]["input"]["next_command"] = altered["chat"]["input"]["next_command"].replace(
                    str(RUN), "/another"
                )
            if mutation == "kind":
                altered["chat"]["input"]["next_command"] = altered["chat"]["input"]["next_command"].replace(
                    "--kind message", "--kind feedback"
                )
            with self.subTest(mutation=mutation), self.assertRaises(actions.ClientGap):
                actions.message(
                    altered,
                    "Could we reuse it?",
                    token,
                    WORKSPACE,
                    RUN,
                    request_id=None if mutation == "missing_id" else "typed-once",
                )


class MessageReceiptTests(unittest.TestCase):
    setUp = boundary_fixture.MessageBoundaryTests.setUp
    save = boundary_fixture.MessageBoundaryTests.save
    submit = boundary_fixture.MessageBoundaryTests.submit
    consume = boundary_fixture.MessageBoundaryTests.consume

    def test_receipt_names_the_public_chat_message_for_an_immediate_echo(self):
        receipt = self.submit()["receipt"]
        self.assertTrue(receipt.get("message_id"))
        self.consume()
        view = chat_view.project(self.state, {"status": self.state["status"]})
        row = next(row for row in view["messages"] if row.get("id") == receipt["message_id"])
        self.assertEqual(receipt["text"], row["text"])
        self.assertEqual("user", row["role"])
        self.assertEqual(
            receipt,
            interventions.submit(
                self.workspace, self.run, request_id=receipt["id"], kind="message", text=receipt["text"]
            )["receipt"],
        )


class LiveSendTests(unittest.TestCase):
    def test_empty_enter_after_a_saved_message_never_creates_a_second_submission(self):
        ui = screen_ui()
        ui.observe(working_view())
        ui.send_message = mock.Mock(return_value={"id": "saved"})
        ui._composer.insert("Fix only the command name and result code")
        ui._enter(False)
        before = list(ui._transcript.blocks)
        for empty in ("", "   ", "\t"):
            with self.subTest(empty=repr(empty)):
                ui._composer.clear()
                ui._composer.insert(empty)
                ui._enter(False)
                self.assertEqual(1, ui.send_message.call_count)
                self.assertEqual(before, ui._transcript.blocks)
                self.assertIsNone(ui._message_retry)

    def test_status_polling_cannot_resubmit_enter_or_lose_the_next_typed_draft(self):
        ui = screen_ui()
        ui.observe(working_view())
        ui._composer.insert("First question")
        sent = []

        def submit(text, shown):
            sent.append(text)
            if len(sent) == 1:
                ui._enter(False)  # a second Enter while the fresh status read pumps input
                ui._handle("x", asking=False)
            return {"id": "once"}

        ui.send_message = submit
        ui._enter(False)
        self.assertEqual(["First question"], sent)
        self.assertEqual("", ui._composer.text)
        self.assertEqual(["x"], ui._queued)
        ui._handle(ui._queued.pop(0), asking=False)
        self.assertEqual("x", ui._composer.text)

    def client(self, view):
        return SimpleNamespace(
            workspace=WORKSPACE, run_dir=RUN, status=mock.Mock(return_value=view), cli=mock.Mock(), seen=set()
        )

    def receipt(self, argv, **kwargs):
        request = argv[argv.index("--request-id") + 1]
        text = argv[argv.index("--text") + 1]
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "version": 1,
                    "operation": "submit",
                    "accepted": True,
                    "receipt": {
                        "id": request,
                        "kind": "message",
                        "text": text,
                        "message_id": "public-message-" + request,
                    },
                }
            ),
        )

    def test_sender_uses_only_named_pending_action_and_receipt_marks_one_public_echo(self):
        view = working_view()
        client = self.client(view)
        client.cli.side_effect = self.receipt
        receipt = sending.Sender(client).send("What do you think?", view)
        self.assertEqual("What do you think?", receipt["text"])
        self.assertIn(receipt["message_id"], client.seen)
        argv = client.cli.call_args.args[0]
        self.assertEqual(["intervention", "submit"], argv[:2])
        self.assertNotIn("--no-chat", argv)
        self.assertEqual(str(RUN), argv[argv.index("--run-dir") + 1])

    def test_uncertain_receipt_retry_keeps_the_same_request_without_retargeting(self):
        view = working_view()
        client = self.client(view)
        sender = sending.Sender(client)
        client.cli.side_effect = actions.ClientGap("receipt timeout")
        with self.assertRaises(sending.MessageError):
            sender.send("Question", view)
        first = client.cli.call_args.args[0]
        client.cli.side_effect = self.receipt
        # Reconciliation is the same formerly named pending-only action, even if
        # the provider reached a different checkpoint before its receipt arrived.
        client.status.return_value = {**view, "chat": {"context": "later", "input": None}}
        sender.send("Question", view)
        self.assertEqual(first, client.cli.call_args.args[0])
        client.workspace, client.run_dir = Path("/other"), Path("/other/.autocode/runs/r")
        before = client.cli.call_count
        with self.assertRaises(sending.MessageError):
            sender.send("Question", view)
        self.assertEqual(before, client.cli.call_count)

    def test_malformed_or_mismatched_receipt_never_clears_pending_request(self):
        for payload in (
            "not json",
            json.dumps({"accepted": True}),
            json.dumps(
                {
                    "version": 1,
                    "operation": "submit",
                    "accepted": True,
                    "receipt": {"id": "other", "kind": "message", "text": "Question", "message_id": "other"},
                }
            ),
        ):
            client = self.client(working_view())
            client.cli.return_value = SimpleNamespace(returncode=0, stdout=payload)
            sender = sending.Sender(client)
            with self.assertRaises(sending.MessageError):
                sender.send("Question", working_view())
            first = client.cli.call_args.args[0]
            client.cli.side_effect = self.receipt
            sender.send("Question", working_view())
            self.assertEqual(first, client.cli.call_args.args[0])

    def test_running_composer_sends_message_and_keeps_draft_on_uncertainty(self):
        for success in (True, False):
            with self.subTest(success=success):
                ui = screen_ui()
                ui.observe(working_view())
                ui._composer.insert("What do you think?")
                ui.send_message = mock.Mock(return_value={"id": "once"})
                if not success:
                    ui.send_message.side_effect = sending.MessageError("Receipt unavailable; text kept")
                self.assertIsNone(ui._enter(False))
                ui.send_message.assert_called_once_with("What do you think?", working_view())
                self.assertEqual("" if success else "What do you think?", ui._composer.text)
                text = str(ui._transcript.blocks)
                self.assertIn("Message saved" if success else "Receipt unavailable", text)

    def test_running_composer_without_offer_does_not_send_and_new_chat_cannot_inherit_sender(self):
        ui = screen_ui()
        ui.send_message = mock.Mock()
        ui.observe({"status": "RUNNING", "chat": {"input": None}})
        ui._composer.insert("Keep this draft")
        ui._enter(False)
        ui.send_message.assert_not_called()
        self.assertEqual("Keep this draft", ui._composer.text)

    def test_live_composer_explains_that_messages_are_allowed_while_work_continues(self):
        ui = screen_ui()
        ui.send_message = mock.Mock()
        ui.observe(working_view())
        drawn = []
        ui._put = lambda row, column, text, *rest: drawn.append(text)
        ui._composer_box(ui._layout(35, 120))
        self.assertIn("Message", drawn)
        self.assertTrue(any("Type a message" in text for text in drawn), drawn)

    def test_uncertain_message_at_a_later_approval_prompt_reconciles_instead_of_becoming_feedback(self):
        ui = screen_ui()
        original = working_view()
        ui.observe(original)
        ui._composer.insert("What do you think?")
        ui.send_message = mock.Mock(side_effect=sending.MessageError("Receipt uncertain"))
        ui._enter(False)
        ui.observe(
            {
                **original,
                "status": "AWAITING_GOAL_APPROVAL",
                "chat": {"context": "later", "input": {"kind": "feedback"}},
            }
        )
        ui._asking = True
        ui.send_message.side_effect = None
        self.assertIsNone(ui._enter(True), "Do not send the retained text as a new approval-prompt answer")
        ui.send_message.assert_called_with("What do you think?", original)
        self.assertEqual("", ui._composer.text)
        self.assertTrue(ui._asking, "Receipt reconciliation must leave the plan decision unanswered")

    def test_sender_never_treats_a_checkpoint_feedback_offer_as_a_pending_message(self):
        view = working_view()
        view["chat"]["input"] = {
            "kind": "feedback",
            "next_command": f"autocode --workspace {WORKSPACE} --run-dir {RUN} --feedback MESSAGE",
        }
        client = self.client(view)
        client.cli.side_effect = self.receipt
        with self.assertRaises(sending.MessageError):
            sending.Sender(client).send("Change", view)
        client.cli.assert_not_called()

    def test_a_fresh_view_for_another_run_cannot_retarget_the_submission(self):
        original = working_view()
        changed = copy.deepcopy(original)
        changed["workspace"], changed["run_dir"] = "/other", "/other/.autocode/runs/chat"
        changed["chat"]["input"]["next_command"] = changed["chat"]["input"]["next_command"].replace(
            "/project", "/other"
        )
        client = self.client(changed)
        client.cli.side_effect = self.receipt
        with self.assertRaises(sending.MessageError):
            sending.Sender(client).send("What do you think?", original)
        client.cli.assert_not_called()
