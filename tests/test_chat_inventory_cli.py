"""Real terminal input against a controlled public CLI with 25 listed runs.

The command fixture implements only runs/--status. It does not create 25 core
controllers or qualify model delivery; every task action would fail the test.
"""

import contextlib
import json
import os
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests.terminal_screen import TerminalScreen, require_tmux
from tests.test_chat_screens import composer_text

ROOT = Path(__file__).resolve().parents[1]


class RunPickerPresentationTests(unittest.TestCase):
    def test_inventory_leaves_the_terminal_when_work_resumes(self):
        script = """
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, 'tools')
import autocode_chat_client as chat
from autocode_chat_tui import ChatTUI
workspace = Path('/campaign/picker-fixture')
rows = [{'workspace': str(workspace), 'run_dir': str(workspace / name),
         'title': 'Picker entry ' + name, 'changeset': 'changeset-fixture'} for name in ('one', 'two')]
history = chat.History(workspace)
with patch.object(chat, 'project_runs', return_value=rows), patch.object(chat, 'every_project_runs', return_value=[]):
    with ChatTUI() as ui:
        ui.say('You: Keep my actual request.')
        ui.bind(history.slash)
        ui._run('/runs', True)
        ui.ask('Choose a chat: ')
        ui.observe({'status': 'RUNNING', 'liveness': {'kind': 'supervised'},
                    'progress': {'stage': 'Planner'}, 'chat': {'log': [
                        {'id': 'work', 'agent': 'Planner', 'kind': 'message', 'text': 'Checking the real task.'}]}})
        ui.ask('Current task: ')
"""
        with TerminalScreen(
            [sys.executable, "-c", script], cwd=ROOT, env=os.environ, cols=120, rows=30, tmux=require_tmux(self)
        ) as screen:
            screen.wait_for(
                lambda text: all(
                    part in text for part in ("/campaign/picker-fixture", "changeset-fixture", "Choose a chat:", "╰─")
                ),
                timeout=10,
            )
            screen.keys("Enter")
            shown = screen.wait_for(
                lambda text: (
                    all(
                        part in text
                        for part in ("Current task:", "Checking the real task.", "Keep my actual request.", "╰─")
                    )
                    and "/campaign/picker-fixture" not in text
                    and "changeset-fixture" not in text
                ),
                timeout=10,
            )
            self.assertNotIn("Picker entry", shown)
            screen.keys("Enter")
            self.assertEqual(0, screen.exit_status())


BACKEND = """import json, os, pathlib, signal, sys
root = pathlib.Path(sys.argv[1])
args = sys.argv[2:]
def record(kind, **values):
 with open(root / 'calls', 'a') as output:
  output.write(json.dumps(dict(kind=kind, pid=os.getpid(), **values)) + '\\n')
if args[0] == 'runs':
 kind = 'project' if '--workspace' in args else 'all'
 record(kind)
 if (root / 'hold').exists():
  (root / (kind + '-held')).write_text(str(os.getpid()))
  signal.pause()
 print(json.dumps({'runs': json.loads((root / 'rows').read_text())}))
elif args[0] == '--status':
 run = args[args.index('--run-dir') + 1]
 record('status', run=run)
 if pathlib.Path(run).name == 'r23' and (root / 'hold-return-status').exists():
  old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGUSR1})
  marker = root / 'return-status-held'
  pending = root / 'return-status-held.tmp'
  pending.write_text(str(os.getpid()))
  pending.replace(marker)
  signal.sigwait({signal.SIGUSR1})
  signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
 print(json.dumps({'view': {'workspace': str(root), 'run_dir': run,
  'done': True, 'status': 'TASK_COMPLETE', 'needs': {'kind': 'none'},
  'chat': {'context': run, 'title': pathlib.Path(run).name, 'input': {'kind': 'follow_up'},
   'notice': f'Current public status: {pathlib.Path(run).name} / {os.getpid()}'},
  'execution': {'mode': 'durable_local', 'detach_supported': True}}}))
else:
 record('FORBIDDEN', args=args)
 sys.exit(4)
"""


