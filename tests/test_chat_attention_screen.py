"""Real curses drawer, Unicode cursor, resize and typing during a held acknowledgement."""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests import ROOT
from tests.terminal_screen import TerminalScreen, require_tmux
from tests.test_chat_attention import row
from tests.test_chat_screens import composer_text


class AttentionScreen(unittest.TestCase):
    def test_acknowledging_another_run_never_takes_over_the_draft_or_blocks_typing(self):
        tmux = require_tmux(self)
        program = """import json,pathlib,sys
sys.path.insert(0,sys.argv[1])
import autocode_chat_attention as attention
from autocode_chat_client import ChatClient
from autocode_chat_tui import ChatTUI
control,result,provider=map(pathlib.Path,sys.argv[2:])
seen=None
with ChatTUI() as ui:
 client=ChatClient(pathlib.Path.cwd(),pathlib.Path.cwd()/'run-b',command=(sys.executable,str(provider)),say=ui.say)
 client.attention_changed=ui.attention
 ui.set_context(client.workspace,client.run_dir)
 def idle():
  global seen
  raw=control.read_text()
  if seen!=raw:
   client._attention.listing(raw,'',started=0)
   seen=raw
  attention.poll_acknowledgement(client)
  ui.attention(client._attention.summary())
 ui.idle=idle
 ui.bind(client.slash)
 idle()
 try:
  answer=ui.ask('Draft on B. Inspect A with F8.')
  result.write_text(json.dumps({'draft':answer,'run':str(client.run_dir)}))
 finally:client.close()
"""
        provider = """import json,pathlib,sys
gate=pathlib.Path(__file__).with_suffix('.gate')
pathlib.Path(__file__).with_suffix('.ready').touch()
with gate.open() as stream:stream.read()
args=sys.argv[1:]
assert args[:2]==['attention','ack'],args
def option(name):return args[args.index(name)+1]
print(json.dumps({'acknowledged':True,'receipt':{'item_id':option('--item-id'),'workspace':option('--workspace'),'run_dir':option('--run-dir')}}))
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            control, result, executable = root / "control.json", root / "result.json", root / "provider.py"
            gate = executable.with_suffix(".gate")
            os.mkfifo(gate)
            executable.write_text(provider)
            current = row()
            current["attention"]["item"]["summary"] = "Long task 你好 " * 8
            control.write_text(json.dumps({"runs": [current]}))
            with TerminalScreen(
                [sys.executable, "-c", program, str(ROOT / "tools"), str(control), str(result), str(executable)],
                cwd=root,
                env={**os.environ, "TERM": "xterm-256color"},
                cols=80,
                rows=24,
                tmux=tmux,
            ) as screen:
                screen.wait_for("Draft on B")
                screen.paste("draft 你好")
                screen.wait_for(lambda shown: composer_text(shown) == "draft 你好")
                screen.keys("Left", "F8")
                shown = screen.wait_for("ATTENTION")
                self.assertIn("1 unread", shown)
                self.assertIn("[unread]", shown)
                self.assertIn("Plan approval needed", shown)
                screen.keys("a")
                screen.wait_for(
                    lambda shown: "Acknowledgement pending" in shown and executable.with_suffix(".ready").exists()
                )
                screen.keys("Escape")
                started = time.monotonic()
                screen.type("X")
                screen.wait_for(lambda shown: composer_text(shown) == "draft 你X好")
                key_seconds = time.monotonic() - started
                # The ack child is demonstrably still held, while the user's editor responds.
                self.assertFalse(result.exists())
                with gate.open("w") as release:
                    release.write("release")
                screen.wait_for("Alert acknowledged")
                self.assertEqual("draft 你X好", composer_text(screen.text()))
                screen.resize(120, 34)
                screen.keys("F8")
                shown = screen.wait_for("ATTENTION")
                self.assertIn("acknowledged", shown)
                self.assertIn("0 unread", shown)
                screen.keys("Escape", "Enter")
                self.assertEqual(0, screen.exit_status())
                saved = json.loads(result.read_text())
                self.assertEqual("draft 你X好", saved["draft"])
                self.assertEqual(str(root / "run-b"), saved["run"])
                print(
                    f"Attention real terminal: draft key rendered in {key_seconds:.3f}s while acknowledgement was held"
                )
