"""Recent prose stays visible beside a real terminal's scrolled conversation and draft."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from tests import ROOT
from tests.terminal_screen import TerminalScreen, require_tmux


class RecentProgressTerminalTests(unittest.TestCase):
    def test_updates_roll_without_raw_tools_or_moving_the_draft_off_screen(self):
        with tempfile.TemporaryDirectory() as directory:
            updates = Path(directory) / "updates.json"
            updates.write_text(json.dumps(["Checking the history interface.", "Checking _only_ the result mapping."]))
            script = f"""
import json, sys
from pathlib import Path
sys.path.insert(0, 'tools')
from autocode_chat_tui import ChatTUI
updates = Path({str(updates)!r})
with ChatTUI() as ui:
    for n in range(60):
        ui.say(f'You: Earlier request {{n}}')
    previous = None
    while True:
        texts = updates.read_text()
        if texts != previous:
            previous = texts
            ui.observe({{'status': 'RUNNING', 'phase': 'PLANNING',
                'cost': {{'spent_usd': 1.25, 'complete': False}},
                'progress': {{'stage': 'Planner', 'headline': 'Planner working'}},
                'chat': {{'log': [{{'id': str(n), 'agent': 'Planner', 'kind': 'message', 'text': text}}
                                 for n, text in enumerate(json.loads(texts))]
                    + [{{'id': 'tool', 'agent': 'Planner', 'kind': 'command', 'text': 'bash -lc hidden-command'}},
                       {{'id': 'thought', 'agent': 'Planner', 'kind': 'thinking', 'text': 'hidden-reasoning'}}]}}}})
        ui.wait(0.1)
"""
            with TerminalScreen(
                [sys.executable, "-c", script],
                cwd=ROOT,
                env=os.environ,
                cols=100,
                rows=30,
                tmux=require_tmux(self),
            ) as screen:
                shown = screen.wait_for(
                    lambda shown: (
                        shown.count("Checking the history interface.") == 1
                        and shown.count("Checking only the result mapping.") == 1
                        and "latest updates" not in shown
                        and "╰─" in shown
                        and "Planner · $1.25+" in shown.splitlines()[-1]
                    ),
                    timeout=10,
                    what="one copy of each live progress message above the complete composer",
                )
                self.assertNotIn("│ NOW", shown)
                screen.type("my unsent draft")
                for _ in range(30):
                    screen.keys("PPage")
                screen.wait_for(
                    lambda shown: all(
                        text in shown
                        for text in (
                            "Earlier request 0",
                            "Planner · latest updates",
                            "Checking only the result mapping.",
                            "my unsent draft",
                            "╰─",
                        )
                    ),
                    what="old conversation with current progress and draft still visible",
                )
                replacement = updates.with_suffix(".next")
                replacement.write_text(
                    json.dumps(
                        [
                            "Checking the history interface.",
                            "Checking _only_ the result mapping.",
                            "Comparing the failure cases.",
                        ]
                    )
                )
                replacement.replace(updates)
                shown = screen.wait_for(
                    lambda shown: (
                        all(
                            text in shown
                            for text in (
                                "Earlier request 0",
                                "Planner · latest updates",
                                "Checking only the result mapping.",
                                "Comparing the failure cases.",
                                "my unsent draft",
                                "╰─",
                            )
                        )
                        and "Checking the history interface." not in shown
                    ),
                    what="new notes roll in without scrolling the person's history",
                )
                self.assertNotIn("hidden-command", shown)
                self.assertNotIn("hidden-reasoning", shown)
                screen.resize(70, 22)
                shown = screen.wait_for(
                    lambda shown: (
                        len(shown.splitlines()) == 22
                        and all(
                            text in shown
                            for text in (
                                "Planner · latest updates",
                                "Comparing the failure cases.",
                                "my unsent draft",
                                "╰─",
                            )
                        )
                    ),
                    what="progress and complete composer after shrinking the terminal",
                )
                self.assertIn("my unsent draft", shown.splitlines()[-3])
                for _ in range(30):
                    screen.keys("NPage")
                screen.wait_for(
                    lambda shown: (
                        shown.count("Checking only the result mapping.") == 1
                        and shown.count("Comparing the failure cases.") == 1
                        and "latest updates" not in shown
                        and "my unsent draft" in shown.splitlines()[-3]
                    ),
                    what="return to live history without duplicate progress or losing the draft",
                )
