"""The full-screen chat's pure pieces: width, wrapping, transcript blocks, composer, window."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_chat_client as chat_client
import autocode_chat_tui as tui


class DisplayWidthTests(unittest.TestCase):
    def test_ascii_wide_and_combining(self):
        self.assertEqual(5, tui.display_width("hello"))
        self.assertEqual(4, tui.display_width("你好"))
        self.assertEqual(1, tui.display_width("e\u0301"))  # e + combining acute
        self.assertEqual(0, tui.display_width(""))

    def test_clip_never_exceeds_the_width(self):
        self.assertEqual("你", tui.clip("你好", 3))
        self.assertEqual("hello", tui.clip("hello", 5))
        self.assertEqual("", tui.clip("hello", 0))

    def test_truncate_marks_what_it_cut(self):
        self.assertEqual("hello", tui.truncate("hello", 5))
        self.assertEqual("hello w…", tui.truncate("hello world", 8))
        self.assertEqual("…", tui.truncate("hello", 1))


class SafeTextTests(unittest.TestCase):
    def test_escape_sequences_never_reach_the_terminal(self):
        self.assertEqual("red", tui.safe_text("\x1b[31mred\x1b[0m"))
        self.assertEqual("ok", tui.safe_text("\x1b]0;title\x07ok"))

    def test_other_control_characters_become_spaces(self):
        self.assertEqual("a b", tui.safe_text("a\x01b"))
        self.assertEqual("a   b", tui.safe_text("a\tb"))
        self.assertEqual("a\nb", tui.safe_text("a\nb"))


class WrapTests(unittest.TestCase):
    def test_breaks_at_spaces(self):
        self.assertEqual(["hello", "world"], tui.wrap("hello world", 5))
        self.assertEqual(["one two", "three"], tui.wrap("one two three", 7))

    def test_breaks_a_word_longer_than_the_line(self):
        self.assertEqual(["abcd", "efgh", "ij"], tui.wrap("abcdefghij", 4))

    def test_counts_wide_characters(self):
        self.assertEqual(["你好", "世界"], tui.wrap("你好世界", 4))

    def test_keeps_explicit_lines_and_indentation(self):
        self.assertEqual(["a", "", "  b"], tui.wrap("a\n\n  b", 20))
        self.assertEqual([""], tui.wrap("", 20))


class TranscriptTests(unittest.TestCase):
    def test_messages_and_roles(self):
        transcript = tui.Transcript()
        self.assertTrue(transcript.add("\nAutoCode: hello"))
        self.assertTrue(transcript.add("You: hi"))
        self.assertTrue(transcript.add("AutoCode stopped: provider quota"))
        self.assertEqual(
            [
                ("assistant-label", "AutoCode"),
                ("assistant-body", "  hello"),
                ("plain", ""),
                ("user-label", "You"),
                ("user-body", "  hi"),
                ("plain", ""),
                ("notice-label", "Notice"),
                ("notice-body", "  AutoCode stopped: provider quota"),
                ("plain", ""),
            ],
            transcript.lines(80),
        )

    def test_a_progress_line_is_a_status_not_a_message(self):
        transcript = tui.Transcript()
        self.assertFalse(transcript.add("  · Builder: editing greet.py"))
        self.assertEqual("Builder: editing greet.py", transcript.activity)
        self.assertEqual([], transcript.lines(80))
        transcript.add("AutoCode: done")
        self.assertEqual("", transcript.activity)

    def test_lines_name_where_the_newest_block_begins(self):
        # The body anchors a block taller than the screen to its top, so a long approval
        # reads from its first line instead of losing its head to the tail clip (#2434).
        transcript = tui.Transcript()
        transcript.add("AutoCode: first")
        transcript.add("You: a reply that is long enough to matter for anchoring")
        rows = transcript.lines(80)
        self.assertEqual(6, len(rows))
        self.assertEqual(3, transcript.last_block_start)
        self.assertEqual(("user-label", "You"), rows[transcript.last_block_start])
        transcript2 = tui.Transcript()
        transcript2.lines(80)
        self.assertIsNone(transcript2.last_block_start)

    def test_rendered_lines_are_reused_until_width_or_transcript_changes(self):
        transcript = tui.Transcript()
        transcript.add("AutoCode: hello world")
        original_wrap = tui.wrap
        with mock.patch.object(tui, "wrap", wraps=original_wrap) as wrapped:
            first = transcript.lines(80)
            self.assertIs(first, transcript.lines(80))
            self.assertEqual(1, wrapped.call_count)
            transcript.lines(40)
            self.assertEqual(2, wrapped.call_count, "a different width needs fresh wrapping")
            transcript.add("You: a new message")
            updated = transcript.lines(40)
            self.assertIsNot(first, updated, "new transcript content invalidates rendered lines")
            self.assertEqual(4, wrapped.call_count)
            transcript.live = "A streamed response"
            live = transcript.lines(40)
            self.assertIsNot(updated, live, "streamed response updates invalidate rendered lines")
            self.assertEqual(7, wrapped.call_count)
            self.assertIs(live, transcript.lines(40))
            self.assertEqual(7, wrapped.call_count)

    def test_the_closing_words_are_everything_said_after_the_conversation(self):
        transcript = tui.Transcript()
        transcript.add("AutoCode: the plan is ready")
        transcript.add("AutoCode needs you for this one (Waiting on another run). Waits for the API run")
        transcript.add("Run it yourself:\n  autocode wait --run-dir /p/api/.autocode/runs/r9")
        self.assertEqual(
            [
                "AutoCode needs you for this one (Waiting on another run). Waits for the API run",
                "Run it yourself:\n  autocode wait --run-dir /p/api/.autocode/runs/r9",
            ],
            transcript.closing(),
        )

    def test_what_a_command_showed_is_not_left_on_the_terminal(self):
        transcript = tui.Transcript()
        transcript.add("Done.")
        transcript.info("/log", "18:00:00 Complete")
        self.assertEqual(["Done."], transcript.closing())

    def test_with_nothing_said_after_the_conversation_its_last_message_stays(self):
        transcript = tui.Transcript()
        self.assertEqual([], transcript.closing())
        transcript.add("AutoCode: The requested task is complete.")
        self.assertEqual(["AutoCode: The requested task is complete."], transcript.closing())
        transcript.add("Done.")
        self.assertEqual(["Done."], transcript.closing())

    def test_leaving_the_first_question_leaves_only_what_was_said_after_it(self):
        """#1755: Ctrl-D at the run picker left every listed run on the terminal."""
        transcript = tui.Transcript()
        transcript.add("Runs waiting here:")
        transcript.add("  1. r1  Add a flag")
        transcript.add("  2. r2  Fix the typo")
        transcript.asked = len(transcript.blocks)  # Pick one (1-2), or type a new request:
        self.assertEqual([], transcript.closing())
        transcript.add("Nothing started.")
        self.assertEqual(["Nothing started."], transcript.closing())

    def test_before_any_question_everything_the_client_said_stays(self):
        transcript = tui.Transcript()
        transcript.add("AutoCode stopped: no run mode 'bogus'")
        transcript.add("Run it yourself:\n  autocode modes list")
        self.assertEqual(
            ["AutoCode stopped: no run mode 'bogus'", "Run it yourself:\n  autocode modes list"], transcript.closing()
        )

    def test_the_request_the_person_typed_is_shown_once(self):
        transcript = tui.Transcript()
        transcript.add("You: Add a --shout flag ")
        self.assertFalse(transcript.add("\nYou: Add a --shout flag"))
        self.assertEqual([("user", "You: Add a --shout flag ")], transcript.blocks)
        transcript.add("AutoCode: Is that right?")
        self.assertTrue(transcript.add("You: Add a --shout flag"), "a later reply that repeats it is new")

    def test_blank_text_is_ignored(self):
        transcript = tui.Transcript()
        self.assertFalse(transcript.add("\n\n"))
        self.assertEqual([], transcript.blocks)


