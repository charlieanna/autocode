"""The full-screen `autocode chat` in a real terminal: what a person sees and what their keys do (#1646).

The TUI's own tests check what it asks curses to draw on a stand-in screen. These
run `autocode chat` in tmux with the offline fixture provider and read the screen,
which is how the type-ahead, paste, echo and `/` menu bugs fixed in #1639 were found.
"""

import os
import re
import sys
import unittest

from tests import ROOT
from tests.terminal_screen import TerminalScreen, require_tmux
from tests.test_agent_step import fixture_workspace
from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS

AUTOCODE = ROOT / "tools" / "autocode.py"
RUN_SECONDS = 300  # a fixture stage is quick, but CI hosts are shared


class ClosingScreenTests(unittest.TestCase):
    def test_leaving_approval_keeps_exit_notice_without_reprinting_the_plan(self):
        script = """
import sys
sys.path.insert(0, 'tools')
from autocode_chat_tui import ChatTUI
with ChatTUI() as ui:
    ui.say('You: Fix the history')
    ui.say('AutoCode: Here are the coordinated plans.')
    ui.say('Plan to approve:\\n' + '\\n'.join(f'PLAN-DETAIL-{n}' for n in range(100)))
    try:
        ui.ask('Approve these coordinated plans?')
    except EOFError:
        ui.say('Left the chat; the run is unchanged.')
"""
        with TerminalScreen(
            [sys.executable, "-c", script], cwd=ROOT, env=os.environ, cols=100, rows=25, tmux=require_tmux(self)
        ) as screen:
            screen.wait_for(
                lambda shown: "Plan to approve:" in shown and "Approve these coordinated plans?" in shown,
                what="the plan and its approval prompt",
            )
            screen.keys("C-d")
            self.assertEqual(0, screen.exit_status())
            shown = screen.wait_for("Left the chat; the run is unchanged.")
            self.assertNotIn("PLAN-DETAIL-", shown)
            self.assertNotIn("Plan to approve:", shown)


