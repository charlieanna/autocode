"""The full-screen chat's pure pieces: width, wrapping, transcript blocks, composer, window."""

import unittest
from unittest import mock

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


class AskTests(unittest.TestCase):
    """The composer against a stand-in screen, without a terminal."""

    def setUp(self):
        for name in ("curs_set", "flushinp"):
            patcher = mock.patch.object(tui.curses, name, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def info(self, ui):
        return [text for role, text in ui._transcript.blocks if role == "info"]

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
        self.assertEqual(["/details\nWaiting for your approval."], self.info(ui))

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
        self.assertIsNone(ui.wait(0.3))
        self.assertEqual("hello", ui._composer.text)
        self.assertEqual(
            ["AutoCode is working\nType / for commands. AutoCode asks here when it needs you."], self.info(ui)
        )

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


class WindowTests(unittest.TestCase):
    def test_short_text_keeps_the_cursor_column(self):
        self.assertEqual(("hello", 5), tui.window("hello", 5, 10))

    def test_long_text_scrolls_to_keep_the_cursor_visible(self):
        self.assertEqual(("world", 5), tui.window("hello world", 11, 6))

    def test_wide_characters_are_not_split(self):
        self.assertEqual(("世界", 4), tui.window("你好世界", 4, 5))


if __name__ == "__main__":
    unittest.main()