class ComposerTests(unittest.TestCase):
    def test_editing_around_the_cursor(self):
        composer = tui.Composer()
        composer.insert("helo")
        composer.left()
        composer.insert("l")
        self.assertEqual("hello", composer.text)
        composer.home()
        composer.insert(">")
        composer.end()
        composer.backspace()
        self.assertEqual(">hell", composer.text)
        composer.home()
        composer.delete()
        self.assertEqual("hell", composer.text)

    def test_delete_word_removes_the_spaces_and_word_before_the_cursor(self):
        composer = tui.Composer()
        composer.insert("one two   ")
        composer.delete_word()
        self.assertEqual("one ", composer.text)
        composer.delete_word()
        self.assertEqual("", composer.text)

    def test_history_walks_back_and_restores_the_draft(self):
        composer = tui.Composer()
        composer.history = ["first", "second"]
        composer.insert("draft")
        composer.previous()
        self.assertEqual("second", composer.text)
        composer.previous()
        self.assertEqual("first", composer.text)
        composer.next()
        self.assertEqual("second", composer.text)
        composer.next()
        self.assertEqual("draft", composer.text)

    def test_submit_stores_nonempty_replies_only_and_resets(self):
        composer = tui.Composer()
        composer.insert("hi")
        self.assertEqual("hi", composer.submit())
        self.assertEqual(["hi"], composer.history)
        self.assertEqual("", composer.text)
        self.assertEqual(0, composer.cursor)
        self.assertEqual("", composer.submit())
        self.assertEqual(["hi"], composer.history)


class ComposerLabelTests(unittest.TestCase):
    def test_a_new_request_prompt_names_the_chat(self):
        self.assertEqual("New chat", tui.composer_label("What do you want done?", True))
        self.assertEqual("New chat", tui.composer_label("Pick one (1-3), or type a new request:", True))

    def test_other_questions_are_replies_and_waiting_names_the_run(self):
        self.assertEqual("Reply", tui.composer_label("Approve this plan? [y/N]", True))
        self.assertEqual("AutoCode", tui.composer_label("", False))


class PasteTests(unittest.TestCase):
    """What follows an escape key: a bracketed paste is one piece of text, anything else is not a paste."""

    def keys(self, *keys):
        source = iter(keys)
        return lambda: next(source, None)

    def test_a_pasted_line_break_stays_in_the_text(self):
        read = self.keys(*"[200~", *"Add a flag\r\nthat shouts\r", *"\x1b[201~")
        self.assertEqual("Add a flag\nthat shouts", tui.read_paste(read))

    def test_escape_codes_inside_a_paste_never_reach_the_terminal(self):
        read = self.keys(*"[200~", *"\x1b[31mred", *"\x1b[201~")
        self.assertEqual("red", tui.read_paste(read))

    def test_an_escape_that_starts_no_paste_is_not_one(self):
        self.assertIsNone(tui.read_paste(self.keys()))
        self.assertIsNone(tui.read_paste(self.keys("x")))
        self.assertIsNone(tui.read_paste(self.keys("[", "2", "0", "1", "~")))

    def test_the_keys_read_after_an_escape_that_is_no_paste_are_handed_back(self):
        consumed: list = []
        self.assertIsNone(tui.read_paste(self.keys("x", "y"), consumed))
        self.assertEqual(["x"], consumed)
        consumed = []
        self.assertIsNone(tui.read_paste(self.keys("[", 260), consumed))
        self.assertEqual(["[", 260], consumed)


class CsiTabTests(unittest.TestCase):
    """#1997: Ctrl-←/→ as the bytes the terminal really sends, so they never become composer text."""

    def keys(self, *keys):
        source = iter(keys)
        return lambda: next(source, None)

    def test_the_xterm_spelling_is_one_tab_step(self):
        tail = list("[1;5")  # what the paste probe leaves behind after ESC [ 1 ; 5
        tab = tui.csi_tab(self.keys("C"), tail)
        self.assertEqual("next", tab.direction)
        self.assertEqual(list("[1;5"), tail)  # the seed is part of the key, not of the reply

    def test_the_other_terminal_spellings_switch_too(self):
        for seed, final, direction in (("[5;5", "C", "next"), ("[[", "C", "next"), ("[1;5", "D", "previous")):
            with self.subTest(seed=seed, final=final):
                self.assertEqual(direction, tui.csi_tab(self.keys(final), list(seed)).direction)

    def test_a_tail_that_cannot_be_a_tab_key_is_handed_back_untouched(self):
        tail = ["[", "A"]
        self.assertIsNone(tui.csi_tab(self.keys(), tail))
        self.assertEqual(["[", "A"], tail)

    def test_a_key_that_arrives_only_halfway_keeps_every_character_it_got(self):
        tail = ["[", "1"]
        self.assertIsNone(tui.csi_tab(self.keys(";"), tail))
        self.assertEqual(["[", "1", ";"], tail)

    def test_the_reader_names_the_key_and_queues_nothing_for_the_composer(self):
        ui = screen_ui("\x1b", "[", "1", ";", "5", "C")
        key = ui._read_key()
        self.assertEqual("next", tui.tab_step(key))
        self.assertEqual([], ui._queued)

    def test_a_lone_escape_stays_just_escape(self):
        ui = screen_ui("\x1b")
        self.assertEqual(tui.ESCAPE, ui._read_key())
        self.assertEqual([], ui._queued)


def screen_ui(*keys):
    """A ChatTUI over a stand-in screen that delivers ``keys``, then times out like an idle terminal."""
    screen = mock.MagicMock()
    screen.getmaxyx.return_value = (30, 120)
    pending = list(keys)

    def get_wch():
        if not pending:
            raise tui.curses.error("no input")
        return pending.pop(0)

    screen.get_wch.side_effect = get_wch
    with mock.patch.object(tui.ChatTUI, "_open"):
        ui = tui.ChatTUI()
    ui._screen = screen
    return ui


class SmallTerminalTests(unittest.TestCase):
    def test_a_too_small_terminal_says_how_big_to_make_it_without_cutting_the_words(self):
        for rows, cols in ((10, 80), (24, 20), (6, 14)):
            with self.subTest(rows=rows, cols=cols):
                ui = screen_ui()
                ui._screen.getmaxyx.return_value = (rows, cols)
                shown = []
                ui._put = lambda line, col, text, attr=0: shown.append((line, text))
                ui._draw()
                self.assertTrue(all(len(text) <= cols - 1 for _, text in shown))
                self.assertTrue(all(line < rows for line, _ in shown))
                words = " ".join(text for _, text in sorted(shown)).split()
                self.assertEqual(
                    f"Terminal too small for the chat. Make it at least 32 columns wide and 12 rows tall (now {cols}x{rows}).".split()[
                        : len(words)
                    ],
                    words,
                )


class StatusLineTests(unittest.TestCase):
    def test_the_status_row_reads_in_plain_words_before_any_status_read(self):
        ui = screen_ui()
        shown = []
        ui._put = lambda line, col, text, attr=0: shown.append((line, text))
        ui._header(ui._layout(30, 120))
        row = next(text for line, text in shown if line == 2)
        self.assertEqual("Reading run status (status not read yet)", row.rstrip())


class PlanningCorrectionTests(unittest.TestCase):
    def test_the_full_screen_composer_sends_a_named_planning_correction(self):
        from autocode_run_view import view

        from tests.test_chat_client import DONE, RUN, WORKSPACE, ScriptedChat

        held = view(
            {"status": "PAUSED_INVALID_OUTPUT", "next_stage": "astra_finalize", "task": "Fix it"},
            workspace=WORKSPACE,
            run_dir=RUN,
        )
        correction = "Keep the exact original citations."
        ui = screen_ui(*correction, "\n", "\n")
        client = ScriptedChat(held, DONE)
        client._ask, client._say, client._observe = ui.ask, ui.say, ui.observe
        ui.bind(client.slash)
        with mock.patch.object(tui.curses, "curs_set"), mock.patch.object(tui.curses, "flushinp"):
            self.assertEqual(0, client.converse())
        self.assertEqual([["--feedback", correction]], client.taken)


