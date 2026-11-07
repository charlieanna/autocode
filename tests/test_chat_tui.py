"""The full-screen chat's pure pieces: width, wrapping, transcript blocks, composer, window."""

import contextlib
import io
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
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
    def test_a_working_agents_newest_thought_shows_live_and_folds_when_its_stage_ends(self):
        # #2699: while a stage runs, its newest thought is one live line under the conversation; when the
        # agent leaves the view its thinking folds into one line of history that points at /live.
        transcript = tui.Transcript()
        transcript.add("You: Fix the export.")

        def thought(identifier, text, agent="Planner"):
            return {"id": identifier, "agent": agent, "kind": "thinking", "text": text}

        transcript.progress({"chat": {"log": [thought("t1", "Reading the export module [partial]")]}})
        rows = transcript.lines(80)
        self.assertIn(("info-label", "Planner · thinking"), rows)
        self.assertIn(("live-thinking", "  Reading the export module [partial]"), rows)
        transcript.progress(
            {"chat": {"log": [thought("t1", "Reading the export module"), thought("t2", "The *writer* skips Safari")]}}
        )
        rows = transcript.lines(80)
        self.assertEqual(1, sum(1 for style, _ in rows if style == "live-thinking"), "one live line, the newest")
        self.assertIn(("live-thinking", "  The writer skips Safari"), rows)
        self.assertNotIn("folded", str(rows))
        # The stage ends: the Planner is no longer in the view's log.
        transcript.progress({"chat": {"log": []}})
        rows = transcript.lines(80)
        self.assertEqual(
            ("info", "Planner · thinking\n▸ 2 thoughts folded; /live Planner shows them"), transcript.blocks[-1]
        )
        self.assertEqual(0, sum(1 for style, _ in rows if style == "live-thinking"))
        # A second reading with the same empty log adds no second fold.
        transcript.progress({"chat": {"log": []}})
        self.assertEqual(1, sum(1 for _, text in transcript.blocks if "folded" in text))
        # The Builder's thinking opens its own section; a streaming receipt is not a thought.
        transcript.progress(
            {
                "chat": {
                    "log": [
                        thought("b1", "Adding the test", agent="Builder"),
                        {
                            "id": "b2",
                            "agent": "Builder",
                            "kind": "thinking",
                            "text": "Reasoning stream activity received; text unavailable.",
                            "status": "streaming",
                        },
                    ]
                }
            }
        )
        rows = transcript.lines(80)
        self.assertIn(("info-label", "Builder · thinking"), rows)
        self.assertIn(("live-thinking", "  Adding the test"), rows)

    def test_dismissing_inventory_retains_the_current_question_at_close(self):
        transcript = tui.Transcript()
        transcript.add("You: Original request.")
        transcript.show_listing("Temporary picker")
        transcript.asked = len(transcript.blocks)
        transcript.add("AutoCode: Current clarification.")
        transcript.dismiss_listing()
        self.assertEqual(["AutoCode: Current clarification."], transcript.closing())

    def test_inventory_replacement_and_dismissal_preserve_conversation_and_progress_identity(self):
        transcript = tui.Transcript()
        transcript.add("You: Keep /campaign/user-text and changeset-example.")
        first = {"id": "first", "agent": "Planner", "kind": "message", "text": "First update."}
        transcript.progress({"chat": {"log": [first]}})
        transcript.show_listing("Old listing")
        transcript.progress(
            {"chat": {"log": [{"id": "second", "agent": "Planner", "kind": "message", "text": "Second update."}]}}
        )
        transcript.show_listing("New listing")
        self.assertNotIn("Old listing", str(transcript.lines(120)))
        self.assertIn("New listing", str(transcript.lines(120)))
        transcript.dismiss_listing()
        transcript.progress({"chat": {"log": [{**first, "text": "Updated first."}]}})
        shown = str(transcript.lines(120))
        self.assertNotIn("listing", shown)
        self.assertNotIn("First update.", shown)
        self.assertEqual(1, shown.count("Updated first."))
        self.assertEqual(1, shown.count("Second update."))
        self.assertIn("/campaign/user-text and changeset-example", shown)

    def test_only_requested_adjacent_info_repeats_are_coalesced(self):
        transcript = tui.Transcript()
        transcript.info("Working", "Your draft is kept.", coalesce=True)
        transcript.info("Working", "Your draft is kept.", coalesce=True)
        self.assertEqual(1, len(transcript.blocks))
        transcript.add("AutoCode: New progress.")
        transcript.info("Working", "Your draft is kept.", coalesce=True)
        self.assertEqual(3, len(transcript.blocks))
        transcript.info("Working", "Your draft is kept.")
        self.assertEqual(4, len(transcript.blocks), "ordinary command results still repeat when requested")

    def test_progress_messages_share_the_conversation_and_update_by_identity(self):
        transcript = tui.Transcript()
        transcript.add("You: Fix the history.")
        row = {"id": "attempt:message", "agent": "Planner", "kind": "message", "text": "Checking the registry."}
        transcript.progress({"chat": {"log": [row]}})
        transcript.add("You: Preserve the existing result mappings.")
        transcript.progress({"chat": {"log": [{**row, "text": "Checked the registry mappings."}]}})
        transcript.progress({"chat": {"log": [{**row, "text": "Checked the registry mappings."}]}})
        self.assertEqual(3, len(transcript.blocks))
        shown = "\n".join(text for _, text in transcript.lines(100))
        self.assertEqual(1, shown.count("Checked the registry mappings."))
        self.assertLess(shown.index("Checked the registry mappings."), shown.index("Preserve the existing"))

    def test_progress_excludes_tool_reasoning_and_transport_counters(self):
        transcript = tui.Transcript()
        transcript.progress(
            {
                "chat": {
                    "log": [
                        {"id": "tool", "agent": "Planner", "kind": "command", "text": "bash hidden"},
                        {"id": "thought", "agent": "Planner", "kind": "thinking", "text": "hidden thought"},
                        {
                            "id": "counter",
                            "agent": "Planner",
                            "kind": "message",
                            "text": "writing Bash input: 25 chars",
                        },
                        {
                            "id": "stream",
                            "agent": "Planner",
                            "kind": "message",
                            "text": "Response stream activity received; text unavailable.",
                            "status": "streaming",
                        },
                    ]
                }
            }
        )
        self.assertEqual([], transcript.blocks)

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

    def test_leaving_plan_approval_does_not_dump_already_displayed_plans(self):
        transcript = tui.Transcript()
        transcript.add("You: fix the history")
        transcript.add("AutoCode: Here are the coordinated plans.")
        transcript.add("Plan to approve:\n" + "\n".join(f"Criterion {n}" for n in range(100)))
        transcript.asked = len(transcript.blocks)
        self.assertEqual([], transcript.closing())
        transcript.add("Left the chat; the run is unchanged.")
        transcript.add("Run autocode chat here to pick it up again.")
        self.assertEqual(
            ["Left the chat; the run is unchanged.", "Run autocode chat here to pick it up again."],
            transcript.closing(),
        )

    def test_a_new_completion_message_after_the_question_is_retained(self):
        transcript = tui.Transcript()
        transcript.add("AutoCode: The plan to approve.")
        transcript.asked = len(transcript.blocks)
        self.assertEqual([], transcript.closing())
        transcript.add("You: y")
        transcript.add("AutoCode: The requested task is complete.")
        self.assertEqual(["AutoCode: The requested task is complete."], transcript.closing())

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


