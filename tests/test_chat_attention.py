"""Public attention discovery, ordering, keyboard isolation and bounded transport."""

import curses
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_chat_attention as attention
import autocode_chat_attention_action as transport
import autocode_chat_attention_discovery as discovery
import autocode_chat_attention_panel as panel
import autocode_chat_client as chat

from tests.test_attention import RUN, WORKSPACE, offered
from tests.test_chat_polling import Child
from tests.test_chat_tui import screen_ui


def row(context="plan-6", **changes):
    return {
        "workspace": str(WORKSPACE),
        "run_dir": str(RUN),
        "availability": "available",
        "attention": offered(context=context),
        "needs": "approve_plan",
        **changes,
    }


def listing(inbox, *rows, started=0):
    inbox.listing(json.dumps({"runs": rows}), "", started=started)


class InboxTests(unittest.TestCase):
    def test_current_prompt_is_not_duplicated_but_background_and_new_draft_notices_remain(self):
        said = Mock()
        client = chat.ChatClient(WORKSPACE, RUN, say=said)
        self.addCleanup(client.close)
        client._poller.tick = Mock(return_value=[])
        client._attention.notifications = Mock(return_value=[row()])
        client.poll()
        said.assert_not_called()
        self.assertIsNone(client._status_view, "notification filtering cannot synthesize a task view")
        client._composing_new = True
        client.poll()
        self.assertIn("New attention:", said.call_args.args[0])
        said.reset_mock()
        client._composing_new = False
        client._attention.notifications.return_value = [row(run_dir=str(RUN.with_name("another")))]
        client.poll()
        self.assertIn("New attention:", said.call_args.args[0])

    def test_restart_restores_unread_silently_then_a_new_decision_notifies_once(self):
        inbox = attention.Inbox()
        listing(inbox, row())
        self.assertEqual(1, inbox.summary()["unread"])
        self.assertEqual([], inbox.notifications(), "cold-start inventory is silent")
        listing(inbox, row())
        self.assertEqual([], inbox.notifications())
        listing(inbox, row("plan-7"))
        self.assertEqual([row("plan-7")], inbox.notifications())
        listing(inbox, row("plan-7"))
        self.assertEqual([], inbox.notifications())
        restored = attention.Inbox()
        listing(restored, row("plan-7"))
        self.assertEqual(1, restored.summary()["unread"])
        self.assertEqual([], restored.notifications())

    def test_old_inventory_cannot_replace_a_newer_status_or_transfer_an_ack(self):
        inbox = attention.Inbox()
        listing(inbox, row())
        current = row("plan-7")
        inbox.observe({**current, "needs": {"kind": "approve_plan"}}, received=20)
        listing(inbox, row(), started=10)
        self.assertEqual(current["attention"], inbox.entries()[0]["attention"])
        inbox.acknowledged(current["attention"]["item"]["id"], {"receipt": "saved"})
        self.assertEqual(0, inbox.summary()["unread"])
        listing(inbox, row("plan-8"), started=30)
        self.assertEqual(1, inbox.summary()["unread"])

    def test_resolved_or_acknowledged_events_do_not_emit_queued_notifications(self):
        inbox = attention.Inbox()
        listing(inbox, row())
        listing(inbox, row("plan-7"))
        inbox.observe({**row(), "attention": {"item": None}, "needs": {"kind": "continue"}}, received=10)
        self.assertEqual([], inbox.notifications())
        self.assertEqual([], inbox.entries())

    def test_corrupt_inventory_and_missing_metadata_remain_visible_unknown(self):
        inbox = attention.Inbox()
        listing(inbox, row(attention=[]))
        self.assertEqual(1, inbox.summary()["unknown"])
        inbox.listing("invalid", "", started=10)
        self.assertTrue(inbox.summary()["error"])
        self.assertEqual(1, inbox.summary()["unknown"])
        inbox.unavailable((str(WORKSPACE), str(RUN)))
        self.assertIn("unavailable", attention.label(inbox.entries()[0]))

    def test_two_completions_in_same_chat_have_distinct_wait_identities(self):
        first = {**row(), "done": True, "attention": offered(done=True)}
        second = {**first, "attention": offered(done=True, turns=[{"event_id": "next-request"}])}
        self.assertNotEqual(chat.wait_identity(first), chat.wait_identity(second))
        inbox = attention.Inbox()
        listing(inbox, first)
        listing(inbox, second)
        self.assertEqual([second], inbox.notifications())