class LiveDefaultScreenTests(unittest.TestCase):
    def test_stage_names_stay_visible_in_the_unified_timeline_and_waiting_status(self):
        script = """
import sys
sys.path.insert(0, 'tools')
from autocode_chat_tui import ChatTUI
with ChatTUI() as ui:
    ui.say('You: Fix the history.')
    ui.observe({'status': 'WAITING_FOR_USER',
                'progress': {'stage': 'Planner', 'headline': 'Waiting for you'},
                'chat': {'log': [
                    {'id': 'requirements', 'agent': 'Requirements', 'kind': 'message',
                     'text': 'The operation and mapped result should be shown.'},
                    {'id': 'planner', 'agent': 'Planner', 'kind': 'message',
                     'text': 'Checking the established result mappings.'}]}})
    while True:
        ui.wait(0.1)
"""
        screen = TerminalScreen(
            [sys.executable, "-c", script], cwd=ROOT, env=os.environ, cols=120, rows=30, tmux=require_tmux(self)
        )
        self.addCleanup(screen.close)
        screen.wait_for(
            lambda shown: (
                all(
                    text in shown
                    for text in (
                        "Planner · Waiting for you",
                        "STAGE  Planner",
                        "▌ Requirements",
                        "▌ Planner",
                        "The operation and mapped result should be shown.",
                        "Checking the established result mappings.",
                    )
                )
                and "LIVE ·" not in shown
            ),
            what="attributed stage updates and the waiting stage without a separate live pane",
        )

    def test_structured_report_does_not_replace_readable_progress_with_json(self):
        script = """
import json, sys
sys.path.insert(0, 'tools')
from autocode_chat_log import prose_event
from autocode_chat_tui import ChatTUI
rows = []
for identifier, text in [('update', 'Checking the existing registry mappings.'),
                         ('report', json.dumps({'summary': 'saved ' * 200,
                                                'workspace_scope': {'spans_repositories': True}}))]:
    key, entry = prose_event({'type': 'text', 'part': {'id': identifier, 'text': text}})
    rows.append({'id': key, 'agent': 'Requirements', **entry})
with ChatTUI() as ui:
    ui.observe({'status': 'RUNNING', 'chat': {'log': rows}})
    while True:
        ui.wait(0.1)
"""
        screen = TerminalScreen(
            [sys.executable, "-c", script], cwd=ROOT, env=os.environ, cols=120, rows=30, tmux=require_tmux(self)
        )
        self.addCleanup(screen.close)
        screen.wait_for(
            lambda shown: (
                "Checking the existing registry mappings." in shown
                and "Structured response received." in shown
                and "workspace_scope" not in shown
                and "spans_repositories" not in shown
            ),
            timeout=10,
            what="readable progress and a neutral report receipt without JSON fragments",
        )

    def test_tool_input_fragments_do_not_crowd_out_readable_progress(self):
        script = """
import sys
sys.path.insert(0, 'tools')
from autocode_chat_tui import ChatTUI
rows = [{'id': 'reply', 'agent': 'Plan Reviewer', 'kind': 'message',
         'text': 'Checking the caller route against the saved requirement.'}]
for number in range(25):
    rows.extend([
        {'id': f'pulse-{number}', 'agent': 'Plan Reviewer', 'kind': 'thinking',
         'text': f'writing Bash input: {number + 1} chars;'},
        {'id': f'command-{number}', 'agent': 'Plan Reviewer', 'kind': 'command',
         'text': f'rg route source-{number}.go', 'status': 'done'},
    ])
with ChatTUI() as ui:
    ui.observe({'status': 'RUNNING', 'chat': {'log': rows}, 'progress': {
        'headline': 'Plan Reviewer working',
        'requirements': {'label': '0 of 1 requirement checked',
                         'items': [{'id': 'AC1', 'label': 'History is readable', 'state': 'unchecked'}]}}})
    while True:
        ui.wait(0.1)
"""
        screen = TerminalScreen(
            [sys.executable, "-c", script], cwd=ROOT, env=os.environ, cols=120, rows=30, tmux=require_tmux(self)
        )
        self.addCleanup(screen.close)
        screen.wait_for(
            lambda shown: (
                "LIVE · all agents" not in shown
                and "Plan Reviewer working" in shown
                and "ACCEPTANCE CHECKS" in shown
                and "Checking the caller route against the saved" in shown
                and "requirement." in shown
                and "rg route source-24.go" not in shown
                and "source-23.go" not in shown
                and "writing Bash" not in shown
                and "chars" not in shown
            ),
            timeout=10,
            what="plain-English progress without commands or 25 transport fragments",
        )
        screen.type("/live tools")
        screen.keys("Enter")
        screen.wait_for(
            lambda shown: (
                "LIVE · tools" in shown
                and "rg route source-23.go" in shown
                and "rg route source-24.go" in shown
                and "writing Bash" not in shown
            ),
            what="detailed command history remains available on request",
        )

    def test_live_prose_is_readable_in_the_terminal(self):
        script = """
import sys
sys.path.insert(0, 'tools')
from autocode_chat_log import prose_event
from autocode_chat_tui import ChatTUI
events = [
    ('item.updated', 'reasoning', 'Conf'),
    ('item.updated', 'reasoning', 'Found an'),
    ('item.updated', 'agent_message', 'Checking that'),
    ('item.completed', 'agent_message', 'I’m checking; I’ll report back.'),
    ('item.completed', 'agent_message', 'older context ' * 50 + 'Which codes should history show?'),
]
rows = []
for n, (event, kind, text) in enumerate(events):
    key, entry = prose_event({'type': event, 'item': {'id': str(n), 'type': kind, 'text': text}})
    rows.append({'id': key, 'agent': 'Plan Reviewer', **entry})
with ChatTUI() as ui:
    ui.observe({'status': 'RUNNING', 'chat': {'log': rows}})
    while True:
        ui.wait(0.1)
"""
        screen = TerminalScreen(
            [sys.executable, "-c", script], cwd=ROOT, env=os.environ, cols=140, rows=40, tmux=require_tmux(self)
        )
        self.addCleanup(screen.close)
        screen.wait_for(
            lambda shown: all(
                text in " ".join(shown.split())
                for text in (
                    "Checking that [partial]",
                    "I'm checking; I'll report back.",
                    "[earlier text omitted]",
                    "Which codes should history show?",
                )
            ),
            timeout=10,
            what="readable labels, apostrophes, streaming notice and complete newest words",
        )
        screen.type("/live thinking")
        screen.keys("Enter")
        screen.wait_for(
            lambda shown: "[thinking] Partial thought received." in shown and "Found an [partial]" in shown,
            what="thinking remains available only on request",
        )

    def test_running_view_has_one_default_conversation_and_optional_live_details(self):
        # Real curses rendering of a public view; no provider or task is launched.
        script = """
import sys
sys.path.insert(0, 'tools')
from autocode_chat_tui import ChatTUI
with ChatTUI() as ui:
    ui.observe({'status': 'RUNNING', 'chat': {'log': []}})
    while True:
        ui.wait(0.1)
"""
        screen = TerminalScreen(
            [sys.executable, "-c", script], cwd=ROOT, env=os.environ, cols=120, rows=20, tmux=require_tmux(self)
        )
        self.addCleanup(screen.close)
        screen.wait_for(
            lambda shown: "LIVE · all agents" not in shown and "AutoCode is working" in shown,
            what="one conversation before the first progress message",
        )
        screen.type("/live off")
        screen.keys("Enter")
        screen.wait_for(lambda shown: "LIVE · all agents" not in shown, what="explicitly hidden live pane")
        screen.resize(120, 30)
        screen.wait_for(
            lambda shown: (
                len(shown.splitlines()) == 30 and "AutoCode chat" in shown and "LIVE · all agents" not in shown
            ),
            what="hidden live pane after resize",
        )
        screen.type("/live all")
        screen.keys("Enter")
        screen.wait_for(
            lambda shown: (
                "LIVE · all agents" in shown and "Working; waiting for a plain-English progress update." in shown
            ),
            what="explicitly restored live pane",
        )