class MultiLineComposerTests(unittest.TestCase):
    """The reply box shows the whole reply over several rows, as Claude Code's terminal does."""

    def test_rows_wrap_at_the_width_and_at_line_breaks_with_the_cursor_placed(self):
        self.assertEqual((["hello", " worl", "d"], 2, 1), tui.composer_rows("hello world", 11, 5, 6))
        self.assertEqual((["ab", "cd"], 0, 2), tui.composer_rows("ab\ncd", 2, 10, 6))
        self.assertEqual((["abcde", ""], 1, 0), tui.composer_rows("abcde", 5, 5, 6), "a full row moves the cursor on")
        self.assertEqual((["你好", "世界"], 1, 4), tui.composer_rows("你好世界", 4, 5, 6))

    def test_only_the_rows_around_the_cursor_are_kept(self):
        self.assertEqual((["c", "d"], 1, 1), tui.composer_rows("a\nb\nc\nd", 7, 10, 2))
        self.assertEqual((["a", "b"], 0, 0), tui.composer_rows("a\nb\nc\nd", 0, 10, 2))

    def test_up_and_down_move_between_lines_before_walking_history(self):
        composer = tui.Composer()
        composer.history = ["older"]
        composer.insert("first line\nsecond")
        self.assertTrue(composer.line_up())
        self.assertEqual(6, composer.cursor, "the column is kept")
        self.assertFalse(composer.line_up())
        self.assertTrue(composer.line_down())
        self.assertEqual(len(composer.text), composer.cursor)
        self.assertFalse(composer.line_down())

    def drawn(self, *keys):
        ui = screen_ui(*keys)
        ui._asking, ui._prompt = True, "Your reply"
        for key in keys:
            ui._handle(key, asking=True)
        rows = {}
        ui._put = lambda row, col, text, attr=0: rows.setdefault(row, []).append((col, text))
        layout = ui._layout(30, 120)
        ui._composer_box(layout)
        return ui, layout, rows

    def test_a_long_reply_grows_the_box_instead_of_scrolling_one_line(self):
        reply = "word " * 60
        ui, layout, rows = self.drawn(*reply)
        self.assertGreater(layout.box_rows, 1)
        self.assertEqual(layout.box_bottom, layout.box_text + layout.box_rows)
        shown = "".join(
            text for row in range(layout.box_text, layout.box_bottom) for col, text in rows[row] if col == 4
        )
        self.assertEqual(reply, shown, "every typed character is on screen")

    def test_backslash_enter_starts_a_new_line_and_enter_then_sends(self):
        ui, layout, rows = self.drawn(*"one\\", "\r", *"two")
        self.assertEqual("one\ntwo", ui._composer.text)
        self.assertEqual(2, layout.box_rows)
        self.assertEqual([(4, "one")], [cell for cell in rows[layout.box_text] if cell[0] == 4])
        self.assertEqual([(4, "two")], [cell for cell in rows[layout.box_text + 1] if cell[0] == 4])
        self.assertEqual("one\ntwo", ui._handle("\r", asking=True))


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
    delay = [-1]
    screen.timeout.side_effect = lambda value: delay.__setitem__(0, value)

    def get_wch():
        if delay[0] == 0 or not pending:
            raise tui.curses.error("no input")
        return pending.pop(0)

    screen.get_wch.side_effect = get_wch
    with mock.patch.object(tui.ChatTUI, "_open"):
        ui = tui.ChatTUI()
    ui._screen = screen
    return ui


class PromptTypeaheadTests(unittest.TestCase):
    def ui(self, queued, *, draft=""):
        ui = screen_ui()
        pending = list(queued)
        ui._composer.insert(draft)
        delay = [-1]
        ui._screen.timeout.side_effect = lambda value: delay.__setitem__(0, value)

        def read():
            if delay[0] == 0:
                if pending:
                    return pending.pop(0)
                raise tui.curses.error("no queued input")
            return "\n"

        ui._screen.get_wch.side_effect = read
        self.enterContext(mock.patch.object(tui.curses, "flushinp", side_effect=pending.clear))
        self.enterContext(mock.patch.object(tui.curses, "curs_set"))
        return ui, pending

    def test_ctrl_d_already_queued_before_a_prompt_still_exits(self):
        ui, pending = self.ui(["\x04"])
        with self.assertRaises(EOFError):
            ui.ask("Continue? [y/N]: ")
        self.assertEqual([], pending)

    def test_discard_keeps_only_an_empty_draft_exit_and_restores_input_timeout(self):
        ui, pending = self.ui(["y", "\n", "\x04"])
        self.assertTrue(ui._discard_typeahead())
        self.assertEqual([], pending)
        ui._screen.timeout.assert_has_calls([mock.call(0), mock.call(-1)])

    def test_discarded_yes_cannot_answer_the_new_approval(self):
        ui, pending = self.ui(["y", "\n"])
        self.assertEqual("", ui.ask("Approve this plan? [y/N]: "))
        self.assertEqual([], pending)

    def test_ctrl_d_with_an_existing_draft_is_not_an_exit(self):
        ui, pending = self.ui(["\x04"], draft="unsent reply")
        self.assertFalse(ui._discard_typeahead())
        self.assertEqual("unsent reply", ui._composer.text)
        self.assertEqual([], pending)

    def test_literal_ctrl_d_inside_a_bracketed_paste_is_not_an_exit(self):
        ui, pending = self.ui(list("\x1b[200~quoted \x04 text\x1b[201~"))
        self.assertFalse(ui._discard_typeahead())
        self.assertEqual([], pending)

    def test_already_buffered_answer_keys_cannot_approve_the_new_plan(self):
        ui, _ = self.ui([])
        ui._queued.extend(["y", "\n"])
        self.assertEqual("", ui.ask("Approve this plan? [y/N]: "))

    def test_discard_bounds_a_continuous_stream_before_flushing(self):
        ui, _ = self.ui([])
        ui._screen.get_wch.side_effect = lambda: "y"
        self.assertFalse(ui._discard_typeahead())
        self.assertLessEqual(ui._screen.get_wch.call_count, 4096)
        tui.curses.flushinp.assert_called_once()