class PauseHintTests(unittest.TestCase):
    def hints(self, *, coordinated=False, attached=False):
        ui = screen_ui()
        view = {"progress": {"line": "Working"}}
        if coordinated:
            view["coordination"] = {"cancel_command": ["autocode", "coordinate", "cancel"]}
        ui.observe(view)
        drawn = []
        ui._put = lambda row, col, text, attr=0: drawn.append(text)
        with mock.patch.object(ui, "_can_detach", return_value=attached):
            layout = ui._layout(40, 180)
            ui._question_or_status(layout)
            ui._hints(layout)
        return "\n".join(drawn)

    def test_coordinator_names_cancellation_in_both_running_hints(self):
        for attached in (False, True):
            with self.subTest(attached=attached):
                shown = self.hints(coordinated=True, attached=attached)
                self.assertIn("ctrl-c cancel coordination after this step", shown)
                self.assertIn("ctrl-c cancels coordination after this step", shown)
                self.assertNotIn("pause", shown)
                self.assertEqual(attached, "ctrl-d detaches" in shown)

    def test_ordinary_owned_and_attached_work_keep_their_pause_hints(self):
        self.assertIn("ctrl-c asks AutoCode to pause after this step", self.hints())
        self.assertIn("ctrl-c asks for a boundary pause", self.hints(attached=True))
        self.assertNotIn("cancel", self.hints())


