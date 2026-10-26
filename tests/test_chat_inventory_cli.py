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
                cols=120,
                rows=72,
                tmux=require_tmux(self),
            )
            try:
                screen.wait_for("Anything else?", 15)
                screen.type("/runs")
                screen.keys("Enter")
                initial = screen.wait_for("Run list read finished", 10)
                self.assertIn("Public entry 24", initial, "the first requested list appears without a second command")
                screen.type("/runs")
                screen.keys("Enter")
                shown = screen.wait_for("Public entry 24", 10)
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
                        "· r00" in shown.splitlines()[0] and composer_text(shown) == "" and "? Anything else?" in shown
                    ),
                    10,
                )
                screen.keys("C-Right")
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
                and "? Anything else?" in shown
                and any(
                    f"Current public status: {run.name} / {row['pid']}" in shown
                    for row in self.calls(root)
                    if row["kind"] == "status" and row["run"] == str(run) and row["pid"] not in previous
                )
            )

        screen.wait_for(ready, 10, what=f"a fresh public status for {run.name}")
