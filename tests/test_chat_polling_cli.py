"""The real TUI attached to a separately launched, gated offline task controller."""

import fcntl
import os
import pty
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

from tests.test_agent_step import fixture_workspace
from tests.test_chat_client import drain_terminal, read_terminal
from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS

ROOT = Path(__file__).resolve().parents[1]
AUTOCODE = ROOT / "tools" / "autocode.py"


class AttachedTerminal(unittest.TestCase):
    def test_help_details_pause_and_completion_with_an_external_worker(self):
        workspace, config = fixture_workspace(self)
        run_dir = workspace / ".autocode" / "runs" / "attached"
        (workspace / ".autocode").mkdir()
        gate = workspace / ".autocode" / "terminal-test-gate"
        ready = workspace / ".autocode" / "terminal-test-ready"
        os.mkfifo(gate)
        provider = Path(config["PATH"].split(os.pathsep)[0]) / "codex"
        source = provider.read_text()
        source = source.replace(
            '    task = data.get("current_task") or {}',
            '    if stage == "requirements_gather":\n'
            f"        Path({str(ready)!r}).touch()\n"
            f"        with open({str(gate)!r}) as gate:\n"
            "            gate.read()\n"
            '    task = data.get("current_task") or {}',
        )
        provider.write_text(source)
        env = {**os.environ, **config, "TERM": "xterm-256color"}
        log = tempfile.TemporaryFile("w+")  # noqa: SIM115 - unittest cleanup owns the worker log
        self.addCleanup(log.close)
        worker = subprocess.Popen(
            [
                sys.executable,
                str(AUTOCODE),
                BRIEF,
                "--workspace",
                str(workspace),
                "--new-run-dir",
                str(run_dir),
                "--in-place",
                "--no-chat",
                "--spec-first",
                *FIXTURE_OPTIONS,
            ],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            process_group=0,
        )
        self.addCleanup(self.stop, worker)
        run = taskrun.TaskRun(workspace, run_dir, env=env)
        deadline = time.monotonic() + 90
        while not ready.exists():
            if worker.poll() is not None or time.monotonic() > deadline:
                log.seek(0)
                self.fail("external controller did not reach the gated fake provider: " + log.read()[-3000:])
            # Wait for an actual provider event, not a latency assertion.
            time.sleep(0.02)
        leader, follower = pty.openpty()
        self.addCleanup(os.close, leader)
        fcntl.ioctl(follower, termios.TIOCSWINSZ, struct.pack("HHHH", 34, 120, 0, 0))
        viewer = subprocess.Popen(
            [
                sys.executable,
                str(AUTOCODE),
                "chat",
                "--workspace",
                str(workspace),
                "--run-dir",
                str(run_dir),
            ],
            env=env,
            stdin=follower,
            stdout=follower,
            stderr=follower,
        )
        os.close(follower)
        self.addCleanup(self.stop, viewer)
        read_terminal(leader, until=b"ctrl-c asks AutoCode", seconds=30)
        started = time.monotonic()
        os.write(leader, b"/help\r")
        read_terminal(leader, until=b"List these commands", seconds=10)
        help_seconds = time.monotonic() - started
        self.assertIsNone(worker.poll(), "help arrived before the provider gate was released")
        os.write(leader, b"/details\r")
        # The command's echo is not its answer: wait for the listing's own last line, so
        # /pause is typed only after the details response is on screen.
        read_terminal(leader, until=b"does not measure meaningful work progress", seconds=10)
        os.write(leader, b"/pause\r")
        read_terminal(leader, until=b"external worker", seconds=15)
        self.assertIsNone(worker.poll(), "pause requests do not interrupt the external worker")
        with open(gate, "w") as release:
            release.write("continue")
        # Keep the terminal drained while waiting on a process rather than on screen text:
        # curses blocks in its write once the pty's buffer fills, which stalls both the
        # redraw the next wait is looking for and the exit being waited on.
        if not drain_terminal(leader, lambda: worker.poll() is not None, seconds=90):
            log.seek(0)
            self.fail("the external worker never finished: " + log.read()[-3000:])
        self.assertEqual(2, worker.wait(timeout=15))
        read_terminal(leader, until=b"ctrl-d quit", seconds=30)
        os.write(leader, b"\x04")
        self.assertTrue(
            drain_terminal(leader, lambda: viewer.poll() is not None, seconds=15),
            "the viewer did not leave after Ctrl-D",
        )
        self.assertEqual(2, viewer.wait(timeout=15))
        view = run.status()
        self.assertFalse((view.get("controller") or {}).get("alive"))
        self.assertFalse(view.get("done"), "a paused requirements step is not task completion")
        self.assertFalse((workspace / "greet.py").exists(), "the viewer did not approve a plan or start Builder")
        print(f"Attached real TUI: /help rendered in {help_seconds:.3f}s while external provider stayed gated")

    @staticmethod
    def stop(child):
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()

    def test_hung_status_does_not_block_keys_or_a_healthy_chat_and_is_reaped(self):
        workspace, config = fixture_workspace(self)
        current, other = workspace / "current", workspace / "healthy"
        ready, complete = workspace / "hung-ready", workspace / "complete"
        healthy_ready, healthy_gate = workspace / "healthy-ready", workspace / "healthy-gate"
        expire = workspace / "expire-status"
        os.mkfifo(healthy_gate)
        backend = workspace / "status.py"
        backend.write_text("""import json, os, pathlib, signal, sys
root = pathlib.Path(sys.argv[1])
run = sys.argv[sys.argv.index('--run-dir') + 1]
if run.endswith('current'):
    count = root / 'reads'
    number = int(count.read_text()) + 1 if count.exists() else 1
    count.write_text(str(number))
    if number > 1 and not (root / 'complete').exists():
        (root / 'hung-ready').write_text(str(os.getpid()))
        signal.pause()
    view = {'done': (root / 'complete').exists(), 'liveness': {'kind': None if (root / 'complete').exists() else 'supervised'}, 'needs': {'kind': 'continue'}}
else:
    if not (root / 'healthy-released').exists():
        (root / 'healthy-ready').touch()
        with open(root / 'healthy-gate') as gate:
            gate.read()
        (root / 'healthy-released').touch()
    view = {'done': True, 'chat': {'title': 'Healthy'}}
print(json.dumps({'view': view}))
""")
        viewer_script = workspace / "viewer.py"
        viewer_script.write_text(f"""import sys, time
sys.path.insert(0, {str(ROOT / "tools")!r})
from pathlib import Path
from autocode_chat_client import ChatClient
from autocode_chat_tui import ChatTUI
ui = ChatTUI()
client = ChatClient(Path({str(workspace)!r}), Path({str(current)!r}),
 command=(sys.executable, {str(backend)!r}, {str(workspace)!r}),
 say=ui.say, observe=ui.observe, wait=ui.wait, poll_seconds=0.1)
# Neither scheduling nor host load decides when this fault expires. Advance only
# after help and the healthy notification have been observed, then for recovery.
started = time.monotonic()
def clock():
 return started + 100 * Path({str(expire)!r}).exists() + 100 * Path({str(complete)!r}).exists()
client._poller.clock = clock
client.others[{str(other)!r}] = (Path({str(workspace)!r}), None)
client.tab_order.append({str(other)!r})
client.tabs_changed = ui.tabs
try:
 client.status()
 client.watch()
finally:
 client.close()
 ui.close()
print('VIEWER-CLOSED')
""")
        leader, follower = pty.openpty()
        self.addCleanup(os.close, leader)
        fcntl.ioctl(follower, termios.TIOCSWINSZ, struct.pack("HHHH", 34, 120, 0, 0))
        viewer = subprocess.Popen(
            [sys.executable, str(viewer_script)],
            stdin=follower,
            stdout=follower,
            stderr=follower,
            env={**os.environ, **config, "TERM": "xterm-256color"},
        )
        os.close(follower)
        self.addCleanup(self.stop, viewer)
        if not drain_terminal(leader, lambda: ready.exists() and healthy_ready.exists(), seconds=15):
            self.fail("fault-injected status read never started")
        hung_pid = int(ready.read_text())
        started = time.monotonic()
        os.write(leader, b"/help\r")
        # Wait for the help response, not its typeahead menu: only the response lists
        # /pause. The healthy response is deliberately still gated: help cannot depend
        # on its order. While AutoCode works, help lists only what works now (#2561).
        shown = read_terminal(leader, until=b"Ask AutoCode to pause", seconds=3)
        help_seconds = time.monotonic() - started
        self.assertNotIn(b"Leave the chat", shown)
        self.assertNotIn(b"Another chat finished", shown)
        os.kill(hung_pid, 0)
        with healthy_gate.open("w") as release:
            release.write("continue")
        shown = read_terminal(leader, until=b"Another chat finished", seconds=10)
        self.assertIn(b"Another chat finished", shown, "healthy read was observed while the current read hung")
        os.kill(hung_pid, 0)
        expire.touch()
        read_terminal(leader, until=b"status read timed out", seconds=10)
        with self.assertRaises(ProcessLookupError):
            os.kill(hung_pid, 0)
        complete.touch()
        read_terminal(leader, until=b"VIEWER-CLOSED", seconds=15)
        self.assertEqual(0, viewer.wait(timeout=5))
        print(
            f"Hung status real TUI: /help rendered in {help_seconds:.3f}s; healthy chat updated; timed-out child reaped"
        )