class CoordinatedMeterTests(unittest.TestCase):
    def test_reopening_does_not_mislabel_anchor_spend_before_task_inventory_arrives(self):
        from tests.test_coordination_usage import ledger

        ui = screen_ui()
        ui.observe({**ledger("/source", 1, 100), "coordination": {"phase": "planning"}})
        self.assertEqual("task $? · tokens pending", ui._spend())

    def test_task_meter_survives_repository_switches_and_unavailable_reads(self):
        from tests.test_coordination_usage import ledger

        ui = screen_ui()
        source, member = ledger("/source", 1, 100), ledger("/member", 2, 200)
        ui.observe(source)
        ui.observe_coordination({"version": 1, "anchor": "/source", "runs": [source, member, ledger("/new", 0, 0)]})
        ui.observe(ledger("/new", 0, 0, complete=False))
        self.assertEqual("task $3.00+ · 300+ tokens", ui._spend())
        ui.status_unavailable("read delayed")
        self.assertEqual("task $3.00+ · 300+ tokens", ui._spend())
        ui.observe(member)
        self.assertEqual("task $3.00+ · 300+ tokens", ui._spend())
        ui.observe(source)
        self.assertEqual("task $3.00+ · 300+ tokens", ui._spend())
        ui.observe(ledger("/unrelated", 7, 700))
        self.assertEqual("$7.00 · 700 tokens", ui._spend())

    def test_side_panel_labels_task_usage_separately_from_current_work(self):
        from tests.test_coordination_usage import ledger

        ui = screen_ui()
        ui._panel_on = True
        ui.observe_coordination(
            {"version": 1, "anchor": "/source", "runs": [ledger("/source", 1, 100), ledger("/member", 0, 0)]}
        )
        ui.observe(ledger("/member", 0, 0, complete=False))
        shown = []
        ui._put = lambda row, col, text, attr=0: shown.append(text)
        ui._side_panel(ui._layout(*ui._size()))
        self.assertIn("TASK USAGE", " ".join(shown))
        self.assertIn("$1.00+", " ".join(shown))