class InventoryReads(unittest.TestCase):
    def test_inventory_uses_existing_two_child_bound_and_does_not_block_current_status(self):
        import autocode_chat_polling as polling

        children = []

        def launch(argv, **kwargs):
            child = Child(kwargs["stdout"], len(children))
            child.argv = argv
            children.append(child)
            return child

        self.poller = polling.StatusPoller(("autocode",), clock=lambda: 100, launch=launch)
        self.children = children
        with patch.object(polling.os, "killpg"):
            self.check_reads()

    def check_reads(self):
        current, inventory = (str(WORKSPACE), str(RUN)), (str(WORKSPACE), None)
        self.poller.tick({inventory: 10, current: 0})
        self.assertEqual(2, len(self.poller.active))
        self.assertEqual(["runs", "--all", "--json"], self.children[0].argv[1:])
        self.children[1].finish({"done": False})
        result = self.poller.tick({inventory: 10, current: 10})
        self.assertEqual(current, result[0][0])
        self.assertFalse(self.children[0].reaped)
        self.poller.close()
        self.assertTrue(all(child.reaped for child in self.children))


class DrawerTests(unittest.TestCase):
    def test_acknowledgement_and_event_kind_stay_visible_ahead_of_a_long_task_snippet(self):
        import autocode_chat_tui as tui

        current = row()
        current["attention"]["item"].update(
            summary="Long task 你好 " * 8, kind="completion", label="Run finished; inspect evidence"
        )
        current["attention"]["acknowledgement"]["state"] = "acknowledged"
        shown = tui.truncate(attention.label(current), 80)
        self.assertIn("[acknowledged]", shown)
        self.assertIn("Run finished; inspect evidence", shown)

    def test_acknowledge_and_cancel_preserve_unicode_draft_cursor_and_history_view(self):
        ui = screen_ui()
        ui._composer.insert("draft 你好")
        ui._composer.left()
        ui._scroll, ui._live.filter = 4, "Builder"
        before = (ui._composer.text, ui._composer.cursor, ui._scroll, ui._live.filter)
        acknowledge, opened = Mock(return_value="Acknowledgement pending"), Mock()
        menu = attention.Menu([row()], {"unread": 1}, opened, acknowledge, Mock())
        ui._queued = ["a", "\n"]
        with patch.object(curses, "flushinp"):
            panel.show(ui, menu)
        self.assertEqual([], ui._queued, "type-ahead cannot acknowledge unseen alerts")
        panel.handle(ui, "a", asking=True)
        acknowledge.assert_called_once_with(row())
        panel.handle(ui, "\x1b", asking=True)
        self.assertIsNone(ui._attention_menu)
        self.assertEqual(before, (ui._composer.text, ui._composer.cursor, ui._scroll, ui._live.filter))
        opened.assert_not_called()

    def test_working_owned_view_cannot_switch_and_ack_has_no_task_action(self):
        ui = screen_ui()
        opened = Mock()
        menu = attention.Menu([row()], {}, opened, Mock(), Mock())
        ui._attention_menu = menu
        panel.handle(ui, "\n", asking=False)
        opened.assert_not_called()
        self.assertIn("owns active work", menu.note)
        ui._view = {"execution": {"mode": "durable_local", "detach_supported": True}}
        panel.handle(ui, "\n", asking=False)
        opened.assert_called_once_with(row())

    def test_narrow_header_keeps_attention_badge_with_a_long_workspace_name(self):
        ui = screen_ui()
        ui._context = "very-long-workspace-" * 10
        ui._attention_view = {"unread": 2, "unknown": 1}
        shown = []
        ui._put = lambda line, col, text, attr=0: shown.append((line, col, text))
        ui._header(ui._layout(24, 80))
        header = [(col, text) for line, col, text in shown if line == 0]
        self.assertTrue(any("F8: 2 new alerts, 1 unchecked" in text for _, text in header))
        self.assertTrue(all(col + len(text) <= 80 for col, text in header))

    def test_attention_badge_counts_in_plain_words(self):
        for counts, expected in (
            ({"unread": 1}, " F8: 1 new alert"),
            ({"unread": 0, "unknown": 2}, " F8: 2 unchecked alerts"),
            ({"unread": 3, "unknown": 1}, " F8: 3 new alerts, 1 unchecked"),
            ({}, ""),
            ({"unread": 2, "error": "boom"}, " F8: alerts unavailable: boom"),  # the reason travels with it
        ):
            with self.subTest(counts=counts):
                ui = screen_ui()
                ui._attention_view = counts
                self.assertEqual(expected, ui._attention_badge())

    def test_discovery_handoff_closes_only_the_replaced_read_pool(self):
        discovered = discovery.Discovery(
            WORKSPACE, command=("autocode",), env=None, say=Mock(), changed=Mock(), open_run=Mock()
        )
        self.addCleanup(discovered.close)
        client = SimpleNamespace(_poller=Mock(), _status_view=None)
        old = client._poller
        discovered.transfer(client)
        discovered.close()
        old.close.assert_called_once()
        self.assertIs(client._poller, discovered._poller)
        self.assertFalse(client._poller.closed)
        client._poller.close()


