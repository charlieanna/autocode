"""A past command error must not look like the current state after work resumes."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import autocode_chat_tui as tui

from tests import ROOT
from tests.terminal_screen import TerminalScreen, require_tmux

WORKING = {"status": "RUNNING", "liveness": {"kind": "supervised"}, "needs": {"kind": "continue"}}


class ErrorHistoryTests(unittest.TestCase):
    def rendered(self, transcript):
        return "\n".join(text for _, text in transcript.lines(100))

    def test_fresh_running_work_labels_the_previous_stop_as_history_and_keeps_its_reason(self):
        transcript = tui.Transcript()
        transcript.add("Coordination stopped: A checkout needs a delivery branch.")
        transcript.lines(100)  # Exercise invalidation of a previously rendered stop.
        transcript.progress(WORKING)
        shown = self.rendered(transcript)
        self.assertIn("Earlier coordination error", shown)
        self.assertIn("A checkout needs a delivery branch.", shown)
        self.assertNotIn("Coordination stopped:", shown)
        transcript.progress(WORKING)
        self.assertEqual(shown, self.rendered(transcript))

    def test_a_checkpoint_or_unverified_worker_does_not_relabel_a_current_stop(self):
        for view in (
            {},
            {"status": "RUNNING", "needs": {"kind": "continue"}},
            {"status": "WAITING_FOR_USER", "needs": {"kind": "answer"}},
            {"status": "PAUSED_PROCESS_CLEANUP", "liveness": {"kind": "supervised"}},
        ):
            with self.subTest(view=view):
                transcript = tui.Transcript()
                transcript.add("Coordination stopped: Cleanup is unconfirmed.")
                transcript.progress(view)
                self.assertIn("Coordination stopped: Cleanup is unconfirmed.", self.rendered(transcript))
                self.assertNotIn("Earlier coordination error", self.rendered(transcript))

    def test_a_new_error_remains_current_until_a_later_running_observation(self):
        transcript = tui.Transcript()
        transcript.add("Coordination stopped: First reason.")
        transcript.progress(WORKING)
        transcript.add("Coordination stopped: Second reason.")
        shown = self.rendered(transcript)
        self.assertIn("Coordination stopped: Second reason.", shown)
        self.assertEqual(1, shown.count("Earlier coordination error"))
        transcript.progress(WORKING)
        shown = self.rendered(transcript)
        self.assertEqual(2, shown.count("Earlier coordination error"))
        self.assertNotIn("Coordination stopped:", shown)

    def test_regular_chat_and_agent_messages_are_not_rewritten(self):
        transcript = tui.Transcript()
        transcript.add("You: Why did it stop?")
        transcript.add("AutoCode: Checking the cause.")
        before = self.rendered(transcript)
        transcript.progress(WORKING)
        self.assertEqual(before, self.rendered(transcript))

    def test_tui_status_observation_updates_the_visible_history_without_a_task_action(self):
        with patch.object(tui.ChatTUI, "_open"):
            ui = tui.ChatTUI()
        ui._draw = Mock()
        ui.say("Coordination stopped: Local remote missing.")
        ui.observe(WORKING)
        shown = self.rendered(ui._transcript)
        self.assertIn("Earlier coordination error", shown)
        self.assertNotIn("Coordination stopped:", shown)
        self.assertIn("Local remote missing.", shown)


class ErrorHistoryTerminalTests(unittest.TestCase):
    def test_a_running_public_view_makes_the_old_error_clearly_historical(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            backend = root / "backend.py"
            backend.write_text(f"""
import json, sys
from pathlib import Path
assert sys.argv[1:] == ['--status']
Path({str(root / "calls.json")!r}).write_text(json.dumps(sys.argv[1:]))
print(json.dumps({{'view': {WORKING!r}}}))
""")
            script = f"""
import json, subprocess, sys
sys.path.insert(0, 'tools')
from autocode_chat_tui import ChatTUI
with ChatTUI() as ui:
    ui.say('You: Fix the history.')
    ui.say('Coordination stopped: Local remote missing.')
    view = json.loads(subprocess.check_output([sys.executable, {str(backend)!r}, '--status'], text=True))['view']
    ui.observe(view)
    ui.ask('Your reply: ')
"""
            with TerminalScreen(
                [sys.executable, "-c", script], cwd=ROOT, env=os.environ, cols=120, rows=30, tmux=require_tmux(self)
            ) as screen:
                text = screen.wait_for(
                    lambda text: all(
                        item in text for item in ("Earlier coordination error", "Local remote missing.", "╰─")
                    ),
                    timeout=10,
                )
                self.assertNotIn("Coordination stopped:", text)
                screen.keys("Enter")
                self.assertEqual(0, screen.exit_status())
            self.assertEqual(["--status"], json.loads((root / "calls.json").read_text()))


if __name__ == "__main__":
    unittest.main()
