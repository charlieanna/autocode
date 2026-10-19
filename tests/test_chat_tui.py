"""The full-screen chat's pure pieces: width, wrapping, transcript blocks, composer, window."""

import unittest

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


class WindowTests(unittest.TestCase):
    def test_short_text_keeps_the_cursor_column(self):
        self.assertEqual(("hello", 5), tui.window("hello", 5, 10))

    def test_long_text_scrolls_to_keep_the_cursor_visible(self):
        self.assertEqual(("world", 5), tui.window("hello world", 11, 6))

    def test_wide_characters_are_not_split(self):
        self.assertEqual(("世界", 4), tui.window("你好世界", 4, 5))


if __name__ == "__main__":
    unittest.main()