class OverviewTerminal(unittest.TestCase):
    def test_25_listed_entries_open_only_selected_chats_and_held_listings_keep_keys_help_and_quit_live(self):
        with tempfile.TemporaryDirectory(prefix="inventory-terminal-") as folder:
            root = Path(folder)
            runs = [root / "runs" / f"r{number:02}" for number in range(25)]
            rows = [
                {"workspace": str(root), "run_dir": str(run), "title": f"Public entry {number:02}"}
                for number, run in enumerate(runs)
            ]
            (root / "rows").write_text(json.dumps(rows))
            (root / "backend.py").write_text(BACKEND)
            viewer = root / "viewer.py"
            viewer.write_text(f"""import sys
from pathlib import Path
sys.path.insert(0, {str(ROOT / "tools")!r})
from autocode_chat_client import ChatClient, History
from autocode_chat_tui import ChatTUI
root = Path({folder!r})
command = (sys.executable, str(root / 'backend.py'), str(root))
ui = ChatTUI()
client = ChatClient(root, Path({str(runs[0])!r}), command=command, ask=ui.ask,
 say=ui.say, observe=ui.observe, unavailable=ui.status_unavailable, wait=ui.wait)
client.history = History(root, command=command)
ui.set_context(root, client.run_dir)
ui.bind(client.slash)
client.switched, client.tabs_changed = ui.switch, ui.tabs
client.attention_changed, ui.idle = ui.attention, client.poll
try:
 code = client.converse()
finally:
 client.close()
 ui.close()
raise SystemExit(code)
""")
            screen = TerminalScreen(
                [sys.executable, str(viewer)],
                cwd=root,
                env={**os.environ, "TERM": "xterm-256color"},
                cols=99,  # below the width that shows the side panel by itself (#3070)
                rows=72,
                tmux=require_tmux(self),
            )
            try:
                screen.wait_for("Anything else?", 15)
                screen.type("/runs")
                screen.keys("Enter")
                initial = screen.wait_for(
                    lambda frame: (
                        "Public entry 24" in frame
                        and "Public entry 00" in frame
                        and "? This task is complete. Anything else?" in frame
                        and composer_text(frame) == ""
                    ),
                    10,
                )
                self.assertIn("Public entry 24", initial, "the first requested list appears without a second command")
                screen.type("/runs")
                screen.keys("Enter")
                # The previous list can still be visible while /runs is being
                # typed. Its slash menu hides the final row until Enter is
                # handled and the conversation has finished redrawing.
                shown = screen.wait_for(
                    lambda frame: (
                        "Public entry 24" in frame
                        and "Public entry 00" in frame
                        and "Listed chats are not opened tabs" in frame
                        and composer_text(frame) == ""
                    ),
                    10,
                )
                self.assertIn("Public entry 00", shown)
                self.assertIn("Listed chats are not opened tabs", shown)
                calls = self.calls(root)
                self.assertEqual({str(runs[0])}, {row["run"] for row in calls if row["kind"] == "status"})
                before = {row["pid"] for row in calls if row["kind"] == "status"}
                screen.type("/open 2")
                screen.keys("Enter")
                self.wait_for_current_status(screen, root, runs[23], before)
                screen.type("B draft 你好")
                before = {row["pid"] for row in self.calls(root) if row["kind"] == "status"}
                screen.keys("Left", "C-Left")
                # A restored header/prompt is display history, not a fresh
                # status view. Wait for this read before another tab action.
                self.wait_for_current_status(screen, root, runs[0], before)
                screen.wait_for(
                    lambda shown: (
                        "· r00" in shown.splitlines()[0]
                        and composer_text(shown) == ""
                        and "? This task is complete. Anything else?" in shown
                    ),
                    10,
                )
                before = {row["pid"] for row in self.calls(root) if row["kind"] == "status"}
                gate, held_status = root / "hold-return-status", root / "return-status-held"
                gate.touch()
                screen.keys("C-Right")
                screen.wait_for(
                    lambda shown: composer_text(shown) == "B draft 你好" and held_status.is_file(),
                    10,
                    what="the cached draft while this tab's fresh status is held",
                )
                # Cached text is display history. Release and observe the new
                # public view before sending commands through its prompt.
                gate.unlink()
                os.kill(int(held_status.read_text()), signal.SIGUSR1)
                self.wait_for_current_status(screen, root, runs[23], before)
                screen.wait_for(lambda shown: composer_text(shown) == "B draft 你好", 10)
                screen.type("X")
                screen.wait_for(lambda shown: composer_text(shown) == "B draft 你X好", 10)
                screen.keys("C-u")
                (root / "hold").touch()
                screen.type("/runs")
                screen.keys("Enter")
                screen.wait_for(
                    lambda shown: all((root / (kind + "-held")).exists() for kind in ("project", "all")), 10
                )
                held = [int((root / (kind + "-held")).read_text()) for kind in ("project", "all")]
                for pid in held:
                    os.kill(pid, 0)
                screen.resize(120, 34)
                screen.type("draft 你好")
                screen.keys("Left")
                started = time.monotonic()
                screen.type("X")
                screen.wait_for(lambda shown: composer_text(shown) == "draft 你X好", 1)
                key_ms = (time.monotonic() - started) * 1000
                screen.keys("C-u")
                started = time.monotonic()
                screen.type("/help")
                screen.keys("Enter")
                screen.wait_for("List these commands", 1)
                help_ms = (time.monotonic() - started) * 1000
                for pid in held:
                    os.kill(pid, 0)  # commands were handled before either listing was released or timed out
                screen.type("/quit")
                screen.keys("Enter")
                self.assertEqual(0, screen.exit_status(3))
                calls = self.calls(root)
                self.assertFalse([row for row in calls if row["kind"] == "FORBIDDEN"])
                self.assertEqual(
                    {str(runs[0]), str(runs[23])}, {row["run"] for row in calls if row["kind"] == "status"}
                )
                for pid in {row["pid"] for row in calls}:
                    with self.assertRaises(ProcessLookupError):
                        os.kill(pid, 0)
                print(
                    f"25 public entries, 2 opened statuses: key {key_ms:.1f}ms; /help {help_ms:.1f}ms; held project/global reads reaped on /quit"
                )
            finally:
                screen.close()
                for row in self.calls(root):
                    with contextlib.suppress(ProcessLookupError):
                        os.kill(row["pid"], signal.SIGKILL)

    @staticmethod
    def calls(root):
        path = root / "calls"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def wait_for_current_status(self, screen, root, run, previous):
        def ready(shown):
            return (
                f"· {run.name}" in shown.splitlines()[0]
                and "? This task is complete. Anything else?" in shown
                and any(
                    f"Current public status: {run.name} / {row['pid']}" in shown
                    for row in self.calls(root)
                    if row["kind"] == "status" and row["run"] == str(run) and row["pid"] not in previous
                )
            )

        screen.wait_for(ready, 10, what=f"a fresh public status for {run.name}")
