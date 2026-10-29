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
from tests.test_chat_client import read_terminal
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
        self.assertEqual(2, worker.wait(timeout=90))
        read_terminal(leader, until=b"ctrl-d quit", seconds=30)
        os.write(leader, b"\x04")
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
        deadline = time.monotonic() + 15
        while not ready.exists() or not healthy_ready.exists():
            if viewer.poll() is not None or time.monotonic() > deadline:
                self.fail("fault-injected status read never started")
            time.sleep(0.01)
        hung_pid = int(ready.read_text())
        started = time.monotonic()
        os.write(leader, b"/help\r")
        # Wait for the full help response, not its typeahead menu. The healthy
        # response is deliberately still gated: help cannot depend on its order.
        shown = read_terminal(leader, until=b"Leave the chat", seconds=3)
        help_seconds = time.monotonic() - started
        self.assertIn(b"List these commands", shown)
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
