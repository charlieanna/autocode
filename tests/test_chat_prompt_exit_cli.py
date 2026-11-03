"""A queued terminal exit must survive the transition into a fresh question."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

from tests import ROOT
from tests.terminal_screen import TerminalScreen, require_tmux


class PromptExitTerminalTests(unittest.TestCase):
    def test_ctrl_d_queued_just_before_a_question_detaches_without_answering(self):
        with tempfile.TemporaryDirectory(prefix="prompt-exit-") as directory:
            root = Path(directory)
            ready, detached, answered = (root / name for name in ("ready", "detached", "answered"))
            script = f"""
import select, sys
from pathlib import Path
sys.path.insert(0, {str(ROOT / "tools")!r})
from autocode_chat_tui import ChatTUI
with ChatTUI() as ui:
    ui.say('AutoCode: Preparing the next question.')
    Path({str(ready)!r}).touch()
    assert select.select([sys.stdin], [], [], 10)[0], 'No terminal key arrived'
    try:
        response = ui.ask('Approve this plan? [y/N]: ')
    except EOFError:
        Path({str(detached)!r}).touch()
    else:
        Path({str(answered)!r}).write_text(response)
"""
            with TerminalScreen(
                [sys.executable, "-c", script], cwd=ROOT, env=os.environ, cols=100, rows=24, tmux=require_tmux(self)
            ) as screen:
                screen.wait_for(lambda shown: ready.exists() and "Preparing the next question." in shown, 10)
                screen.keys("C-d")
                screen.wait_for(lambda _: detached.exists(), 5, what="queued Ctrl-D detaches at prompt transition")
                self.assertEqual(0, screen.exit_status(5))
                self.assertFalse(answered.exists(), "An exit key cannot submit an answer or approve a plan")