class HistoryFileTests(unittest.TestCase):
    """The composer's ↑/↓ history, kept per project in .autocode between chats."""

    def test_requests_are_kept_in_order_and_bare_answers_are_not(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".autocode" / tui.HISTORY_FILE
            self.assertFalse(tui.keep_history(path, ["Add a flag"]), "never creates .autocode")
            self.assertFalse(path.parent.exists())
            path.parent.mkdir()
            self.assertTrue(tui.keep_history(path, ["Add a flag", "line one\nline two"]))
            self.assertEqual(["Add a flag", "line one\nline two"], tui.read_history(path))
            self.assertEqual(
                [False, False, False, True], [tui.worth_keeping(text) for text in ("y", "No", "2", "Use B")]
            )

    def test_a_long_history_keeps_the_newest_entries(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / tui.HISTORY_FILE
            tui.keep_history(path, [f"request {n}" for n in range(tui.HISTORY_LIMIT + 5)])
            kept = tui.read_history(path)
            self.assertEqual(tui.HISTORY_LIMIT, len(kept))
            self.assertEqual(f"request {tui.HISTORY_LIMIT + 4}", kept[-1])

    def test_the_chat_recalls_the_projects_earlier_requests(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / ".autocode").mkdir()
            tui.keep_history(Path(folder) / ".autocode" / tui.HISTORY_FILE, ["Fix the typo"])
            ui = screen_ui()
            ui.set_context(Path(folder))
            self.assertEqual(["Fix the typo"], ui._composer.history)

    def test_opening_another_chat_starts_its_screen_afresh(self):
        ui = screen_ui()
        ui.say("You: the old request")
        ui.observe({"progress": {"headline": "Complete"}})
        ui.switch(Path("/p"), Path("/p/.autocode/runs/r2"))
        self.assertEqual([], ui._transcript.blocks)
        self.assertIsNone(ui._view)
        self.assertEqual("p · r2", ui._context)


class LiveReplyTests(unittest.TestCase):
    def test_a_reply_being_written_shows_last_and_goes_when_done(self):
        ui = screen_ui()
        ui.say("You: hello")
        ui.stream("Let me think")
        rows = ui._transcript.lines(80)
        self.assertIn(("assistant-label", "AutoCode · writing…"), rows)
        self.assertEqual(("assistant-body", "  Let me think"), rows[-2])
        ui.stream(None)
        self.assertNotIn(("assistant-label", "AutoCode · writing…"), ui._transcript.lines(80))

    def test_an_interactive_command_gets_the_terminal_and_the_chat_comes_back(self):
        ui = screen_ui()
        with (
            mock.patch.object(tui.curses, "endwin", create=True) as endwin,
            mock.patch.object(tui.subprocess, "call", return_value=0) as call,
            mock.patch.object(tui.ChatTUI, "_bracketed_paste"),
            contextlib.redirect_stdout(io.StringIO()) as printed,
        ):
            self.assertEqual(0, ui.outside(["autocode", "connections", "login", "--provider", "opencode"], "Sign in"))
        endwin.assert_called_once()
        self.assertEqual(["autocode", "connections", "login", "--provider", "opencode"], call.call_args.args[0])
        ui._screen.clearok.assert_called_with(True)
        self.assertIn("Sign in: the chat comes back when it finishes.", printed.getvalue())


class TabBarTests(unittest.TestCase):
    ROWS = [
        {"number": 1, "run": "/p/r1", "title": "Add a flag", "state": "done", "current": False},
        {"number": 2, "run": "/p/r2", "title": "Fix the typo", "state": "needs you", "current": True},
    ]

    def test_ctrl_t_and_ctrl_arrows_are_the_tab_keys(self):
        self.assertEqual("new", tui.tab_step("\x14"))
        self.assertIsNone(tui.tab_step("t"))
        self.assertEqual("previous", tui.tab_step(tui.CsiTab("previous")))
        self.assertIsNone(tui.tab_step("["))
        with mock.patch.object(
            tui.curses, "keyname", create=True, side_effect=lambda key: {560: b"kRIT5", 545: b"kLFT5"}.get(key, b"KEY")
        ):
            self.assertEqual("next", tui.tab_step(560))
            self.assertEqual("previous", tui.tab_step(545))
            self.assertIsNone(tui.tab_step(400))

    def test_the_tab_bar_shows_once_there_are_two_chats(self):
        ui = screen_ui()
        drawn = []
        ui._put = lambda row, col, text, attr=0: drawn.append((row, text))
        layout = ui._layout(30, 120)
        ui.tabs(self.ROWS[:1])
        ui._header(layout)
        self.assertFalse(any("Add a flag" in text for _, text in drawn))
        ui.tabs(self.ROWS)  # draws the screen itself
        drawn.clear()
        ui._header(layout)
        row_one = [text for row, text in drawn if row == 1]
        self.assertEqual([" 1 Add a flag ✓ ", " 2 Fix the typo ! "], row_one)

    def test_tab_keys_wait_for_a_question(self):
        ui = screen_ui()
        acted = []
        ui.bind(lambda name, argument: acted.append((name, argument)) or "ok")
        self.assertIsNone(ui._tab_key("next", asking=False))
        self.assertEqual([], acted, "never leaves a step running unwatched")
        self.assertIn("Tabs switch once AutoCode asks you something", ui._transcript.blocks[-1][1])
        ui._tab_key("next", asking=True)
        self.assertEqual([("/tab", "next")], acted)

    def test_before_a_chat_ctrl_t_and_new_say_this_is_already_a_new_chat(self):
        """#1759: both used to answer as if /new could not run here, though the client has the reply."""
        for press in ("ctrl-t", "/new"):
            with self.subTest(press):
                ui = screen_ui()
                ui.bind(chat_client.History(Path(tempfile.gettempdir())).slash, run=False)
                if press == "ctrl-t":
                    self.assertIsNone(ui._tab_key("new", asking=True))
                else:
                    ui._composer.text = "/new"
                    self.assertIsNone(ui._enter(asking=True))
                self.assertEqual(
                    ("info", "/new\nThis is a new chat: type what you want done."), ui._transcript.blocks[-1]
                )
        ui = screen_ui()
        ui.bind(lambda name, argument: "ok", run=False)
        ui._tab_key("next", asking=True)
        self.assertIn("Tabs switch once a chat is open", ui._transcript.blocks[-1][1])

    def test_a_tab_ctrl_t_opened_can_be_left_before_its_request_is_sent(self):
        """#1997: the tab Ctrl-T made has no run, so the tab keys and /tab answered "Tabs switch once a chat
        is open" and the only way out was ctrl-d; nothing runs there, and the client serves /tab."""
        ui = screen_ui()
        acted = []
        ui.bind(lambda name, argument: acted.append((name, argument)) or "ok")  # a chat client's slash
        ui.switch(Path("/p"), None)  # /new or Ctrl-T: the local draft, no run yet
        self.assertFalse(ui._run_open)
        self.assertIsNone(ui._tab_key("next", asking=True))
        self.assertEqual([("/tab", "next")], acted)
        ui._composer.text = "/ta"
        self.assertEqual(["/tab"], [command.name for command in ui._menu(asking=True)], "the menu offers it")
        ui._composer.text = "/pl"
        self.assertEqual([], ui._menu(asking=True), "the other run-only commands still wait for a run")
        ui._composer.text = "/tab 1"
        self.assertIsNone(ui._enter(asking=True))
        self.assertEqual([("/tab", "next"), ("/tab", "1")], acted, "typed /tab works there too")
        # Before any chat (History bound, run=False) there is no other tab: the refusal stands.
        before = screen_ui()
        before.bind(lambda name, argument: "ok", run=False)
        before._tab_key("next", asking=True)
        self.assertIn("Tabs switch once a chat is open", before._transcript.blocks[-1][1])


class AskTests(unittest.TestCase):
    """The composer against a stand-in screen, without a terminal."""

    def setUp(self):
        for name in ("curs_set", "flushinp"):
            patcher = mock.patch.object(tui.curses, name, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def info(self, ui):
        return [text for role, text in ui._transcript.blocks if role == "info"]

    def test_leaving_the_run_picker_leaves_only_the_last_words_on_the_terminal(self):
        """#1755: the question marks where the client's last words start."""
        ui = screen_ui("\x04")
        for line in ("Runs waiting here:", "  1. r1  Add a flag", "  2. r2  Fix the typo"):
            ui.say(line)
        with self.assertRaises(EOFError):
            ui.ask("Pick one (1-2), or type a new request: ")
        ui.say("Nothing started.")
        self.assertEqual(["Nothing started."], ui._transcript.closing())

    def test_before_a_chat_is_open_the_menu_offers_what_works_without_one(self):
        ui = screen_ui()
        ui.bind(lambda name, argument: "ok", run=False)
        ui._composer.text = "/"
        self.assertEqual("/runs", ui._menu(True)[0].name)
        self.assertNotIn("/plan", [command.name for command in ui._menu(True)])

    def test_every_command_is_reachable_with_the_arrows_however_long_the_menu(self):
        ui = screen_ui()
        ui.bind(lambda name, argument: "ok")
        ui._asking, ui._composer.text = True, "/"
        menu = ui._menu(True)
        self.assertGreater(len(menu), tui.MENU_ROWS)
        drawn = []
        ui._put = lambda row, col, text, attr=0: drawn.append(text)
        layout = ui._layout(30, 120)
        ui._menu_index = len(menu) - 1  # the last entry, below the first window
        ui._command_menu(layout)
        self.assertTrue(any(menu[-1].name in text for text in drawn), "the window follows the choice")

    def test_a_question_after_work_rings_the_terminal_bell(self):
        ui = screen_ui(*"y\n", *"y\n")
        with mock.patch.object(tui.curses, "beep", create=True) as beep:
            ui.ask("First?")
            beep.assert_not_called()
            ui._launched = True  # a step ran since that question
            ui.ask("Approve this plan? [y/N]")
            beep.assert_called_once()

    def test_a_command_at_a_question_shows_its_answer_and_the_question_still_waits(self):
        ui = screen_ui(*"/tasks\n", *"y\n")
        ui.observe(
            {
                "progress": {
                    "tasks": {
                        "label": "0 of 1 task done",
                        "items": [{"number": 1, "label": "Write greet.py", "state": "waiting"}],
                    }
                }
            }
        )
        self.assertEqual("y", ui.ask("Approve this plan? [y/N]"))
        self.assertEqual(["/tasks\n0 of 1 task done\n· 1. Write greet.py"], self.info(ui))

    def test_enter_on_a_partly_typed_command_runs_the_highlighted_one(self):
        ui = screen_ui(*"/de", tui.curses.KEY_DOWN, tui.curses.KEY_UP, "\n", *"n\n")
        ui.observe({"explanation": {"explanation": "Waiting for your approval."}})
        self.assertEqual("n", ui.ask("Approve this plan? [y/N]"))
        self.assertEqual(1, len(self.info(ui)))
        self.assertTrue(self.info(ui)[0].startswith("/details\nWaiting for your approval."))
        self.assertIn("Core snapshot sampled at: unknown", self.info(ui)[0])

    def test_tab_completes_the_highlighted_command(self):
        ui = screen_ui()
        for key in "/he\t":
            ui._handle(key, asking=True)
        self.assertEqual("/help", ui._composer.text)

    def test_an_unknown_command_is_never_sent_as_a_reply_but_double_slash_is(self):
        ui = screen_ui(*"/approve\n", *"//etc is fine\n")
        self.assertEqual("/etc is fine", ui.ask("Anything else?"))
        self.assertEqual(
            ["/approve\n/approve is not a command. Type / to see the commands; // sends text that starts with /."],
            self.info(ui),
        )

    def test_while_autocode_works_pause_goes_to_the_client_and_ctrl_d_does_not_leave(self):
        ui = screen_ui("\x04", *"/pause\n")
        self.assertEqual("pause", ui.wait(5))
        self.assertIsNone(screen_ui(*"/quit\n").wait(0.3), "quit is not offered while AutoCode works")

    def test_text_typed_while_autocode_works_is_kept_not_sent(self):
        ui = screen_ui(*"hello\n")
        ui.observe({"liveness": {"kind": "supervised"}})
        self.assertIsNone(ui.wait(0.3))
        self.assertEqual("hello", ui._composer.text)
        self.assertEqual(
            ["AutoCode is working\nType / for commands. AutoCode asks here when it needs you."], self.info(ui)
        )

    def test_escape_closes_the_menu_and_the_next_key_is_kept(self):
        ui = screen_ui(*"/pl", "\x1b", "h", *"i\n")
        self.assertEqual("hi", ui.ask("Anything else?"), "the key after Esc is not swallowed")
        self.assertEqual([], self.info(ui))

    def test_a_sentence_after_an_unknown_command_is_never_sent(self):
        ui = screen_ui(*"/Build a greeting CLI\n", "\x15", *"Build it\n")
        self.assertEqual("Build it", ui.ask("What do you want done?"))
        self.assertIn("/build is not a command", self.info(ui)[0])

    def test_escape_cancels_a_command_question(self):
        ui = screen_ui(*"/undo 1\n", "\x1b", *"n\n")
        ui.bind(lambda name, argument: tui.panel.Confirm("Step 1.", "Go back to step 1?", lambda: "Done."))
        self.assertEqual("n", ui.ask("Continue? [y/N]"))
        self.assertEqual(["/undo\nStep 1.", "Go back to step 1?\nNothing changed."], self.info(ui))

    def test_unwinding_a_question_discards_its_pending_command_confirmation(self):
        ui = screen_ui(*"/undo 1\n")
        action = mock.Mock(return_value="Done.")
        ui.bind(lambda name, argument: tui.panel.Confirm("Step 1.", "Go back to step 1?", action))

        def resumed():
            if ui._pending is not None:
                raise chat_client.PromptWithdrawn

        ui.idle = resumed
        with self.assertRaises(chat_client.PromptWithdrawn):
            ui.ask("Which model should the Tester continue on?")
        ui._composer.reset()
        for key in "y\n":
            ui._handle(key, asking=False)
        action.assert_not_called()

    def test_diff_with_a_step_number_goes_to_the_client_and_its_answer_is_shown(self):
        ui = screen_ui(*"/diff 2\n", *"n\n")
        calls = []
        ui.bind(lambda name, argument: calls.append((name, argument)) or "1 file changed since that step")
        self.assertEqual("n", ui.ask("Continue? [y/N]"))
        self.assertEqual([("/diff", "2")], calls)
        self.assertEqual(["/diff\n1 file changed since that step"], self.info(ui))

    def test_undo_waits_for_a_yes_and_anything_else_changes_nothing(self):
        went: list[str] = []
        confirm = tui.panel.Confirm("Step 1: Builder step.", "Go back to step 1?", lambda: went.append("1") or "Done.")
        ui = screen_ui(*"/undo 1\n", *"no\n", *"/undo 1\n", *"y\n", *"n\n")
        ui.bind(lambda name, argument: confirm)
        self.assertEqual("n", ui.ask("Continue? [y/N]"), "the confirmation's answers never answer the question")
        self.assertEqual(["1"], went)
        self.assertEqual(
            [
                "/undo\nStep 1: Builder step.",
                "Go back to step 1?\nNothing changed.",
                "/undo\nStep 1: Builder step.",
                "Go back to step 1?\nDone.",
            ],
            self.info(ui),
        )

    def test_the_confirmation_is_the_question_shown_while_it_waits(self):
        ui = screen_ui(*"/undo 1\n")
        ui.bind(lambda name, argument: tui.panel.Confirm("Step 1.", "Go back to step 1?", lambda: "Done."))
        ui._asking, ui._prompt = True, "Continue? [y/N]"
        for key in "/undo 1\n":
            ui._handle(key, asking=True)
        self.assertEqual(["? Go back to step 1? [y/N]"], ui._layout(30, 120).question)

    def test_without_a_client_diff_is_not_offered(self):
        ui = screen_ui(*"/diff\n", *"n\n")
        self.assertEqual("n", ui.ask("Continue? [y/N]"))
        self.assertIn("is not available right now", self.info(ui)[0])

    def test_help_omits_run_commands_before_a_chat_is_open(self):
        ui = screen_ui()
        ui._run_open = False
        ui._run("/help", asking=True)
        help_text = self.info(ui)[-1]
        self.assertNotIn("/live", help_text)
        self.assertNotIn("/log", help_text)
        self.assertIn("/help", help_text)

    def test_keys_typed_while_autocode_worked_are_dropped_before_a_question(self):
        screen = mock.MagicMock()
        screen.getmaxyx.return_value = (30, 100)
        calls = []
        screen.get_wch.side_effect = lambda: calls.append("read") or "\n"
        with mock.patch.object(tui.ChatTUI, "_open"), mock.patch.object(tui.curses, "curs_set"):
            ui = tui.ChatTUI()
            ui._screen = screen
            with mock.patch.object(tui.curses, "flushinp", side_effect=lambda: calls.append("flush")):
                self.assertEqual("", ui.ask("Approve this plan? [y/N]"))
        self.assertEqual(["flush", "read"], calls)


class TallReplyAnchorTests(unittest.TestCase):
    """#2434: a plan carrying milestones and flow outgrows the body, and the person pages it."""

    def setUp(self):
        for name in ("curs_set", "flushinp"):
            patcher = mock.patch.object(tui.curses, name, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def ui(self, *keys, rows=20, cols=100):
        ui = screen_ui(*keys)
        ui._screen.getmaxyx.return_value = (rows, cols)
        return ui

    def shown(self, ui):
        """The body's visible rows: what the person reads, from the scroll state _body draws."""
        layout = ui._layout(*ui._size())
        lines = ui._transcript.lines(layout.body_width or layout.width)
        height = max(0, layout.body_end - layout.body_start)
        end = len(lines) - ui._scroll
        return " ".join(text for _, text in lines[max(0, end - height) : end])

    def test_a_tall_plan_keeps_its_head_at_its_question_despite_the_pointer_after_it(self):
        ui = self.ui("y", "\x03")  # Ctrl-C leaves the question's view in place: an answer would re-follow the tail
        ui.say("You: y")  # the prepare-plan answer: reading restarts here
        # Exactly what approve() says: render_plan's unprefixed text, the pointer, a change-set hint.
        ui.say(
            "\nPlan to approve:\n"
            "  [C1] Greeting contract holds\n"
            "      Checked by: Execute greeting and invalid-input regression checks\n"
            "  Milestone M1 (proves C1): Deliver and verify the greeting CLI\n"
            "Constraints:\n  - Python standard library only\n"
            "What it may do:\n  - Read and edit only this fixture Git workspace\n"
            "Not covered:\n  - Web service\n  - Deployment\n"
            "Steps in order:\n  1. Run greet.py with one name\n  2. Read Hello, NAME or a usage error"
        )
        ui.say("(Everything the plan records: autocode --show-goal --run-dir /p/.autocode/runs/r1)")
        ui.say(
            "This run is in a change set with api. /changeset shows every run's plan and approves "
            "them together; approving here releases this run alone."
        )
        with self.assertRaises(KeyboardInterrupt):
            ui.ask("Approve this plan? [y/N], or type what to change: ")
        shown = self.shown(ui)
        self.assertIn("Plan to approve:", shown, "the anchored first page opens at the plan's head")
        self.assertNotIn("Steps in order", shown, "the flow's steps are paged to, not on the plan's first page")
        self.assertNotIn("Run greet.py", shown)
        ui._handle(tui.curses.KEY_NPAGE, asking=True)
        paged = self.shown(ui)
        self.assertIn("Steps in order", paged, "PageDown walks forward through the plan")
        self.assertIn("Read Hello, NAME or a usage error", paged)
        self.assertNotIn("Plan to approve:", paged)
        ui._handle(tui.curses.KEY_PPAGE, asking=True)
        self.assertIn("Plan to approve:", self.shown(ui), "PageUp walks back to the plan's head")

    def test_a_plan_that_fits_is_not_scrolled_away_for_an_older_tall_summary(self):
        """At 80x24 the plan fit the body, so the question reached back to the requirements
        summary repeated after the person's replies and opened there, with the plan it asked
        about off screen."""
        ui = self.ui("y", "\x03", rows=24, cols=80)
        ui.say("You: y")
        ui.say("AutoCode: " + "\n".join(["Here is what I understand.", *[f"  behavior {n}" for n in range(1, 20)]]))
        ui.say("Working on your request…")
        ui.say(
            "\nPlan to approve:\n"
            "  [C1] Greeting contract holds\n"
            "      Checked by: Execute greeting and invalid-input regression checks\n"
            "  Milestone M1 (proves C1): Deliver and verify the greeting CLI\n"
            "Constraints:\n  - Python standard library only\n"
            "What it may do:\n  - Read and edit only this fixture Git workspace"
        )
        with self.assertRaises(KeyboardInterrupt):
            ui.ask("Approve this plan? [y/N], or type what to change: ")
        shown = self.shown(ui)
        self.assertIn("Plan to approve:", shown)
        self.assertIn("Read and edit only this fixture Git workspace", shown, "the whole plan is on screen")
        self.assertNotIn("Here is what I understand.", shown)

    def test_a_short_acknowledgement_after_a_tall_listing_stays_visible(self):
        """The attached-viewer pause bug: a tall listing must not pin the view over the
        acknowledgement said after it, once no question asks for the listing's head."""
        ui = self.ui()
        ui.say("You: /pause")
        ui.say("AutoCode: " + "\n".join(["head of the listing", *[f"  row {number}" for number in range(1, 25)]]))
        ui.say(
            "Pausing after this step. This viewer does not interrupt the external worker."
        )  # unprefixed, as request_pause says it
        shown = self.shown(ui)
        self.assertIn("external worker", shown)
        self.assertNotIn("head of the listing", shown, "the tall listing is paged to, not pinned over the answer")

    def test_the_persons_paged_place_survives_a_new_block(self):
        ui = self.ui()
        ui.say("AutoCode: " + "\n".join([f"row {number}" for number in range(1, 25)]))
        ui._scroll_by(-ui._page())  # the person pages forward from the anchored top, part way
        self.assertFalse(ui._anchored)
        ui.say("AutoCode: a new short block")
        shown = self.shown(ui)
        self.assertIn("row 10", shown, "their window holds")
        self.assertNotIn("a new short block", shown, "a new block does not pull the person's view away")

    def test_an_old_tall_block_the_person_answered_is_not_reached_back_for(self):
        ui = self.ui("y", "\x03")
        ui.say("AutoCode: " + "\n".join(["first line of the understanding", *["understanding"] * 20]))
        ui.say("You: y")
        ui.say("AutoCode: short follow-up")
        with self.assertRaises(KeyboardInterrupt):
            ui.ask("Approve this plan? [y/N]")
        shown = self.shown(ui)
        self.assertIn("short follow-up", shown)
        self.assertNotIn("first line of the understanding", shown, "the person answered it; the tail is followed")

    def test_a_listing_the_person_asked_for_is_theirs_to_page_not_the_questions_anchor(self):
        """The CI failure's shape: /details' tall listing is the person's own command result;
        neither its reply's tail nor a later question reaches back into it."""
        ui = self.ui("y", "\x03")
        ui.say("You: /pause")
        ui._transcript.info(
            "/details", "\n".join(["head of the listing", *[f"  row {number}" for number in range(1, 25)]])
        )
        ui.say(
            "Pausing after this step. This viewer does not interrupt the external worker."
        )  # unprefixed, as request_pause says it
        with self.assertRaises(KeyboardInterrupt):
            ui.ask("Anything else? ")
        shown = self.shown(ui)
        self.assertIn("external worker", shown)
        self.assertNotIn("head of the listing", shown, "a listing is paged, never pinned by a question")

    def test_running_pause_from_a_paged_place_shows_its_answer(self):
        ui = self.ui()
        ui.say("AutoCode: " + "\n".join([f"row {number}" for number in range(1, 25)]))
        ui._scroll_by(-ui._page())  # the person pages forward from the anchored top, part way
        self.assertGreater(ui._scroll, 0)
        ui._run("/pause", asking=False)
        self.assertEqual((0, False), (ui._scroll, ui._anchored), "a command the person ran answers at the tail")

    def test_tab_switch_carries_scroll_ownership_with_the_position(self):
        """A tab's paged place and the chat's own anchor are one display context: neither
        leaks across a switch to change how the next block is shown."""
        ui = self.ui("y", "\x03")
        ui.observe({"workspace": "/p", "run_dir": "/p/.autocode/runs/r1"})
        ui.say("You: y")
        ui.say("AutoCode: " + "\n".join([f"row {number}" for number in range(1, 25)]))
        with self.assertRaises(KeyboardInterrupt):
            ui.ask("Approve this plan? [y/N]")
        self.assertTrue(ui._anchored, "the reply's tall block anchored this tab")
        ui.switch(Path("/p"), Path("/p/.autocode/runs/r2"))
        self.assertEqual((0, False), (ui._scroll, ui._anchored), "a fresh tab follows the tail")
        ui.say("AutoCode: " + "\n".join([f"line {number}" for number in range(1, 25)]))
        ui._scroll_by(-ui._page())  # the person pages this tab forward, owning its view
        self.assertFalse(ui._anchored)
        ui.switch(Path("/p"), Path("/p/.autocode/runs/r1"))
        self.assertTrue(ui._anchored, "the first tab's own anchor survived the switch")
        ui.say("AutoCode: short follow-up")
        self.assertEqual(0, ui._scroll, "the anchored tab returns to the tail for a short block")
        ui.switch(Path("/p"), Path("/p/.autocode/runs/r2"))
        self.assertFalse(ui._anchored, "the paged tab kept the person's ownership")
        self.assertGreater(ui._scroll, 0, "the paged tab kept its place")


class WindowTests(unittest.TestCase):
    def test_short_text_keeps_the_cursor_column(self):
        self.assertEqual(("hello", 5), tui.window("hello", 5, 10))

    def test_long_text_scrolls_to_keep_the_cursor_visible(self):
        self.assertEqual(("world", 5), tui.window("hello world", 11, 6))

    def test_wide_characters_are_not_split(self):
        self.assertEqual(("世界", 4), tui.window("你好世界", 4, 5))


class LivePaneTests(unittest.TestCase):
    """The live log under the conversation, drawn while the composer still takes typing."""

    VIEW = {
        "chat": {
            "log": [
                {"id": "a:r1", "agent": "Builder · task M1", "kind": "thinking", "text": "Read greet.py first"},
                {"id": "a:e1", "agent": "Builder · task M2", "kind": "edit", "text": "edited greet.py"},
                {
                    "id": "b:c1",
                    "agent": "Builder · task M2",
                    "kind": "command",
                    "text": "pytest -q",
                    "status": "failed",
                },
                {"id": "c:m1", "agent": "Plan Reviewer", "kind": "message", "text": "Check the output."},
            ]
        }
    }

    def drawn(self, ui, rows=30, cols=120):
        ui._screen.getmaxyx.return_value = (rows, cols)
        puts = []
        ui._put = lambda row, col, text, attr=0: puts.append((row, col, text))
        ui._draw()
        return puts

    def test_the_pane_shows_each_agent_above_the_composer(self):
        ui = screen_ui()
        ui.observe(self.VIEW)
        puts = self.drawn(ui)
        layout = ui._layout(30, 120)
        self.assertIsNotNone(layout.live_start)
        self.assertLess(layout.body_end, layout.panel_end)
        self.assertLess(layout.panel_end, layout.box_top, "the composer stays below the pane")
        texts = [text for row, _, text in puts if layout.live_start <= row < layout.panel_end]
        self.assertTrue(any("LIVE · all agents" in text for text in texts))
        self.assertIn("Builder · task M1", texts)
        self.assertIn("Builder · task M2", texts)
        self.assertTrue(any("Read greet.py first" in text for text in texts))
        self.assertTrue(any("edited greet.py" in text for text in texts))
        self.assertTrue(any("Check the output." in text for text in texts))
        self.assertFalse(any("pytest -q" in text for text in texts))

    def test_typing_while_autocode_works_keeps_the_pane(self):
        ui = screen_ui(*"/pl")
        ui.observe(self.VIEW)
        self.assertIsNone(ui.wait(0.3))
        self.assertEqual("/pl", ui._composer.text)
        self.assertIsNotNone(ui._layout(30, 120).live_start)

    def test_without_agent_work_or_on_a_short_terminal_there_is_no_pane(self):
        ui = screen_ui()
        self.assertIsNone(ui._layout(30, 120).live_start)
        ui.observe(self.VIEW)
        self.assertIsNone(ui._layout(20, 120).live_start)

    def test_live_hides_shows_and_filters_the_pane(self):
        ui = screen_ui()
        ui.observe(self.VIEW)
        ui._run("/live", False)
        self.assertIsNone(ui._layout(30, 120).live_start)
        ui._run("/live", False, "M2")
        self.assertIsNotNone(ui._layout(30, 120).live_start)
        self.assertEqual(["Builder · task M2"], [entry["agent"] for entry in ui._live.shown()])
        ui._run("/live", False, "all")
        self.assertEqual(3, len(ui._live.shown()))
        ui._run("/live", False, "off")
        self.assertIsNone(ui._layout(30, 120).live_start)

    def test_where_the_pane_does_not_fit_live_prints_the_log_in_the_conversation(self):
        ui = screen_ui()
        ui._screen.getmaxyx.return_value = (14, 120)
        ui.observe(self.VIEW)
        ui._run("/live", False, "all")
        [(role, text)] = ui._transcript.blocks[-1:]
        self.assertEqual("info", role)
        self.assertIn("Builder · task M1 ∴ Read greet.py first", text)
        self.assertNotIn("pytest -q", text)

    def test_long_entries_wrap_under_their_agent_and_never_carry_escapes(self):
        ui = screen_ui()
        ui.observe({"chat": {"log": [{"id": "a", "agent": "Builder", "kind": "message", "text": "word \x1b[2J" * 20}]}})
        rows = ui.live_rows(40)
        self.assertEqual("Builder", rows[0][0][0])
        self.assertTrue(all(text.startswith("  ") for row in rows[1:] for text, _ in row))
        self.assertFalse(any("\x1b" in text for row in rows for text, _ in row))

    def test_opening_another_chat_starts_its_log_afresh(self):
        ui = screen_ui()
        ui.observe(self.VIEW)
        ui.switch(Path("/p"), Path("/p/.autocode/runs/r2"))
        self.assertEqual({}, ui._live.entries)


if __name__ == "__main__":
    unittest.main()


class AttentionBadgeTests(unittest.TestCase):
    def test_the_unavailable_badge_names_the_reason(self):
        ui = screen_ui()
        ui.attention({"error": "`autocode attention --json` failed: exit 3"})
        puts = []
        ui._put = lambda row, col, text, attr=0: puts.append((row, col, text))
        ui._draw()
        header = next(text for row, _, text in puts if "F8" in text)
        self.assertIn("F8: alerts unavailable:", header)
        self.assertIn("autocode attention --json", header)

    def test_counts_still_show_without_an_error(self):
        ui = screen_ui()
        ui.attention({"unread": 2})
        puts = []
        ui._put = lambda row, col, text, attr=0: puts.append((row, col, text))
        ui._draw()
        header = next(text for row, _, text in puts if "F8" in text)
        self.assertIn("F8: 2 new alerts", header)
        self.assertNotIn("unavailable", header)


class StartingStatusTests(unittest.TestCase):
    """A launched run with no view yet is starting, not stuck reading status."""

    def drawn_status(self, ui):
        puts = []
        ui._put = lambda row, col, text, attr=0: puts.append((row, col, text))
        ui._draw()
        return [text for _, _, text in puts]

    def test_a_launched_run_says_it_waits_for_the_first_view(self):
        ui = screen_ui()
        ui.worker_launch({"execution": {"mode": "owned"}})
        ui._launched = True
        self.assertIn("Run started; waiting for its first status view", " ".join(self.drawn_status(ui)))

    def test_without_a_launch_it_still_reads_status(self):
        ui = screen_ui()
        self.assertIn("Reading run status", " ".join(self.drawn_status(ui)))

    def test_once_a_view_arrives_the_line_reports_the_run(self):
        ui = screen_ui()
        ui.observe({"status": "RUNNING", "progress": {"headline": "Requirements working"}})
        self.assertIn("Requirements working", " ".join(self.drawn_status(ui)))


def drawn(ui, rows=30, cols=120):
    """Every string the screen was asked to draw, by row."""
    ui._screen.getmaxyx.return_value = (rows, cols)
    puts = []
    ui._put = lambda row, col, text, attr=0: puts.append((row, text))
    ui._draw()
    return puts


def row_text(puts, number):
    return " ".join(text for row, text in puts if row == number)


class NewChatScreenTests(unittest.TestCase):
    """A new chat before anything is sent: a welcome, and no loading noise that reads as a fault."""

    def new_chat(self):
        ui = screen_ui()
        ui.set_context(Path("/p/project"))
        ui.say("New chats: Full · Codex. /mode and /engine change them.")
        return ui

    def test_the_welcome_shows_under_the_chats_settings(self):
        shown = " ".join(text for _, text in drawn(self.new_chat()))
        self.assertIn(tui.TAGLINE, shown)
        self.assertIn("Try asking:", shown)
        self.assertIn(tui.EXAMPLES[0], shown)
        self.assertIn("New chats: Full · Codex", shown)

    def test_the_welcome_leaves_once_the_person_has_spoken(self):
        ui = self.new_chat()
        ui.say("You: fix the typo")
        self.assertNotIn("Try asking:", " ".join(text for _, text in drawn(ui)))

    def test_a_new_chat_has_no_status_to_read_and_no_alerts_loading_badge(self):
        ui = self.new_chat()
        ui.attention({"unread": 0, "unknown": 0, "error": "Reading saved attention", "loading": True})
        puts = drawn(ui)
        self.assertEqual("New chat", row_text(puts, 2).strip())
        self.assertNotIn("F8", row_text(puts, 0))
        self.assertNotIn("unavailable", " ".join(text for _, text in puts))

    def test_a_just_launched_run_that_cannot_be_read_yet_is_starting(self):
        ui = self.new_chat()
        ui._launched, ui._launched_at = True, tui.time.monotonic()
        ui.status_unavailable("no state.json yet")
        shown = " ".join(text for _, text in drawn(ui))
        self.assertIn(tui.STARTING, shown)
        self.assertNotIn("Status unavailable", shown)

    def test_a_run_unreadable_past_its_start_reports_the_outage(self):
        ui = self.new_chat()
        ui._launched, ui._launched_at = True, tui.time.monotonic() - tui.STARTUP_GRACE_SECONDS - 1
        ui.status_unavailable("no state.json yet")
        self.assertIn("Status unavailable", " ".join(text for _, text in drawn(ui)))


class SpeakerTests(unittest.TestCase):
    def test_the_clients_own_words_after_a_reply_are_autocodes_turn(self):
        transcript = tui.Transcript()
        transcript.add("New chats: Full · Codex.")  # before anyone speaks: a plain settings line
        transcript.add("You: y")
        transcript.add("Prepare a reviewed plan?\nPlanning uses your selected models.")
        transcript.add("Plan to approve:\n  [C1] Greeting")
        transcript.add("You: y")
        self.assertEqual(
            [
                ("plain", "New chats: Full · Codex."),
                ("plain", ""),
                ("user-label", "You"),
                ("user-body", "  y"),
                ("plain", ""),
                ("assistant-label", "AutoCode"),
                ("plain", "  Prepare a reviewed plan?"),
                ("plain", "  Planning uses your selected models."),
                ("plain", ""),
                ("plain", "  Plan to approve:"),
                ("plain", "    [C1] Greeting"),
                ("plain", ""),
                ("user-label", "You"),
                ("user-body", "  y"),
                ("plain", ""),
            ],
            transcript.lines(80),
        )


class AttentionShownChatTests(unittest.TestCase):
    def test_the_badge_leaves_out_the_chat_on_screen(self):
        ui = screen_ui()
        ui.attention({"unread": 1, "shown_unread": 1})
        self.assertNotIn("F8", row_text(drawn(ui), 0))
        ui.attention({"unread": 2, "shown_unread": 1})
        self.assertIn("F8: 1 new alert", row_text(drawn(ui), 0))


SPENDING_VIEW = {
    "status": "RUNNING",
    "progress": {"headline": "Builder working"},
    "cost": {"spent_usd": 0.42, "complete": True},
    "usage": {
        "accounting": {
            "tokens": {
                "input_tokens": {"value": 150000, "complete": True},
                "output_tokens": {"value": 34000, "complete": True},
            },
            "attempts": [
                {"stage": "terra", "job": "Builder", "exit_code": 1, "duration_seconds": 40},
                {"stage": "terra", "job": "Builder", "active": True, "duration_seconds": 20},
            ],
        }
    },
}


class MeterScreenTests(unittest.TestCase):
    def test_the_header_shows_the_runs_cost_and_tokens(self):
        ui = screen_ui()
        ui.observe(SPENDING_VIEW)
        self.assertIn("$0.42 · 184k tokens", row_text(drawn(ui), 0))

    def test_with_two_chats_the_header_adds_the_session_total(self):
        ui = screen_ui()
        ui.observe(SPENDING_VIEW)
        other = {
            "spent_usd": 1.0,
            "inherited_usd": 0,
            "input_tokens": 1000,
            "output_tokens": 0,
            "cost_complete": True,
            "tokens_complete": True,
        }
        ui.tabs(
            [
                {"number": 1, "run": "/p/r1", "title": "Other", "state": "done", "current": False, "total": other},
                {"number": 2, "run": "/p/r2", "title": "This", "state": "working", "current": True, "total": None},
            ]
        )
        self.assertIn("chat $0.42 · 184k tokens · session $1.42 · 185k tokens", row_text(drawn(ui, cols=140), 0))

    def test_a_struggling_stage_takes_the_status_row(self):
        ui = screen_ui()
        ui.observe(SPENDING_VIEW)
        puts = drawn(ui)
        status = next(text for _, text in puts if "attempt 2" in text and "Builder" in text and "exit 1" in text)
        self.assertIn("last one exit 1", status)
        self.assertIn("STAGES  $0.42 · 184k tokens", " ".join(text for _, text in puts))


class ClosingSummaryTests(unittest.TestCase):
    def test_a_finished_run_names_the_files_it_changed(self):
        shown = {
            "code_checkpoints": {
                "rows": [{"changed_files": ["greet.py", "README.md"]}, {"changed_files": ["greet.py"]}]
            }
        }
        self.assertEqual(
            "Changed 2 files: greet.py, README.md. /diff shows what changed.", tui.panel.finished_text(shown)
        )
        self.assertEqual("", tui.panel.finished_text({}))
        # Live: the acceptance tests the Tester wrote are not a Builder step, but the run changed them too.
        with_tests = {**shown, "acceptance_tests": {"files": ["test_greet.py", "greet.py"]}}
        self.assertEqual(
            "Changed 3 files: greet.py, README.md, test_greet.py. /diff shows what changed.",
            tui.panel.finished_text(with_tests),
        )


class DistinctStyles(dict):
    """A stand-in style table that gives every style its own attribute, so a test can tell them apart."""

    def get(self, key, default=None):
        return self.setdefault(key, len(self) + 1)


def styled(ui, rows=30, cols=120):
    """Every string drawn, with its row, column and attribute; each style draws as its own attribute."""
    ui._styles = DistinctStyles()
    ui._screen.getmaxyx.return_value = (rows, cols)
    puts = []
    ui._put = lambda row, col, text, attr=0: puts.append((row, col, text, attr))
    ui._draw()
    return puts


class MessageFormattingTests(unittest.TestCase):
    """A message's code blocks, `code` and **bold**, and a /diff's lines, are drawn as what they are."""

    def test_a_fenced_block_is_drawn_as_code_under_a_rule_naming_its_language(self):
        rows = tui.body_rows(
            "assistant-body", "Run it:\n```sh\npython3 greet.py Ada\n```\nThat prints the greeting.", 60
        )
        self.assertEqual(
            [
                ("assistant-body", "  Run it:"),
                ("code-fence", "  sh " + "─" * 37),
                ("code", "  python3 greet.py Ada"),
                ("code-fence", "  " + "─" * 40),
                ("assistant-body", "  That prints the greeting."),
            ],
            rows,
        )

    def test_without_a_fence_a_message_keeps_its_style_and_lines(self):
        self.assertEqual(
            [("assistant-body", "  one"), ("assistant-body", "    two")],
            tui.body_rows("assistant-body", "one\n  two", 60),
        )

    def test_a_diffs_patch_is_colored_by_line_but_not_the_words_before_it(self):
        text = (
            "1 file changed since step 1:\n  greet.py\n\n"
            "diff --git a/greet.py b/greet.py\n--- a/greet.py\n+++ b/greet.py\n@@ -1 +1 @@\n-old\n+new\n same"
        )
        styles = [style for style, _ in tui.body_rows("info-body", text, 60, diff=True)]
        self.assertEqual(
            ["info-body"] * 3 + ["diff-file"] * 3 + ["diff-hunk", "diff-del", "diff-add", "info-body"], styles
        )
        listing = "1. Builder step\n- not a patch line"
        self.assertEqual(["info-body"] * 2, [style for style, _ in tui.body_rows("info-body", listing, 60, diff=True)])

    def test_inline_code_and_bold_lose_their_marks(self):
        self.assertEqual(
            [("text", "Run "), ("code", "/diff 2"), ("text", " for "), ("bold", "the change"), ("text", ".")],
            tui.inline_pieces("Run `/diff 2` for **the change**."),
        )
        self.assertEqual([("text", "a * b and `unclosed")], tui.inline_pieces("a * b and `unclosed"))

    def test_the_screen_draws_inline_code_in_the_code_style(self):
        ui = screen_ui()
        ui.say("AutoCode: Type `/diff 1` to see **what changed**.")
        puts = styled(ui)
        code = next(put for put in puts if put[2] == "/diff 1")
        bold = next(put for put in puts if put[2] == "what changed")
        self.assertEqual(ui._style("code-inline"), code[3])
        self.assertEqual(ui._style("assistant-body") | tui.curses.A_BOLD, bold[3])
        self.assertFalse(any("`" in put[2] or "**" in put[2] for put in puts if put[0] == code[0]))
        line = sorted(put for put in puts if put[0] == code[0])
        self.assertEqual("  Type /diff 1 to see what changed.", "".join(put[2] for put in line))

    def test_the_screen_draws_a_diffs_added_and_removed_lines_in_their_colors(self):
        ui = screen_ui()
        ui._transcript.info("/diff", "1 file changed since step 1:\n  greet.py\n\n@@ -1 +1 @@\n-old line\n+new line")
        puts = {text.strip(): attr for _, _, text, attr in styled(ui)}
        self.assertEqual(ui._style("diff-add"), puts["+new line"])
        self.assertEqual(ui._style("diff-del"), puts["-old line"])
        self.assertEqual(ui._style("diff-hunk"), puts["@@ -1 +1 @@"])
        self.assertEqual(ui._style("info-body"), puts["greet.py"])

    def test_what_the_person_typed_is_shown_as_typed(self):
        ui = screen_ui()
        ui.say("You: keep `these` **marks**")
        self.assertIn("  keep `these` **marks**", [text.rstrip() for _, _, text, _ in styled(ui)])


class SidePanelWrapTests(unittest.TestCase):
    """The side panel's requirements and tasks wrap to a second row instead of being cut at its edge."""

    VIEW = {
        "status": "RUNNING",
        "progress": {
            "headline": "Builder working",
            "requirements": {
                "label": "0 of 1 requirement checked",
                "items": [
                    {
                        "id": "AC1",
                        "label": "Given the command python3 greet.py Alice, when it runs, then stdout is exactly "
                        "Hello, Alice followed by one newline, stderr is empty, and the exit code is 0.",
                        "state": "unchecked",
                    }
                ],
            },
        },
    }

    def test_a_long_requirement_takes_two_rows_and_says_where_it_was_cut(self):
        self.assertEqual(["one two", "three"], tui.wrapped_rows("one two three", 8, 2))
        self.assertEqual(["one two", "three f…"], tui.wrapped_rows("one two three four", 8, 2))
        ui = screen_ui()
        ui.observe(self.VIEW)
        layout = ui._layout(30, 120)
        panel_rows = [text for row, col, text in [(r, c, t) for r, c, t, _ in styled(ui)] if col == layout.panel_col]
        first = next(index for index, text in enumerate(panel_rows) if "AC1 Given the command" in text)
        self.assertTrue(panel_rows[first + 1].startswith("  "), panel_rows[first : first + 2])
        self.assertTrue(panel_rows[first + 1].endswith("…"), panel_rows[first + 1])