class MouseHistoryScreenTests(unittest.TestCase):
    def test_approval_prompt_does_not_move_the_plan_under_the_reader(self):
        script = """
import sys
sys.path.insert(0, 'tools')
from autocode_chat_tui import ChatTUI
with ChatTUI() as ui:
    ui.say('Plan to approve:\\n' + '\\n'.join(f'READING-LINE-{n:03d}' for n in range(100)))
    while ui._composer.text != 'ready':
        ui.wait(0.1)
    try:
        ui.ask('Approve this plan?')
    except EOFError:
        ui.say('No approval given.')
"""
        with TerminalScreen(
            [sys.executable, "-c", script], cwd=ROOT, env=os.environ, cols=100, rows=25, tmux=require_tmux(self)
        ) as screen:
            screen.wait_for("Plan to approve:")
            screen.keys("NPage")
            before = screen.wait_for(
                lambda shown: "History" in shown and "READING-LINE-" in shown and "Plan to approve:" not in shown
            )
            first = next(line for line in before.splitlines() if "READING-LINE-" in line)
            screen.type("ready")
            after = screen.wait_for("Approve this plan?")
            self.assertEqual(first, next(line for line in after.splitlines() if "READING-LINE-" in line))
            screen.keys("C-d")
            self.assertEqual(0, screen.exit_status())

    def test_incomplete_mouse_packet_cannot_stall_the_status_loop(self):
        script = """
import sys
sys.path.insert(0, 'tools')
from autocode_chat_tui import ChatTUI
with ChatTUI() as ui:
    ui.say('Ready for input probe')
    while ui._composer.text != 'probe':
        ui.wait(0.1)
    ui.say('Input probe armed')
    ui.wait(1.0)
    ui.say('Status polling continued')
    while True:
        ui.wait(0.1)
"""
        screen = TerminalScreen(
            [sys.executable, "-c", script], cwd=ROOT, env=os.environ, cols=100, rows=25, tmux=require_tmux(self)
        )
        self.addCleanup(screen.close)
        screen.wait_for("Ready for input probe")
        screen.type("probe")
        screen.wait_for("Input probe armed")
        screen.type("\x1b[M")  # Truncated X10 packet: ncurses must not block waiting for its three bytes.
        screen.wait_for("Status polling continued", timeout=5)
        screen.keys("C-u")
        screen.type("after")
        screen.wait_for(
            lambda shown: composer_text(shown) == "after", what="typing works after a truncated mouse packet"
        )

    def test_wheel_and_page_keys_reach_start_and_keep_position_during_updates(self):
        script = """
import sys
sys.path.insert(0, 'tools')
from autocode_chat_tui import ChatTUI
with ChatTUI() as ui:
    ui.say('You: ORIGINAL REQUEST')
    for n in range(45):
        ui.say(f'AutoCode: history item {n}')
    while True:
        ui.wait(0.1)
        if ui._composer.text == 'update':
            ui.say('AutoCode: NEW LIVE UPDATE')
            ui._composer.clear()
"""
        screen = TerminalScreen(
            [sys.executable, "-c", script], cwd=ROOT, env=os.environ, cols=100, rows=25, tmux=require_tmux(self)
        )
        self.addCleanup(screen.close)
        screen.wait_for("history item 44")
        screen.type("\x1b[M`%&" * 80)
        screen.wait_for(
            lambda shown: "ORIGINAL REQUEST" in shown and "History" in shown,
            timeout=10,
            what="wheel reaches the first request",
        )
        screen.type("update")
        # PageDown demonstrates the event loop processed the appended message; PageUp returns to the start.
        screen.keys("NPage")
        screen.wait_for(lambda shown: "ORIGINAL REQUEST" not in shown and "History" in shown)
        screen.keys("PPage")
        screen.wait_for("ORIGINAL REQUEST")
        screen.type("\x1b[Ma%&" * 80)
        screen.wait_for(
            lambda shown: "NEW LIVE UPDATE" in shown and "ORIGINAL REQUEST" not in shown,
            timeout=10,
            what="wheel returns to the latest message",
        )


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
        screen.wait_for("Type /diff N")  # the listing, not the closing summary naming the same files
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
    """The selected chat's identity, excluding the independently changing spend and attention badge."""
    return re.split(r"\s+(?:chat \$|\$|F8:)", screen.splitlines()[0], maxsplit=1)[0].rstrip()


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
