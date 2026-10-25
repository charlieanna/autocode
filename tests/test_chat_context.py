"""Per-run unsent input and display context, with fresh authority after switching."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import autocode_chat_client as chat
import autocode_chat_context as contexts
import autocode_chat_panel as panel
import autocode_chat_tui as tui

from tests.test_chat_tui import screen_ui

WORKSPACE = Path("/tmp/context-project")
A = WORKSPACE / ".autocode" / "runs" / "a"
B = WORKSPACE / ".autocode" / "runs" / "b"


def view(run, context, *, durable=True):
    return {
        "workspace": str(WORKSPACE),
        "run_dir": str(run),
        "status": "RUNNING",
        "needs": {"kind": "continue"},
        "chat": {"context": context},
        "execution": {"mode": "durable_local" if durable else "owned", "detach_supported": durable},
    }


class PerRunContext(unittest.TestCase):
    def test_leaving_one_question_does_not_erase_the_other_runs_restored_prompt_and_draft(self):
        ui = screen_ui()
        ui.set_context(WORKSPACE, A)
        ui.observe(view(A, "a1"))
        ui._prompt = "A question"
        ui._handle(tui.Pasted("A draft 你好"), asking=True)
        ui.switch(WORKSPACE, B)
        ui.observe(view(B, "b1"))

        def switch_to_a():
            ui.switch(WORKSPACE, A)
            raise chat.Switched

        ui._read_key = switch_to_a
        with patch.object(tui.curses, "curs_set"), patch.object(tui.curses, "flushinp"):
            with self.assertRaises(chat.Switched):
                ui.ask("B question")
            ui.observe(view(A, "a1"))
            ui._read_key = lambda: "\n"
            self.assertEqual("A draft 你好", ui.ask("A question"))

    def test_restored_draft_does_not_claim_switch_capability_before_fresh_status(self):
        ui = screen_ui()
        ui.set_context(WORKSPACE, A)
        ui.observe(view(A, "a1"))
        ui.switch(WORKSPACE, B)
        ui.switch(WORKSPACE, A)
        act = Mock(return_value="Selected")
        ui.bind(act)
        ui._tab_key("next", asking=False)
        act.assert_not_called()
        self.assertIn("current status", ui._transcript.blocks[-1][1])
        ui.observe(view(A, "a1"))
        ui._tab_key("next", asking=False)
        act.assert_called_once()

    def test_unavailable_selected_run_keeps_navigation_without_reusing_action_authority(self):
        client = chat.ChatClient(WORKSPACE, B, say=Mock(), ask=Mock(return_value="yes"))
        self.addCleanup(client.close)
        client._status_view = view(B, "old-plan")
        with (
            patch.object(client, "status", side_effect=chat.actions.ClientGap("run was removed")),
            patch.object(client, "take") as take,
        ):
            self.assertIsNone(client.step())
            take.assert_not_called()
        self.assertIn("Nothing sent", client._say.call_args.args[0])

    def test_unavailable_target_can_switch_back_to_a_healthy_run(self):
        client = chat.ChatClient(WORKSPACE, B, say=Mock())
        self.addCleanup(client.close)

        def navigate(prompt):
            client.talk_to(WORKSPACE, A)
            raise chat.Switched

        client._ask = navigate
        healthy = {**view(A, "done-a"), "done": True}
        with (
            patch.object(client, "status", side_effect=[chat.actions.ClientGap("unreadable"), healthy]),
            patch.object(client, "follow_up", return_value=False),
            patch.object(client, "take") as take,
        ):
            self.assertEqual(0, client.converse())
            self.assertEqual(A, client.run_dir)
            take.assert_not_called()

    def test_switch_preserves_unicode_cursor_scroll_and_filter_without_leaking_into_next_run(self):
        ui = screen_ui()
        ui.set_context(WORKSPACE, A)
        ui.observe(view(A, "a1"))
        ui._handle(tui.Pasted("第一行\nsecond é line"), asking=False)
        ui._composer.left()
        cursor = ui._composer.cursor
        ui.say("A history\n" + "\n".join(str(number) for number in range(100)))
        ui._scroll = 5
        ui._live.filter, ui._live_on, ui._panel_on = "Tester", True, False
        self.assertFalse(ui.switch(WORKSPACE, B))
        self.assertEqual("", ui._composer.text)
        self.assertIsNone(ui._view)
        ui.observe(view(B, "b1"))
        ui._handle(tui.Pasted("B draft"), asking=False)
        ui._live.filter = "Builder"
        ui._screen.getmaxyx.return_value = (25, 76)
        self.assertTrue(ui.switch(WORKSPACE, A))
        self.assertEqual("第一行\nsecond é line", ui._composer.text)
        self.assertEqual(cursor, ui._composer.cursor)
        self.assertEqual(5, ui._scroll)
        self.assertEqual("Tester", ui._live.filter)
        self.assertTrue(ui._live_on)
        self.assertFalse(ui._panel_on)
        self.assertIsNone(ui._view, "display restoration never restores executable status authority")
        ui.switch(WORKSPACE, B)
        self.assertEqual("B draft", ui._composer.text)
        self.assertEqual("Builder", ui._live.filter)

    def test_same_question_restores_draft_but_a_changed_token_does_not_autofill_approval(self):
        for same in (True, False):
            with self.subTest(same_context=same):
                ui = screen_ui("\n")
                ui.set_context(WORKSPACE, A)
                ui.observe(view(A, "plan-r6"))
                ui._prompt = "Approve this plan?"
                ui._handle(tui.Pasted("yes"), asking=True)
                ui.switch(WORKSPACE, B)
                ui.observe(view(B, "b1"))
                ui.switch(WORKSPACE, A)
                ui.observe(view(A, "plan-r6" if same else "plan-r7"))
                with patch.object(tui.curses, "curs_set"), patch.object(tui.curses, "flushinp"):
                    self.assertEqual("yes" if same else "", ui.ask("Approve this plan?"))
                if not same:
                    self.assertIn("yes", ui._composer.history)
                    ui._composer.previous()
                    self.assertEqual("yes", ui._composer.text, "the user may explicitly recover a superseded draft")

    def test_pending_confirmations_and_queued_keys_never_transfer_between_runs(self):
        ui = screen_ui()
        ui.set_context(WORKSPACE, A)
        ui.observe(view(A, "a1"))
        action = Mock()
        ui._pending = panel.Confirm("Old comparison", "Restore?", action)
        ui._queued = ["\n"]
        ui.switch(WORKSPACE, B)
        self.assertIsNone(ui._pending)
        self.assertEqual([], ui._queued)
        ui.switch(WORKSPACE, A)
        self.assertIsNone(ui._pending)
        action.assert_not_called()

    def test_only_a_durable_working_view_enables_switching_and_default_owned_stays_gated(self):
        for durable in (False, True):
            ui = screen_ui()
            ui.observe(view(A, "a1", durable=durable))
            act = Mock(return_value="selected")
            ui.bind(act)
            ui._tab_key("next", asking=False)
            self.assertEqual(int(durable), act.call_count)
            offered = {row.name for row in panel.commands(asking=False, detachable=durable)}
            self.assertEqual(durable, "/open" in offered)
            self.assertEqual(durable, "/new" in offered)
            self.assertNotIn("/undo", offered)

    def test_cross_project_history_uses_each_projects_file_and_same_titles_keep_distinct_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            projects = [Path(directory) / name for name in ("one", "two")]
            for index, workspace in enumerate(projects):
                (workspace / ".autocode").mkdir(parents=True)
                tui.keep_history(workspace / ".autocode" / tui.HISTORY_FILE, [f"Project {index} request"])
            ui = screen_ui()
            ui.set_context(projects[0], projects[0] / ".autocode" / "runs" / "same-title")
            self.assertEqual(["Project 0 request"], ui._composer.history)
            ui.switch(projects[1], projects[1] / ".autocode" / "runs" / "same-title")
            self.assertEqual(["Project 1 request"], ui._composer.history)
            ui.switch(projects[0], projects[0] / ".autocode" / "runs" / "same-title")
            self.assertEqual(["Project 0 request"], ui._composer.history)

    def test_new_draft_cancel_restores_current_run_and_background_observation_never_steals_it(self):
        ui = screen_ui()
        ui.set_context(WORKSPACE, A)
        ui.observe(view(A, "a1"))
        ui._handle(tui.Pasted("old A draft"), asking=False)
        client = chat.ChatClient(WORKSPACE, A, say=ui.say, observe=ui.observe, ask=Mock(return_value=""))
        self.addCleanup(client.close)
        client.switched = ui.switch
        client.new_chat()
        self.assertEqual("old A draft", ui._composer.text)
        self.assertEqual(A, client.run_dir)
        self.assertFalse(client._composing_new)
        self.assertEqual(ui.observe, client._observe)

    def test_renderer_cache_and_client_message_cache_restore_together_with_no_cross_run_pause(self):
        ui = screen_ui()
        ui.set_context(WORKSPACE, A)
        ui.observe(view(A, "a1"))
        client = chat.ChatClient(WORKSPACE, A, say=ui.say, observe=ui.observe)
        self.addCleanup(client.close)
        client.switched = ui.switch
        client.seen, client.said, client.started = {"a-message"}, {"A said"}, True
        client._replied_context, client._pause_pending = "a-token", True
        client.talk_to(WORKSPACE, B)
        self.assertEqual(set(), client.seen)
        self.assertIsNone(client._replied_context)
        self.assertFalse(client._pause_pending)
        client.accept_status(view(B, "b1"))
        client.seen = {"b-message"}
        client.talk_to(WORKSPACE, A)
        self.assertEqual({"a-message"}, client.seen)
        self.assertEqual("a-token", client._replied_context)
        self.assertIsNone(client._status_view)
        self.assertIsNone(ui._view)

    def test_context_cache_is_bounded_and_identity_does_not_depend_on_title(self):
        cache = contexts.Contexts(limit=2)
        for run in (A, B, B.with_name("third")):
            cache.keep(contexts.identity(WORKSPACE, run), {"title": "Identical title"})
        self.assertIsNone(cache.get(contexts.identity(WORKSPACE, A)))
        self.assertEqual("Identical title", cache.get(contexts.identity(WORKSPACE, B))["title"])

    def test_new_request_draft_is_local_and_recoverable_before_a_run_exists(self):
        ui = screen_ui("\n")
        ui.switch(WORKSPACE, None)
        ui._prompt = "What do you want done?"
        ui._handle(tui.Pasted("Unsent new request"), asking=True)
        ui.switch(WORKSPACE, A)
        ui.observe(view(A, "a1"))
        ui.switch(WORKSPACE, None)
        self.assertIsNone(ui._view)
        with patch.object(tui.curses, "curs_set"), patch.object(tui.curses, "flushinp"):
            self.assertEqual("Unsent new request", ui.ask("What do you want done?"))

    def test_other_run_completion_updates_attention_without_stealing_the_draft(self):
        ui = screen_ui()
        ui.set_context(WORKSPACE, B)
        ui.observe(view(B, "b1"))
        ui._handle(tui.Pasted("B stays here 你好"), asking=True)
        cursor = ui._composer.cursor
        client = chat.ChatClient(WORKSPACE, B, say=ui.say, observe=ui.observe)
        self.addCleanup(client.close)
        client.others[str(A)] = (WORKSPACE, None)
        client.other_status(str(A), WORKSPACE, {**view(A, "done-a"), "done": True})
        self.assertEqual(B, client.run_dir)
        self.assertEqual("B stays here 你好", ui._composer.text)
        self.assertEqual(cursor, ui._composer.cursor)
        self.assertEqual(str(B), ui._view["run_dir"])