class AcknowledgementTransport(unittest.TestCase):
    def start(self):
        self.now, self.children = 100.0, []

        def launch(argv, **kwargs):
            child = Child(kwargs["stdout"], len(self.children))
            self.children.append(child)
            return child

        self.kill = patch.object(transport.os, "killpg").start()
        self.addCleanup(patch.stopall)
        pending = transport.Pending(
            ("autocode",), ["attention", "ack"], row(), env=None, cwd=WORKSPACE, clock=lambda: self.now, launch=launch
        )
        self.addCleanup(pending.close)
        return pending

    def test_timeout_reaps_only_the_ack_group_without_automatically_resending(self):
        pending = self.start()
        self.assertIsNone(pending.poll())
        self.now += transport.polling.READ_TIMEOUT
        self.assertFalse(pending.poll()["acknowledged"])
        self.assertTrue(self.children[0].reaped)
        self.assertTrue(self.children[0].output.closed)
        self.assertEqual(1, len(self.children))
        self.assertIsNone(pending.poll())
        self.kill.assert_called_once()

    def test_lost_malformed_or_wrong_run_receipts_are_never_synthesized_success(self):
        for reply in ([], {"acknowledged": True, "receipt": {"item_id": "wrong"}}):
            with self.subTest(reply=reply):
                pending = self.start()
                self.children[0].output.write(json.dumps(reply).encode())
                self.children[0].code = 0
                self.assertFalse(pending.poll()["acknowledged"])

    def test_shared_ack_success_cancels_pre_receipt_reads_without_switching_chat(self):
        pending = self.start()
        current = row()["attention"]["item"]
        receipt = {"item_id": current["id"], "workspace": current["workspace"], "run_dir": current["run_dir"]}
        self.children[0].output.write(json.dumps({"acknowledged": True, "receipt": receipt}).encode())
        self.children[0].code = 0
        inbox = attention.Inbox()
        listing(inbox, row())
        client = SimpleNamespace(
            _attention_action=pending,
            _attention=inbox,
            _poller=Mock(),
            workspace=WORKSPACE,
            say=Mock(),
            attention_changed=Mock(),
        )
        attention.poll_acknowledgement(client)
        self.assertIsNone(client._attention_action)
        self.assertEqual(0, inbox.summary()["unread"])
        self.assertEqual(str(WORKSPACE), str(client.workspace))
        self.assertEqual(2, client._poller.refresh.call_count)
        self.assertIn("still needs", client.say.call_args.args[0])