class ResumedQuestionTerminal(unittest.TestCase):
    def test_external_resume_removes_stale_question_without_a_reply_or_second_action(self):
        from tests.terminal_screen import TerminalScreen, require_tmux

        tmux = require_tmux(self)
        with tempfile.TemporaryDirectory(prefix="resumed-question-") as folder:
            root = Path(folder)
            run = root / "saved-run"
            backend = root / "public_status.py"
            backend.write_text("""import json, pathlib, sys
root = pathlib.Path(__file__).parent
with (root / 'commands.jsonl').open('a') as out:
    out.write(json.dumps(sys.argv[1:]) + '\\n')
if '--status' not in sys.argv:
    sys.exit('No action was offered to this viewer')
phase = (root / 'phase').read_text() if (root / 'phase').exists() else 'waiting'
view = {'workspace': str(root), 'run_dir': str(root / 'saved-run'), 'done': phase == 'done'}
if phase == 'waiting':
    view.update(status='PAUSED_JOB', needs={'kind': 'answer', 'questions': [{'id':'route-tester', 'question':'Choose the recovery model'}], 'route': {'question_id':'route-tester', 'role':'tester', 'job':'Tester', 'cause':'timeout', 'stopped_model':'kept-model'}}, next_command='autocode --answer route-tester=MODEL')
elif phase == 'running':
    view.update(status='RUNNING', needs={'kind':'continue'}, liveness={'kind':'supervised'}, progress={'line':'Tester working'})
else:
    view.update(status='DONE', needs={'kind':'none'})
print(json.dumps({'view':view}))
""")
            viewer = root / "viewer.py"
            viewer.write_text(f"""import sys
from pathlib import Path
sys.path.insert(0, {str(ROOT / "tools")!r})
from autocode_chat_client import ChatClient
from autocode_chat_tui import ChatTUI
ui=ChatTUI()
client=ChatClient(Path({str(root)!r}), Path({str(run)!r}), command=(sys.executable,{str(backend)!r}), ask=ui.ask, say=ui.say, observe=ui.observe, wait=ui.wait, poll_seconds=0.05)
ui.idle=client.poll
ui.bind(client.slash)
try:
    result=client.converse()
finally:
    ui.close()
raise SystemExit(result)
""")
            screen = TerminalScreen(
                [sys.executable, str(viewer)],
                cwd=root,
                env={**os.environ, "TERM": "xterm-256color"},
                cols=120,
                rows=34,
                tmux=tmux,
            )
            self.addCleanup(screen.close)
            screen.wait_for("? Which model should the Tester continue on?", timeout=15)
            screen.type("unsent draft")
            screen.wait_for(lambda shown: "unsent draft" in shown and "? Which model" in shown, timeout=10)
            (root / "phase").write_text("running")
            shown = screen.wait_for(
                lambda shown: (
                    "RUNNING" in shown
                    and "? Which model" not in shown
                    and "ctrl-c asks AutoCode" in shown
                    and "unsent draft" in shown
                ),
                timeout=10,
                what="working view without the retired model question",
            )
            self.assertNotIn("? Which model", shown)
            (root / "phase").write_text("done")
            self.assertEqual(0, screen.exit_status(timeout=15))
            import json

            calls = [json.loads(line) for line in (root / "commands.jsonl").read_text().splitlines()]
            self.assertTrue(calls)
            self.assertTrue(all("--status" in call for call in calls), calls)
