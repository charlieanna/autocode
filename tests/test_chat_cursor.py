"""The visible terminal cursor belongs to the reply, not the last footer write."""

import os
import sys
import unittest

from tests import ROOT
from tests.terminal_screen import TerminalScreen, require_tmux


class ComposerCursorTests(unittest.TestCase):
    def test_cursor_tracks_typing_and_resize_in_the_bottom_reply(self):
        script = """
import sys
sys.path.insert(0, 'tools')
from autocode_chat_tui import ChatTUI
with ChatTUI() as ui:
    ui.observe({'status': 'WAITING_FOR_USER', 'progress': {'stage': 'Planner'}})
    try:
        ui.ask('What should change?')
    except EOFError:
        pass
"""
        with TerminalScreen(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=os.environ,
            cols=100,
            rows=30,
            tmux=require_tmux(self),
        ) as screen:
            screen.wait_for(
                lambda shown: "What should change?" in shown and "Planner ·" in shown.splitlines()[-1],
                what="reply and footer drawn",
            )
            self.assertEqual("4,27,1", self.cursor(screen))
            screen.type("draft")
            screen.wait_for(lambda shown: "draft" in shown.splitlines()[-3], what="draft in bottom input")
            self.assertEqual("9,27,1", self.cursor(screen))
            screen.resize(80, 22)
            screen.wait_for(
                lambda shown: (
                    len(shown.splitlines()) == 22
                    and "draft" in shown.splitlines()[-3]
                    and "Planner ·" in shown.splitlines()[-1]
                ),
                what="resized input and footer",
            )
            self.assertEqual("9,19,1", self.cursor(screen))
            screen.keys("C-u", "C-d")
            self.assertEqual(0, screen.exit_status())

    @staticmethod
    def cursor(screen):
        return screen._run("display-message", "-pt", "t", "#{cursor_x},#{cursor_y},#{cursor_flag}").stdout.strip()
