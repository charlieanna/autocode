"""Agent prose renders italics while literal user input and code stay exact."""

import os
import sys
import unittest

from tests import ROOT
from tests.terminal_screen import TerminalScreen, require_tmux


class MarkdownTerminalTests(unittest.TestCase):
    def test_completed_agent_updates_render_inline_code_and_bold_in_the_terminal(self):
        script = """
import sys
sys.path.insert(0, 'tools')
from autocode_chat_tui import ChatTUI
with ChatTUI() as ui:
    ui.say('You: Keep `literal` **input** _exact_.')
    ui.observe({'status': 'WAITING_FOR_USER', 'next_stage': 'draft_plan',
        'needs': {'kind': 'answer'}, 'chat': {'log': [
            {'id': 'agent-update', 'agent': 'Planner', 'kind': 'message',
             'text': 'Use _Both_ for **new entries**; inspect `result_code`.'}
        ]}})
    ui.ask('Your reply: ')
"""
        with TerminalScreen(
            [sys.executable, "-c", script], cwd=ROOT, env=os.environ, cols=120, rows=30, tmux=require_tmux(self)
        ) as screen:
            shown = screen.wait_for(
                lambda text: all(
                    item in text
                    for item in (
                        "Planner",
                        "Use Both for new entries; inspect result_code.",
                        "Keep `literal` **input** _exact_.",
                        "╰─",
                    )
                ),
                timeout=10,
            )
            self.assertIn("Use Both for new entries; inspect result_code.", shown)
            self.assertNotIn("`result_code`", shown)
            self.assertNotIn("**new entries**", shown)
            screen.keys("Enter")
            self.assertEqual(0, screen.exit_status())

    def test_agent_asterisk_italics_are_readable_in_the_actual_terminal(self):
        script = """
import sys
sys.path.insert(0, 'tools')
from autocode_chat_tui import ChatTUI
with ChatTUI() as ui:
    ui.say('You: *typed* input')
    ui.say('AutoCode: Apply *only* *new* entries.')
    ui.say('AutoCode: Keep `*literal*` unchanged.')
    ui.observe({'status': 'WAITING_FOR_USER', 'next_stage': 'draft_plan',
        'needs': {'kind': 'answer'}, 'chat': {'log': [
            {'id': 'agent-update', 'agent': 'Planner', 'kind': 'message', 'text': 'Checking *only* *new* entries.'}
        ]}})
    ui.ask('Your reply: ')
"""
        with TerminalScreen(
            [sys.executable, "-c", script], cwd=ROOT, env=os.environ, cols=120, rows=30, tmux=require_tmux(self)
        ) as screen:
            shown = screen.wait_for(
                lambda text: all(
                    item in text
                    for item in (
                        "Apply only new entries.",
                        "Checking only new entries.",
                        "*typed* input",
                        "*literal*",
                        "╰─",
                    )
                ),
                timeout=10,
            )
            self.assertNotIn("*only*", shown)
            self.assertNotIn("*new*", shown)
            screen.keys("Enter")
            self.assertEqual(0, screen.exit_status())


if __name__ == "__main__":
    unittest.main()
