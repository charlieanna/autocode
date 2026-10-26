"""The full-screen `autocode chat` in a real terminal: what a person sees and what their keys do (#1646).

The TUI's own tests check what it asks curses to draw on a stand-in screen. These
run `autocode chat` in tmux with the offline fixture provider and read the screen,
which is how the type-ahead, paste, echo and `/` menu bugs fixed in #1639 were found.
"""

import os
import sys
import unittest

from tests import ROOT
from tests.terminal_screen import TerminalScreen, require_tmux
from tests.test_agent_step import fixture_workspace
from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS

AUTOCODE = ROOT / "tools" / "autocode.py"
RUN_SECONDS = 300  # a fixture stage is quick, but CI hosts are shared


class ChatScreenTests(unittest.TestCase):
    def setUp(self):
        self.tmux = require_tmux(self)
        self.workspace, env = fixture_workspace(self)
        self.env = {**os.environ, **env}

    def chat(self, *args, cols=100, rows=30, options=FIXTURE_OPTIONS) -> TerminalScreen:
        argv = [sys.executable, str(AUTOCODE), "chat", *args]
        if options:
            argv += ["--", *options]
        screen = TerminalScreen(argv, cwd=self.workspace, env=self.env, cols=cols, rows=rows, tmux=self.tmux)
        self.addCleanup(screen.close)
        screen.wait_for("What do you want done?", 60)
        return screen

    def no_run_started(self):
        self.assertFalse((self.workspace / ".autocode" / "runs").exists())

    def test_the_chat_opens_on_a_new_chat_and_ctrl_d_leaves_without_starting_anything(self):
        screen = self.chat()
        shown = screen.text()
        self.assertIn("AutoCode chat", shown.splitlines()[0])
        self.assertIn("New chat", shown)
        screen.keys("C-d")
        self.assertEqual(2, screen.exit_status())
        self.assertIn("Nothing started.", screen.text())
        self.no_run_started()

    def test_the_command_menu_closes_on_escape_and_never_sends_an_unknown_command(self):
        screen = self.chat()
        screen.type("/")
        shown = screen.wait_for("Your chats in every project")  # the first entry before a chat is open
        self.assertNotIn("/plan", shown, "commands that need a run are not offered before one")
        screen.keys("Escape")
        screen.type("x")  # straight after Escape: the key is typed, not swallowed
        composer = screen.wait_for(
            lambda shown: "Your chats in every project" not in shown and composer_text(shown) == "x", what="x alone"
        )
        self.assertNotIn("Your chats in every project", composer)
        screen.keys("C-u")
        screen.type("/Build something nice")
        screen.keys("Enter")
        shown = screen.wait_for("is not a command")
        self.assertEqual("/Build something nice", composer_text(shown), "the text stays to edit")
        self.no_run_started()

    def test_a_resize_redraws_the_whole_screen(self):
        screen = self.chat(cols=120, rows=34)
        screen.resize(70, 22)
        # tmux changes the pane geometry before curses handles SIGWINCH. A
        # blank frame already fits; wait for the application to redraw too.
        shown = screen.wait_for(
            lambda text: (
                len(text.splitlines()) == 22
                and fits(text, 70)
                and "AutoCode chat" in text.splitlines()[0]
                and "What do you want done?" in text
            ),
            what="the chat redrawn at 70x22",
        )
        self.assertIn("AutoCode chat", shown.splitlines()[0])
        screen.resize(100, 30)
        shown = screen.wait_for(lambda text: composer_width(text) == 99, what="the composer at 100 columns")
        self.assertIn("AutoCode chat", shown.splitlines()[0])
        self.assertIn("What do you want done?", shown)

    def test_a_pasted_request_type_ahead_and_a_complete_run(self):
        screen = self.chat(cols=120, rows=34)
        # A two-line paste is one request: the fixture provider recognizes only the whole brief.
        first, rest = BRIEF.split(". Deliver", 1)
        screen.paste(f"{first}.\nDeliver{rest}")
        screen.keys("Enter")
        screen.wait_for("Approve understanding", RUN_SECONDS)
        screen.type("y")
        screen.keys("Enter")
        screen.wait_for("Prepare plan?")  # the spend confirmation; the request is still at the top
        for _ in range(6):
            screen.keys("PPage")
        top = screen.wait_for("named greet.py", what="the request at the top of the transcript")
        self.assertEqual(1, top.count("named greet.py"), "the request is shown once")
        screen.keys("NPage", "NPage", "NPage", "NPage", "NPage", "NPage")

        screen.type("y")
        screen.keys("Enter")
        screen.wait_for("AutoCode is working")
        screen.type("y")  # typed while AutoCode works, before the plan is shown
        screen.keys("Enter")
        screen.wait_for("Approve this plan", RUN_SECONDS)
        # Keys are read in order: a "z" shown in the plan's reply box means the y and Enter
        # typed earlier did not answer the plan nobody had seen.
        screen.type("z")
        screen.wait_for(
            lambda shown: "Approve this plan" in shown and composer_text(shown) == "z", what="z at the plan"
        )
        screen.keys("C-u")
        screen.type("y")
        screen.keys("Enter")

        done = screen.wait_for("Anything else?", RUN_SECONDS)
        self.assertIn("1 of 1 task done", done, "the side panel at 120 columns")
        screen.type("/diff")
        screen.keys("Enter")
        screen.wait_for("README.md, greet.py, test_greet.py")
        screen.keys("Enter")
        self.assertEqual(0, screen.exit_status())
        self.assertIn("Done.", screen.text(), "the closing words stay on the terminal")
        self.assertTrue((self.workspace / "greet.py").is_file())

    def test_earlier_chats_are_listed_opened_and_a_new_one_started(self):
        screen = self.chat(cols=120, rows=34)
        screen.type(BRIEF)
        screen.keys("Enter")
        for prompt in ("Approve understanding", "Prepare plan?", "Approve this plan"):
            screen.wait_for(prompt, RUN_SECONDS)
            screen.type("y")
            screen.keys("Enter")
        screen.wait_for("Anything else?", RUN_SECONDS)
        screen.keys("Enter")
        self.assertEqual(0, screen.exit_status())
        screen.close()

        screen = self.chat(cols=120, rows=34)
        self.assertIn("1 earlier chat here. Type /runs to see them.", screen.text())
        screen.type("/runs ")  # the space closes the menu: Enter runs the command typed
        screen.keys("Enter")
        listed = screen.wait_for("Type /open N to open one")
        self.assertIn("1. Build a deterministic greeting CLI named greet.py", listed)
        self.assertIn("complete", listed)
        screen.type("/open 1")
        screen.keys("Enter")
        opened = screen.wait_for("Anything else?", RUN_SECONDS)
        self.assertIn("The requested task is complete.", opened, "the finished chat's conversation")
        first = chat_heading(opened)

        screen.type("/new ")
        screen.keys("Enter")
        screen.wait_for("What do you want done?")
        screen.type(BRIEF)
        screen.keys("Enter")
        fresh = screen.wait_for("Approve understanding", RUN_SECONDS)
        self.assertNotIn("The requested task is complete.", fresh, "a new chat starts on a clean screen")
        self.assertNotEqual(
            first, chat_heading(fresh), "the header names the new run independently of the global unread count"
        )
        self.assertEqual(2, len(list((self.workspace / ".autocode" / "runs").iterdir())))
        tabs = fresh.splitlines()[1]
        self.assertIn(" 1 Build a deterministic greeting CLI", tabs, "the finished chat is tab 1")
        self.assertIn(" 2 Build a deterministic greeting CLI", tabs, "the new chat is tab 2")
        screen.type("/tab 1 ")
        screen.keys("Enter")
        back = screen.wait_for("Anything else?", RUN_SECONDS)
        self.assertEqual(first, chat_heading(back), "back on the first chat independently of the global unread count")


def chat_heading(screen: str) -> str:
    """The selected chat's identity, excluding the independently changing global attention badge."""
    return screen.splitlines()[0].split("F8:", 1)[0].rstrip()


def composer_text(screen: str) -> str:
    """What is typed in the composer box, or "" when it shows its placeholder."""
    for line in screen.splitlines():
        inner = line.strip().strip("│|").strip()
        for prompt in ("❯ ", "> "):
            if inner.startswith(prompt):
                typed = inner[len(prompt) :].strip()
                return "" if typed.startswith(("Ask for", "Type your", "AutoCode is working")) else typed
    return ""


def composer_width(screen: str) -> int:
    """The width of the composer box's bottom border."""
    for line in screen.splitlines():
        if line.startswith(("╰", "+")) and line.rstrip().endswith(("╯", "+")):
            return len(line.rstrip())
    return 0


def fits(screen: str, cols: int) -> bool:
    return all(len(line) <= cols for line in screen.splitlines())


if __name__ == "__main__":
    unittest.main()
