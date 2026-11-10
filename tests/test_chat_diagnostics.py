"""Core command errors remain readable, bounded display text, never actions."""

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_chat_client as chat
import autocode_client_actions as actions

from tests import ROOT
from tests.terminal_screen import TerminalScreen, require_tmux


class CommandDiagnosticTests(unittest.TestCase):
    def diagnostic(self, text, **kwargs):
        return importlib.import_module("autocode_chat_diagnostics").command_error(text, **kwargs)

    def test_json_error_displays_its_reason_without_the_transport_envelope(self):
        reason = "Repository has a detached base; select its delivery base branch in the catalog"
        self.assertEqual(reason, self.diagnostic(json.dumps({"error": reason})))

    def test_unrecognized_output_is_preserved_as_bounded_diagnostic_text(self):
        for text in ("Provider cleanup failed", '{"error":', '{"status": "paused"}', '{"error": null}', "[1]"):
            with self.subTest(text=text):
                self.assertEqual(text, self.diagnostic(text))
        self.assertEqual("x" * 2000, self.diagnostic("y" * 50 + "x" * 2000))
        self.assertEqual("x" * 2000, self.diagnostic(json.dumps({"error": "y" * 50 + "x" * 2000})))

    def test_secrets_are_redacted_from_structured_and_plain_errors(self):
        for text in ("Stopped with fixture-private-value", json.dumps({"error": "Stopped with fixture-private-value"})):
            with self.subTest(text=text):
                shown = self.diagnostic(text, secret_values=("fixture-private-value",))
                self.assertNotIn("fixture-private-value", shown)
                self.assertIn("Stopped with", shown)

    def test_failed_coordination_displays_the_reason_and_does_not_retry(self):
        client = chat.ChatClient(Path("/tmp/diagnostic-chat"), Path("/tmp/diagnostic-chat/run"))
        reason = "Repository has a detached base; select its delivery base branch in the catalog"
        child = SimpleNamespace(returncode=2, poll=Mock(return_value=2))

        def launch(*args, **kwargs):
            kwargs["stdout"].write(json.dumps({"error": reason}).encode("utf-8"))
            kwargs["stdout"].flush()
            return child

        with patch.object(chat.subprocess, "Popen", side_effect=launch) as process:
            with self.assertRaises(actions.ClientGap) as caught:
                client.advance(["coordinate", "advance", "--run-dir", str(client.run_dir), "--json"])
        self.assertEqual("Coordination stopped: " + reason, str(caught.exception))
        process.assert_called_once()

    def test_refused_continue_keeps_core_reason_instead_of_generic_no_progress(self):
        client = chat.ChatClient(Path("/tmp/diagnostic-chat"), Path("/tmp/diagnostic-chat/run"))
        self.addCleanup(client.close)
        view = {"status": "RUNNING", "next_stage": "requirements_gather"}
        client._status_view = view
        client.status = Mock(return_value=view)
        reason = "Another AutoCode run is working in this checkout; nothing was changed."
        child = SimpleNamespace(returncode=2, poll=Mock(return_value=2))

        def launch(*args, **kwargs):
            kwargs["stdout"].write(reason.encode("utf-8"))
            kwargs["stdout"].flush()
            return child

        with patch.object(chat.subprocess, "Popen", side_effect=launch) as process:
            with self.assertRaises(actions.ClientGap) as caught:
                client.advance(["--no-chat"])
        self.assertEqual("AutoCode action stopped: " + reason, str(caught.exception))
        process.assert_called_once()


class CoordinationDiagnosticTerminalTests(unittest.TestCase):
    def test_public_json_error_is_readable_and_keeps_the_composer_without_retry(self):
        self.check_screen(coordination=True)

    def test_checkout_refusal_stays_visible_instead_of_no_progress(self):
        self.check_screen(coordination=False)

    def check_screen(self, *, coordination):
        reason = (
            "A repository checkout needs a delivery branch."
            if coordination
            else "Another AutoCode run is working in this checkout; nothing was changed."
        )
        response = json.dumps({"error": reason}) if coordination else reason
        prefix = "Coordination stopped: " if coordination else "AutoCode action stopped: "
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            backend = root / "backend.py"
            backend.write_text(f"""
import json, sys
from pathlib import Path
root = Path({str(root)!r})
if '--status' in sys.argv:
    print(json.dumps({{'view': {{'workspace': str(root), 'run_dir': str(root / 'run'),
        'status': 'RUNNING', 'next_stage': 'requirements_gather',
        'needs': {{'kind': 'continue'}}, 'chat': {{'log': []}}}}}}))
else:
    with (root / 'actions').open('a') as out:
        out.write('advance\\n')
    print({response!r})
    raise SystemExit(2)
""")
            script = f"""
import sys
from pathlib import Path
sys.path.insert(0, 'tools')
from autocode_chat_client import ChatClient
from autocode_chat_tui import ChatTUI
root = Path({str(root)!r})
with ChatTUI() as ui:
    client = ChatClient(root, root / 'run', command=(sys.executable, {str(backend)!r}),
        observe=ui.observe, unavailable=ui.status_unavailable, say=ui.say, ask=ui.ask,
        wait=ui.wait, poll_seconds=0.1)
    ui.bind(client.slash, run=True)
    ui.idle = client.poll
    if {coordination!r}:
        client.step = lambda: client.advance(['coordinate', 'advance', '--run-dir', str(root / 'run'), '--json'])
    code = client.converse()
raise SystemExit(code)
"""
            with TerminalScreen(
                [sys.executable, "-c", script], cwd=ROOT, env=os.environ, cols=99, rows=30, tmux=require_tmux(self)
            ) as screen:
                text = screen.wait_for(
                    lambda text: all(
                        item in text
                        for item in (
                            prefix + reason,
                            "/quit",
                            "╰─",
                        )
                    ),
                    timeout=10,
                )
                self.assertNotIn('{"error":', text)
                self.assertNotIn("made no progress", text)
                screen.type("/quit")
                screen.keys("Enter")
                self.assertEqual(2, screen.exit_status())
            self.assertEqual(["advance"], (root / "actions").read_text().splitlines())


if __name__ == "__main__":
    unittest.main()