class MouseScrollTests(unittest.TestCase):
    def test_keyboard_reads_still_work_without_the_posix_descriptor_helper(self):
        ui = screen_ui("x")
        with mock.patch.dict("sys.modules", {"fcntl": None}):
            self.assertEqual("x", ui._read_key())

    def test_key_reads_do_not_block_on_missing_mouse_bytes_and_restore_flags(self):
        import fcntl
        import os

        ui = screen_ui()
        flags = 128
        observed = []

        def control(fd, command, value=None):
            nonlocal flags
            if command == fcntl.F_GETFL:
                return flags
            flags = value

        def read():
            observed.append(flags)
            raise tui.curses.error("partial input")

        ui._screen.get_wch.side_effect = read
        with mock.patch.object(tui.sys, "stdin") as stdin, mock.patch.object(fcntl, "fcntl", side_effect=control):
            stdin.fileno.return_value = 42
            self.assertIsNone(ui._read_key())
            self.assertEqual(128, flags)
            self.assertIsNone(ui._read_next())
            self.assertEqual(128, flags)
        self.assertEqual([128 | os.O_NONBLOCK] * 2, observed)

    def test_keyboard_reads_still_work_when_stdin_has_no_descriptor(self):
        ui = screen_ui("x")
        with mock.patch.object(tui.sys, "stdin") as stdin:
            stdin.fileno.side_effect = ValueError("no descriptor")
            self.assertEqual("x", ui._read_key())

    def test_wheel_scrolls_history_both_ways_without_editing_composer(self):
        ui = screen_ui()
        ui._composer.insert("draft")
        ui._anchored = True
        with mock.patch.object(tui.curses, "getmouse", return_value=(0, 4, 5, 0, tui.curses.BUTTON4_PRESSED)):
            ui._handle(tui.curses.KEY_MOUSE, asking=False)
        self.assertEqual(3, ui._scroll)
        self.assertFalse(ui._anchored)
        with mock.patch.object(tui.curses, "BUTTON5_PRESSED", 1 << 21, create=True):
            with mock.patch.object(tui.curses, "getmouse", return_value=(0, 4, 5, 0, 1 << 21)):
                ui._handle(tui.curses.KEY_MOUSE, asking=True)
        self.assertEqual(0, ui._scroll)
        self.assertEqual("draft", ui._composer.text)

    def test_wheel_down_works_on_a_library_without_button5(self):
        # macOS's ncurses (mouse version 1) has no BUTTON5_PRESSED and reports the wheel turned down
        # as REPORT_MOUSE_POSITION; the chat asked for BUTTON4 alone, so a person could not scroll back.
        position = 1 << 27
        old = vars(tui.curses).pop("BUTTON5_PRESSED", None)
        if old is not None:
            self.addCleanup(setattr, tui.curses, "BUTTON5_PRESSED", old)
        with mock.patch.object(tui.curses, "REPORT_MOUSE_POSITION", position):
            self.assertEqual(position, tui.ChatTUI._wheel_down())
            ui = screen_ui()
            with mock.patch.object(tui.curses, "mousemask", return_value=(0, 7)) as mask:
                ui._mouse_reporting(True)
            mask.assert_called_once_with(tui.curses.BUTTON4_PRESSED | position)
            ui._scroll = 6
            with mock.patch.object(tui.curses, "getmouse", return_value=(0, 4, 5, 0, position)):
                ui._handle(tui.curses.KEY_MOUSE, asking=False)
            self.assertEqual(3, ui._scroll)

    def test_wheel_position_survives_new_messages(self):
        ui = screen_ui()
        for n in range(40):
            ui.say(f"AutoCode: message {n}")
        with mock.patch.object(tui.curses, "getmouse", return_value=(0, 4, 5, 0, tui.curses.BUTTON4_PRESSED)):
            for _ in range(8):
                ui._handle(tui.curses.KEY_MOUSE, asking=False)

        def visible():
            layout = ui._layout(*ui._size())
            lines = ui._transcript.lines(layout.body_width)
            end = len(lines) - ui._scroll
            return lines[max(0, end - (layout.body_end - layout.body_start)) : end]

        before = visible()
        self.assertTrue(before)
        ui.say("AutoCode: appended while reading")
        self.assertEqual(before, visible())

    def test_clicks_and_wheels_outside_transcript_do_not_scroll(self):
        ui = screen_ui()
        ui._panel_on = True
        ui._live_on = True
        layout = ui._layout(*ui._size())
        for x, y, flags in (
            (4, 5, tui.curses.BUTTON1_PRESSED),
            (4, 0, tui.curses.BUTTON4_PRESSED),
            (4, layout.box_text, tui.curses.BUTTON4_PRESSED),
            (layout.panel_col, 5, tui.curses.BUTTON4_PRESSED),
            (4, layout.live_start, tui.curses.BUTTON4_PRESSED),
        ):
            with self.subTest(x=x, y=y, flags=flags):
                with mock.patch.object(tui.curses, "getmouse", return_value=(0, x, y, 0, flags)) as read:
                    ui._handle(tui.curses.KEY_MOUSE, asking=True)
                read.assert_called_once()
                self.assertEqual(0, ui._scroll)
        with mock.patch.object(tui.curses, "getmouse", side_effect=tui.curses.error):
            ui._handle(tui.curses.KEY_MOUSE, asking=True)

    def test_reporting_enables_only_wheels_and_restores_previous_mask(self):
        ui = screen_ui()
        with mock.patch.object(tui.curses, "mousemask", return_value=(123, 7)) as mask:
            ui._mouse_reporting(True)
            ui._mouse_reporting(False)
        self.assertEqual(
            [mock.call(tui.curses.BUTTON4_PRESSED | tui.ChatTUI._wheel_down()), mock.call(7)],
            mask.call_args_list,
        )

    def test_unsupported_reporting_keeps_keyboard_available(self):
        ui = screen_ui()
        with mock.patch.object(tui.curses, "mousemask", side_effect=tui.curses.error):
            ui._mouse_reporting(True)
            ui._mouse_reporting(False)
        ui._handle(tui.curses.KEY_PPAGE, asking=True)
        self.assertGreater(ui._scroll, 0)

    def test_open_close_and_outside_manage_reporting(self):
        ui = screen_ui()
        with (
            mock.patch.object(ui, "_mouse_reporting") as reporting,
            mock.patch.object(ui, "_bracketed_paste"),
            mock.patch.object(ui, "_start_colors"),
            mock.patch.object(ui, "_draw"),
            mock.patch.object(tui, "curses"),
            mock.patch.object(tui.threading, "Thread"),
            mock.patch.object(tui.subprocess, "call", return_value=0),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            ui._open()
            ui.outside(["example"])
            ui.close()
        self.assertEqual(
            [mock.call(True), mock.call(False), mock.call(True), mock.call(False)], reporting.call_args_list
        )


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
                self.assertEqual(f"Terminal too small: need 32 columns, 12 rows; now {cols}x{rows}.".split(), words)


class StatusLineTests(unittest.TestCase):
    def test_planning_screen_hides_stale_completion_counts_until_execution(self):
        ui = screen_ui()
        ui._panel_on = True
        current = {
            "status": "RUNNING",
            "phase": "PLANNING",
            "progress": {
                "stage": "Plan Reviewer",
                "headline": "Plan Reviewer working",
                "tasks": {"label": "0 of 1 task done", "items": [{"number": 1, "label": "Fix metadata"}]},
                "requirements": {"label": "0 of 6 requirements checked", "items": [{"id": "AC1", "label": "Code"}]},
                "problems": {"open": 0},
            },
        }
        ui.observe(current)
        ui._transcript.activity = (
            "Plan Reviewer working · 0 of 1 task done · 0 of 6 requirements checked · 0 open problems"
        )
        shown = " ".join(text for _, text in drawn(ui))
        self.assertIn("Plan Reviewer working", shown)
        self.assertIn("STAGE  Plan Reviewer", shown)
        self.assertNotIn("0 of", shown)
        self.assertNotIn("0 open problems", shown)
        current["phase"] = "READY_TO_EXECUTE"
        current["progress"].update(stage="Builder", headline="Builder working")
        ui.observe(current)
        shown = " ".join(text for _, text in drawn(ui))
        self.assertIn("TASKS  0 of 1 task done", shown)
        self.assertIn("ACCEPTANCE CHECKS  0 of 6", shown)

    def test_the_status_row_follows_a_queued_stage_once_it_starts_working(self):
        # The client said "Next: Builder" once; "Builder working" is not news to it (#1844), so the
        # status row kept saying "Next: Builder" while the Builder worked in a live run.
        ui = screen_ui()
        line = "Builder working · 0 of 1 task done · 0 of 3 requirements checked · 0 open problems"
        ui.observe(
            {
                "status": "RUNNING",
                "phase": "EXECUTING",
                "progress": {"stage": "Builder", "headline": "Builder working", "line": line},
            }
        )
        ui._transcript.activity = "Next: Builder · 0 of 1 task done · 0 of 3 requirements checked · 0 open problems"
        shown = " ".join(text for _, text in drawn(ui))
        self.assertIn("Builder working · 0 of 1 task done", shown)
        self.assertNotIn("Next: Builder", shown)

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
    def hints(self, *, coordinated=False, attached=False, asking=False, cols=180):
        ui = screen_ui()
        view = {"progress": {"line": "Working"}}
        if coordinated:
            view["coordination"] = {"cancel_command": ["autocode", "coordinate", "cancel"]}
        ui.observe(view)
        ui._asking = asking
        drawn = []
        ui._put = lambda row, col, text, attr=0: drawn.append(text)
        with mock.patch.object(ui, "_can_detach", return_value=attached):
            layout = ui._layout(40, cols)
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

    def test_narrow_footer_keeps_navigation_and_the_boundary_action_visible(self):
        for cols in (32, 40):
            with self.subTest(cols=cols):
                footer = self.hints(cols=cols)
                self.assertIn("/ commands", footer)
                self.assertIn("^C pause", footer)
                self.assertNotIn("Requirements", footer)

    def test_narrow_detach_coordination_and_question_actions_fit(self):
        for cols in (32, 40):
            with self.subTest(cols=cols, state="detach"):
                footer = self.hints(attached=True, cols=cols)
                self.assertIn("^D detach", footer)
                self.assertIn("^C pause", footer)
                self.assertLessEqual(tui.display_width(footer.splitlines()[-1]), cols - 3)
            with self.subTest(cols=cols, state="coordination"):
                footer = self.hints(coordinated=True, cols=cols)
                self.assertIn("^C cancel", footer)
                self.assertNotIn("pause", footer)
                self.assertLessEqual(tui.display_width(footer.splitlines()[-1]), cols - 3)
            with self.subTest(cols=cols, state="question"):
                footer = self.hints(asking=True, cols=cols)
                self.assertIn("history", footer)
                self.assertIn("quit", footer)
                self.assertLessEqual(tui.display_width(footer.splitlines()[-1]), cols - 3)


class StallAndWorkedTests(unittest.TestCase):
    """The status row says how long since the working stage last wrote anything, and the footer how
    long the run has worked in all; both from the status view's own records."""

    def drawn(self, view, *, cols=180):
        ui = screen_ui()
        ui.observe(view)
        puts = []
        ui._put = lambda row, col, text, attr=0: puts.append((text, attr))
        with mock.patch.object(ui, "_can_detach", return_value=False):
            layout = ui._layout(40, cols)
            ui._question_or_status(layout)
            ui._hints(layout)
        return ui, puts

    def view(self, last_output_at):
        return {
            "status": "RUNNING",
            "progress": {"stage": "Builder", "line": "Builder working · 0 of 1 task done"},
            "chat": {"last_output_at": last_output_at},
            "usage": {
                "accounting": {
                    "attempts": [
                        {"stage": "terra", "job": "Builder", "duration_seconds": 600, "exit_code": 0},
                        {"stage": "terra", "job": "Builder", "duration_seconds": 30, "active": True},
                    ]
                }
            },
        }

    def test_a_quiet_stage_is_flagged_on_the_status_row_and_a_writing_one_says_when(self):
        recent = (datetime.now(UTC) - timedelta(seconds=5)).isoformat(timespec="seconds")
        ui, puts = self.drawn(self.view(recent))
        status = next(text for text, _ in puts if "Builder working" in text)
        self.assertRegex(status, r"Builder working · 0 of 1 task done · last output \d+s ago$")
        self.assertEqual(ui._style("working"), next(attr for text, attr in puts if text == status))
        stale = (datetime.now(UTC) - timedelta(minutes=4, seconds=5)).isoformat(timespec="seconds")
        ui, puts = self.drawn(self.view(stale))
        status = next(text for text, _ in puts if "Builder working" in text)
        self.assertRegex(status, r"no output for 4m0\ds$")
        self.assertEqual(ui._style("panel-warn"), next(attr for text, attr in puts if text == status))

    def test_without_the_time_the_status_row_is_as_before(self):
        view = self.view(None)
        del view["chat"]
        _, puts = self.drawn(view)
        status = next(text for text, _ in puts if "Builder working" in text)
        self.assertTrue(status.endswith("0 of 1 task done"), status)

    def test_the_footer_says_how_long_the_run_has_worked_before_its_spend(self):
        _, puts = self.drawn(self.view(None))
        footer = next(text for text, _ in puts if "/ commands" in text)
        self.assertIn("Builder · worked 10m30s · ", footer)
        self.assertLess(footer.index("worked"), footer.index("/ commands"))

    def test_the_working_time_gives_way_before_the_footers_actions_do(self):
        # A 120-column terminal at a question: with the time the hints lost "ctrl-d quit" off the edge.
        ui = screen_ui()
        view = self.view(None)
        view["cost"] = {"spent_usd": 0.42, "complete": True}
        view["usage"]["accounting"]["tokens"] = {
            "input_tokens": {"value": 150_000, "known": 150_000, "complete": True},
            "output_tokens": {"value": 34_000, "known": 34_000, "complete": True},
        }
        ui.observe(view)
        ui._asking = True
        puts = []
        ui._put = lambda row, col, text, attr=0: puts.append(text)
        ui._hints(ui._layout(40, 120))
        footer = next(text for text in puts if "/ commands" in text)
        self.assertIn("ctrl-d quit", footer)
        self.assertNotIn("worked", footer)
        self.assertIn("Builder · $0.42 · 184k tokens · ", footer)
        self.assertLessEqual(tui.display_width(footer), 118)
        puts.clear()
        ui._hints(ui._layout(40, 160))
        self.assertIn("Builder · worked 10m30s · $0.42", next(text for text in puts if "/ commands" in text))


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

    def test_repeated_enter_while_working_keeps_one_notice_and_the_unsent_draft(self):
        ui = screen_ui()
        ui.observe({"liveness": {"kind": "supervised"}})
        for key in "hello\n\n\n":
            self.assertIsNone(ui._handle(key, asking=False))
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
        delay = [-1]
        screen.timeout.side_effect = lambda value: delay.__setitem__(0, value)

        def read():
            calls.append("discard" if delay[0] == 0 else "read")
            if delay[0] == 0:
                raise tui.curses.error("no queued input")
            return "\n"

        screen.get_wch.side_effect = read
        with mock.patch.object(tui.ChatTUI, "_open"), mock.patch.object(tui.curses, "curs_set"):
            ui = tui.ChatTUI()
            ui._screen = screen
            with mock.patch.object(tui.curses, "flushinp", side_effect=lambda: calls.append("flush")):
                self.assertEqual("", ui.ask("Approve this plan? [y/N]"))
        self.assertEqual(["discard", "flush", "read"], calls)


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

    def test_a_new_approval_prompt_keeps_the_plan_position_the_person_is_reading(self):
        ui = self.ui("\x03")
        ui.say("Plan to approve:\n" + "\n".join(f"reading row {number}" for number in range(40)))
        ui._scroll_by(-ui._page())
        before = self.shown(ui)
        self.assertNotIn("Plan to approve:", before)
        with self.assertRaises(KeyboardInterrupt):
            ui.ask("Approve the complete plan? [y/N], or type what to change: ")
        after = self.shown(ui)
        self.assertTrue(before.startswith(after), (before, after))
        self.assertFalse(ui._anchored, "the person's paging remains in control")

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
                {
                    "id": "a:r1",
                    "agent": "Builder · task M1",
                    "kind": "message",
                    "text": "Checking the greeting behavior.",
                },
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

    def test_waiting_stage_is_visible_without_a_sidebar_and_working_stage_is_not_repeated(self):
        ui = screen_ui()
        ui.observe({"status": "WAITING_FOR_USER", "progress": {"stage": "Planner", "headline": "Waiting for you"}})
        shown = " ".join(text for row, _, text in self.drawn(ui, cols=80) if row == 2)
        self.assertIn("Planner · Waiting for you", shown)
        ui.observe({"status": "RUNNING", "progress": {"stage": "Plan Reviewer", "headline": "Plan Reviewer working"}})
        shown = " ".join(text for row, _, text in self.drawn(ui, cols=80) if row == 2)
        self.assertEqual(1, shown.count("Plan Reviewer"))

    def test_public_stage_keeps_full_width_until_the_panel_is_requested(self):
        ui = screen_ui()
        ui.observe({"status": "WAITING_FOR_USER", "progress": {"stage": "Planner", "headline": "Waiting for you"}})
        self.assertIsNone(ui._layout(30, 120).panel_col)
        ui._run("/panel", True)
        self.assertIsNotNone(ui._layout(30, 120).panel_col)
        ui._run("/panel", True)
        self.assertIsNone(ui._layout(30, 120).panel_col)

    def test_coordination_replaces_stale_anchor_labels_without_hiding_member_stage(self):
        ui = screen_ui()
        anchor = {
            "run_dir": "/task/run",
            "status": "WAITING_FOR_USER",
            "coordination": {"id": "task"},
            "progress": {"stage": "Requirements", "headline": "Waiting for you"},
        }
        ui.observe(anchor)
        ui.observe_conversation({"version": 1, "anchor": "/task/run", "needs": {"kind": "start"}})
        ui._panel_on = True
        puts = self.drawn(ui)
        layout = ui._layout(30, 120)
        for row in (2, layout.status_start):
            self.assertIn("Preparing repository work", " ".join(text for r, _, text in puts if r == row))
        self.assertNotIn("Requirements", " ".join(text for _, _, text in puts))
        self.assertNotIn("Waiting for you", " ".join(text for _, _, text in puts))
        self.assertEqual(anchor, ui._view, "rendering must not change the public action view")
        ui.observe({"run_dir": "/member/run", "progress": {"stage": "Planner", "headline": "Planner working"}})
        puts = self.drawn(ui)
        self.assertIn("Planner working", " ".join(text for r, _, text in puts if r == 2))
        self.assertNotIn("Preparing repository work", " ".join(text for _, _, text in puts))

    def test_coordination_keeps_outage_warning_and_real_approval_visible(self):
        ui = screen_ui()
        ui.observe({"run_dir": "/task/run", "coordination": {"id": "task"}})
        ui.observe_conversation({"version": 1, "anchor": "/task/run", "needs": {"kind": "approval"}})
        self.assertIn("Waiting for plan approval", " ".join(text for r, _, text in self.drawn(ui) if r == 2))
        ui.status_unavailable("read failed")
        self.assertIn("Status unavailable", " ".join(text for r, _, text in self.drawn(ui) if r == 2))

    def test_default_progress_uses_one_conversation_and_keeps_paged_history_still(self):
        ui = screen_ui()
        for number in range(30):
            ui.say(f"You: Earlier question {number}")
        ui.observe(self.VIEW)
        layout = ui._layout(30, 120)
        self.assertIsNone(layout.live_start)
        self.assertEqual(layout.body_end, layout.panel_end)
        self.assertIn("Checking the greeting behavior.", repr(ui._transcript.blocks))
        self.assertNotIn("pytest -q", repr(ui._transcript.blocks))
        ui._scroll_by(12)
        before = [
            (row, text) for row, col, text in self.drawn(ui) if col == 0 and layout.body_start <= row < layout.body_end
        ]
        ui.observe(
            {"chat": {"log": [{"id": "new", "agent": "Planner", "kind": "message", "text": "Checking another path."}]}}
        )
        after = [
            (row, text) for row, col, text in self.drawn(ui) if col == 0 and layout.body_start <= row < layout.body_end
        ]
        self.assertEqual(before, after)

    def test_sidebar_keeps_status_and_totals_without_activity_history(self):
        ui = screen_ui()
        ui._panel_on = True
        ui.observe(
            {
                "status": "RUNNING",
                "progress": {"headline": "Planner working"},
                "chat": {"progress": ["Planner working", "Recent steps: Requirements finished"]},
            }
        )
        layout = ui._layout(30, 120)
        shown = "\n".join(text for _, col, text in self.drawn(ui) if col == layout.panel_col)
        self.assertIn("Planner working", shown)
        self.assertNotIn("Recent steps", shown)
        self.assertNotIn("LOG", shown)
        ui._run("/log", False)
        self.assertIn("Planner working", repr(ui._transcript.blocks))

    def test_the_pane_shows_each_agent_above_the_composer(self):
        ui = screen_ui()
        ui._run("/live", False, "all")
        ui.observe(self.VIEW)
        puts = self.drawn(ui)
        layout = ui._layout(30, 120)
        self.assertIsNotNone(layout.live_start)
        self.assertLess(layout.body_end, layout.panel_end)
        self.assertLess(layout.panel_end, layout.box_top, "the composer stays below the pane")
        texts = [text for row, _, text in puts if layout.live_start <= row < layout.panel_end]
        self.assertTrue(any("LIVE · all agents" in text for text in texts))
        self.assertIn("Builder · task M1", texts)
        self.assertNotIn("Builder · task M2", texts)
        self.assertTrue(any("Checking the greeting behavior." in text for text in texts))
        self.assertFalse(any("edited greet.py" in text for text in texts))
        self.assertTrue(any("Check the output." in text for text in texts))
        self.assertFalse(any("pytest -q" in text for text in texts), "commands require the tools filter")

    def test_typing_while_autocode_works_keeps_the_pane(self):
        ui = screen_ui(*"/pl")
        ui._run("/live", False, "all")
        ui.observe(self.VIEW)
        self.assertIsNone(ui.wait(0.3))
        self.assertEqual("/pl", ui._composer.text)
        self.assertIsNotNone(ui._layout(30, 120).live_start)

    def test_without_agent_work_or_without_room_there_is_no_pane(self):
        ui = screen_ui()
        self.assertIsNone(ui._layout(30, 120).live_start)
        ui.observe(self.VIEW)
        self.assertIsNone(ui._layout(17, 120).live_start)

    def test_running_chat_uses_status_without_an_empty_extra_pane(self):
        ui = screen_ui()
        ui.observe({"status": "RUNNING", "chat": {"log": []}})
        puts = self.drawn(ui)
        self.assertIsNone(ui._layout(30, 120).live_start)
        text = "\n".join(text for _, _, text in puts)
        self.assertNotIn("LIVE · all agents", text)
        self.assertIn("AutoCode is working", text)
        self.assertNotIn("No agent is working", text)

    def test_default_progress_keeps_full_conversation_on_short_terminal(self):
        ui = screen_ui()
        ui.observe(self.VIEW)
        self.assertIsNone(ui._layout(20, 120).live_start)
        self.assertIn("Checking the greeting behavior.", repr(ui._transcript.blocks))

    def test_empty_status_log_does_not_hide_retained_live_activity(self):
        ui = screen_ui()
        ui.observe(self.VIEW)
        ui.observe({"chat": {"log": []}})
        self.assertIsNone(ui._layout(30, 120).live_start)
        self.assertTrue(any("Checking the greeting behavior." in text for _, _, text in self.drawn(ui)))

    def test_explicitly_hiding_live_survives_new_running_status(self):
        ui = screen_ui()
        ui._run("/live", False, "off")
        ui.observe({"status": "RUNNING", **self.VIEW})
        self.assertIsNone(ui._layout(30, 120).live_start)

    def test_empty_visible_log_does_not_claim_a_running_agent_stopped(self):
        ui = screen_ui()
        ui.observe({"status": "RUNNING", "chat": {"log": []}})
        ui._run("/live", False, "all")
        text = "\n".join(text for _, _, text in self.drawn(ui))
        self.assertIn("Working; waiting for a plain-English progress update.", text)
        self.assertNotIn("No agent is working", text)

    def test_live_hides_shows_and_filters_the_pane(self):
        ui = screen_ui()
        ui.observe(self.VIEW)
        ui._run("/live", False)
        self.assertIsNotNone(ui._layout(30, 120).live_start)
        ui._run("/live", False, "M2")
        self.assertIsNotNone(ui._layout(30, 120).live_start)
        self.assertEqual([], ui._live.shown())
        ui._run("/live", False, "all")
        self.assertEqual(2, len(ui._live.shown()))
        ui._run("/live", False, "off")
        self.assertIsNone(ui._layout(30, 120).live_start)

    def test_live_default_renders_thinking_in_the_pane_and_short_terminal_fallback(self):
        for height in (30, 14):
            with self.subTest(height=height):
                ui = screen_ui()
                ui._screen.getmaxyx.return_value = (height, 120)
                ui.observe(
                    {
                        "status": "RUNNING",
                        "chat": {
                            "log": [
                                {
                                    "id": "thought",
                                    "agent": "Planner",
                                    "kind": "thinking",
                                    "text": "Checking the caller [partial]",
                                },
                                {
                                    "id": "message",
                                    "agent": "Planner",
                                    "kind": "message",
                                    "text": "Found the entry point.",
                                },
                            ]
                        },
                    }
                )
                ui._run("/live", False)
                if height == 30:
                    shown = "\n".join(text for _, _, text in self.drawn(ui))
                else:
                    shown = ui._transcript.blocks[-1][1]
                self.assertIn("Checking the caller [partial]", shown)
                self.assertIn("Found the entry point.", shown)

    def test_where_the_pane_does_not_fit_live_prints_the_log_in_the_conversation(self):
        ui = screen_ui()
        ui._screen.getmaxyx.return_value = (14, 120)
        ui.observe(self.VIEW)
        ui._run("/live", False, "all")
        [(role, text)] = ui._transcript.blocks[-1:]
        self.assertEqual("info", role)
        self.assertIn("Builder · task M1 [message] Checking the greeting behavior.", text)
        self.assertNotIn("pytest -q", text)
        ui._run("/live", False, "tools")
        self.assertIn("pytest -q", ui._transcript.blocks[-1][1])

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


class RecentProgressScreenTests(unittest.TestCase):
    def test_visible_progress_tracks_only_agent_messages_in_the_current_history_window(self):
        transcript = tui.Transcript()
        transcript.add("You: Earlier request")
        transcript.progress(
            {
                "chat": {
                    "log": [
                        {"id": "one", "agent": "Planner", "kind": "message", "text": "First update."},
                        {"id": "two", "agent": "Planner", "kind": "message", "text": "Second update."},
                    ]
                }
            }
        )
        self.assertEqual({("Planner", "Second update.")}, transcript.visible_progress(80, 3, 0))
        self.assertEqual({("Planner", "First update.")}, transcript.visible_progress(80, 3, 3))
        self.assertEqual(set(), transcript.visible_progress(80, 3, 100))
        self.assertEqual(set(), transcript.visible_progress(80, 0, 0))

    def test_live_tail_shows_each_planner_update_once_after_revising_the_plan(self):
        ui = screen_ui()
        ui._panel_on = False
        ui.say("You: Leave the request and response fields blank.")
        ui.say("AutoCode: I will add that constraint to the saved plan.")
        note = "I will revise the saved plan and check the logging path."
        view = {
            "status": "RUNNING",
            "progress": {"stage": "Planner", "headline": "Planner working"},
            "chat": {"log": [{"id": "revision", "agent": "Planner", "kind": "message", "text": note}]},
        }
        ui.observe(view)
        ui.observe(view)
        for rows, cols in ((40, 121), (22, 70), (16, 60)):
            with self.subTest(rows=rows, cols=cols):
                shown = " ".join(text.strip() for _, text in drawn(ui, rows, cols))
                self.assertEqual(1, shown.count(note), shown)
                self.assertNotIn("latest updates", shown)
                self.assertIn("Planner", shown)
                self.assertIn("❯", row_text(drawn(ui, rows, cols), rows - 3))

    def test_streaming_update_remains_visible_until_it_enters_the_transcript(self):
        ui = screen_ui()
        view = {
            "status": "RUNNING",
            "progress": {"stage": "Planner"},
            "chat": {
                "log": [
                    {
                        "id": "one",
                        "agent": "Planner",
                        "kind": "message",
                        "text": "Checking the request.",
                        "status": "streaming",
                    }
                ]
            },
        }
        ui.observe(view)
        self.assertEqual(1, str(drawn(ui)).count("Checking the request."))
        view["chat"]["log"][0].pop("status")
        ui.observe(view)
        self.assertEqual(1, str(drawn(ui)).count("Checking the request."))

    def test_progress_already_visible_while_scrolled_is_not_repeated_in_the_strip(self):
        ui = screen_ui()
        for n in range(40):
            ui.say(f"You: Earlier request {n}")
        ui.observe(
            {
                "status": "RUNNING",
                "progress": {"stage": "Planner"},
                "chat": {
                    "log": [{"id": "one", "agent": "Planner", "kind": "message", "text": "Checking the request."}]
                },
            }
        )
        ui._scroll = 1
        self.assertEqual(1, str(drawn(ui)).count("Checking the request."))

    def test_recent_progress_stays_above_the_composer_while_reading_earlier_messages(self):
        ui = screen_ui()
        ui._panel_on = False
        for n in range(80):
            ui.say(f"You: Earlier request {n}")
        ui.observe(
            {
                "status": "RUNNING",
                "progress": {"stage": "Planner", "headline": "Planner working"},
                "chat": {
                    "log": [
                        {"id": "one", "agent": "Planner", "kind": "message", "text": "Checking the history interface."},
                        {"id": "two", "agent": "Planner", "kind": "message", "text": "Checking the result mapping."},
                        {"id": "tool", "agent": "Planner", "kind": "command", "text": "bash -lc hidden-command"},
                    ]
                },
            }
        )
        for rows, cols in ((40, 121), (22, 70), (16, 60)):
            with self.subTest(rows=rows, cols=cols):
                ui._scroll = 10000
                puts = drawn(ui, rows, cols)
                layout = ui._layout(rows, cols)
                self.assertIn("Earlier request 0", repr(puts))
                self.assertTrue(layout.notes)
                self.assertEqual(layout.body_end, layout.notes_start)
                notes = " ".join(text for row, text in puts if layout.notes_start <= row < layout.status_start)
                self.assertIn("Planner · latest updates", notes)
                self.assertIn("Checking the result mapping.", notes)
                self.assertNotIn("hidden-command", repr(puts))
                self.assertTrue(all(row < rows for row, _ in puts))
                self.assertIn("❯", row_text(puts, rows - 3))

    def test_recent_notes_yield_room_to_a_question_or_explicit_live_pane(self):
        ui = screen_ui()
        ui.observe(
            {
                "status": "RUNNING",
                "progress": {"stage": "Planner"},
                "chat": {
                    "log": [
                        {"id": "one", "agent": "Planner", "kind": "message", "text": "Checking the history interface."}
                    ]
                },
            }
        )
        ui._asking, ui._prompt = True, "Approve this plan?"
        self.assertEqual([], ui._layout(30, 100).notes)
        ui._asking, ui._live_on = False, True
        self.assertEqual([], ui._layout(30, 100).notes)
        ui._live_on = False
        self.assertEqual([], ui._layout(10, 40).notes)


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
    def test_draw_leaves_the_active_cursor_in_the_composer_after_footer_output(self):
        ui = screen_ui()
        ui._asking = True
        ui._composer.insert("draft")
        ui.observe(SPENDING_VIEW)
        ui._screen.reset_mock()
        ui._draw()
        calls = ui._screen.method_calls
        moved = [index for index, call in enumerate(calls) if call[0] == "move"]
        self.assertTrue(moved)
        self.assertFalse(any(call[0] == "addstr" for call in calls[moved[-1] + 1 :]))
        self.assertEqual(mock.call.move(27, 9), calls[moved[-1]])

    def test_the_footer_shows_the_runs_cost_and_tokens(self):
        ui = screen_ui()
        ui.observe(SPENDING_VIEW)
        self.assertIn("$0.42 · 184k tokens", row_text(drawn(ui), 29))
        self.assertNotIn("$0.42", row_text(drawn(ui), 0))

    def test_with_two_chats_the_footer_adds_the_session_total(self):
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
        self.assertIn("chat $0.42 · 184k tokens · session $1.42 · 185k tokens", row_text(drawn(ui, cols=140), 29))

    def test_a_struggling_stage_takes_the_status_row(self):
        ui = screen_ui()
        ui.observe(SPENDING_VIEW)
        puts = drawn(ui)
        status = next(text for _, text in puts if "attempt 2" in text and "Builder" in text and "exit 1" in text)
        self.assertIn("last one exit 1", status)
        self.assertIn("$0.42 · 184k tokens", row_text(puts, 29))

    def test_stage_meter_and_input_stay_at_bottom_across_terminal_sizes(self):
        ui = screen_ui()
        ui.observe({**SPENDING_VIEW, "progress": {"stage": "Planner", "headline": "Planner working"}})
        ui._composer.insert("my unsent draft")
        for rows, cols in ((12, 80), (22, 80), (40, 121), (63, 121)):
            with self.subTest(rows=rows, cols=cols):
                puts = drawn(ui, rows=rows, cols=cols)
                self.assertIn("my unsent draft", row_text(puts, rows - 3))
                footer = row_text(puts, rows - 1)
                self.assertIn("$0.42 · 184k tokens", footer)
                # The working time shows where the whole footer fits, and gives way where it does not.
                self.assertEqual(cols >= 121, "Planner · worked 1m00s · $0.42" in footer, footer)
                self.assertIsNone(ui._layout(rows, cols).panel_col)
                self.assertEqual(cols - 1, ui._layout(rows, cols).body_width)


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
        # The acceptance tests' files are named apart: the Tester may have bound a test that was already
        # there (the etcd run's resolver_test.go), so they are not counted as files the run changed.
        with_tests = {**shown, "acceptance_tests": {"files": ["test_greet.py", "greet.py"]}}
        self.assertEqual(
            "Changed 2 files: greet.py, README.md. Acceptance tests in test_greet.py. /diff shows what changed.",
            tui.panel.finished_text(with_tests),
        )
        self.assertEqual(
            "Acceptance tests in test_greet.py. /diff shows what changed.",
            tui.panel.finished_text({"acceptance_tests": {"files": ["test_greet.py"]}}),
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

    def test_underscore_emphasis_is_readable_without_changing_code_or_identifiers(self):
        self.assertEqual(
            "Fix only result_code; keep `_literal_`, __init__, _private and unfinished _text.",
            tui.prose_text("Fix _only_ result_code; keep `_literal_`, __init__, _private and unfinished _text."),
        )
        self.assertEqual("Use this whole phrase.", tui.prose_text("Use _this whole phrase_."))

    def test_asterisk_italics_are_readable_without_changing_bold_code_or_operators(self):
        self.assertEqual("Apply only new entries.", tui.prose_text("Apply *only* *new* entries."))
        self.assertEqual("Use this whole phrase.", tui.prose_text("Use *this whole phrase*."))
        for literal in (
            "**bold**",
            "`*literal*`",
            "2 * 3 * 4",
            "value*factor*other",
            "* item",
            "*.go",
            "*test*.go",
            "unfinished *text",
            r"\*literal\*",
        ):
            with self.subTest(literal=literal):
                self.assertEqual(literal, tui.prose_text(literal))

    def test_asterisk_italics_wrap_as_prose_and_leave_fences_diffs_and_user_text_exact(self):
        rows = tui.body_rows("assistant-body", "Apply *only* *new* entries.", 16)
        self.assertEqual("Apply only new entries.", " ".join(text.strip() for _, text in rows))
        self.assertIn(("code", "  *literal*"), tui.body_rows("assistant-body", "```txt\n*literal*\n```", 30))
        self.assertIn(
            ("diff-add", "  +*literal*"), tui.body_rows("info-body", "@@ -1 +1 @@\n+*literal*", 30, diff=True)
        )
        ui = screen_ui()
        ui.say("You: *only* *new*")
        self.assertIn("*only* *new*", str(drawn(ui)))

    def test_emphasis_is_removed_before_prose_wraps_but_fenced_code_and_diffs_stay_exact(self):
        for style in ("assistant-body", "info-body", "notice-body", "plain"):
            with self.subTest(style=style):
                rows = tui.body_rows(style, "Fix _only the displayed result code_.", 20)
                self.assertEqual("Fix only the displayed result code.", " ".join(text.strip() for _, text in rows))
        self.assertIn(("code", "  _literal_"), tui.body_rows("assistant-body", "```py\n_literal_\n```", 30))
        self.assertIn(
            ("diff-add", "  +_literal_"), tui.body_rows("info-body", "@@ -1 +1 @@\n+_literal_", 30, diff=True)
        )

    def test_scrolled_and_explicit_live_updates_hide_prose_emphasis(self):
        ui = screen_ui()
        for n in range(40):
            ui.say(f"You: Earlier request {n}")
        ui.observe(
            {
                "status": "RUNNING",
                "progress": {"stage": "Planner"},
                "chat": {
                    "log": [
                        {"id": "one", "agent": "Planner", "kind": "message", "text": "Checking _only_ the result code."}
                    ]
                },
            }
        )
        ui._scroll = 1000
        shown = str(drawn(ui))
        self.assertIn("Checking only the result code.", shown)
        self.assertNotIn("_only_", shown)
        self.assertIn("Checking only the result code.", str(ui.live_rows(100)))

    def test_user_underscore_emphasis_is_preserved_as_typed(self):
        ui = screen_ui()
        ui.say("You: _only_ these fields")
        self.assertIn("_only_ these fields", str(drawn(ui)))

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

    def test_agent_updates_render_inline_markup_like_assistant_replies(self):
        ui = screen_ui()
        ui.observe(
            {
                "status": "WAITING_FOR_USER",
                "chat": {
                    "log": [
                        {
                            "id": "planner-update",
                            "agent": "Planner",
                            "kind": "message",
                            "text": "Use _Both_ for **new entries**; inspect `result_code`.",
                        }
                    ]
                },
            }
        )
        puts = styled(ui)
        self.assertIn("result_code", [put[2] for put in puts])
        code = next(put for put in puts if put[2] == "result_code")
        bold = next(put for put in puts if put[2] == "new entries")
        self.assertEqual(ui._style("code-inline"), code[3])
        self.assertEqual(ui._style("assistant-body") | tui.curses.A_BOLD, bold[3])
        line = "".join(put[2] for put in sorted(puts) if put[0] == code[0])
        self.assertEqual("  Use Both for new entries; inspect result_code.", line)

    def test_command_output_and_diff_context_keep_literal_inline_marks(self):
        ui = screen_ui()
        ui._transcript.info("/example", "Keep `command` and **literal**.")
        ui._transcript.info("/diff", "@@ -1 +1 @@\n same `command` and **literal**")
        puts = styled(ui)
        self.assertIn("  Keep `command` and **literal**.", [text for _, _, text, _ in puts])
        self.assertIn("   same `command` and **literal**", [text for _, _, text, _ in puts])

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

    def test_requirements_have_a_fixed_count_and_full_text_on_request(self):
        ui = screen_ui()
        ui._panel_on = True
        ui.observe(self.VIEW)
        layout = ui._layout(30, 120)
        panel_rows = [text for row, col, text in [(r, c, t) for r, c, t, _ in styled(ui)] if col == layout.panel_col]
        self.assertIn("ACCEPTANCE CHECKS  0 of 1", " ".join(panel_rows))
        self.assertNotIn("Given the command", " ".join(panel_rows))
        ui._run("/plan", False)
        self.assertIn(self.VIEW["progress"]["requirements"]["items"][0]["label"], ui._transcript.blocks[-1][1])
