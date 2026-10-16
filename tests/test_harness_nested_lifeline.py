"""Harness loss and CLI death let nested offline worker keepers finish (#454)."""

import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_elapsed_timing as elapsed_timing
import autocode_process as native
import autocode_supervision_keeper as keeper

from scenarios.harness import processes as harness_processes

ROOT = Path(__file__).resolve().parents[1]

WORKER = """\
import json, os, signal, socket, sys
from pathlib import Path
sys.path.insert(0, {tools!r})
import autocode_supervision_cli as bridge

directory = Path(sys.argv[1])
channel = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
channel.connect({socket!r})
interrupted = []

def stop(signum, frame):
    if interrupted:
        return
    interrupted.append(signum)
    channel.sendall(b'T')
    if channel.recv(1) != b'X':
        raise SystemExit(3)
    (directory / 'result.json').write_text(json.dumps({{'status': 'PAUSED_INTERRUPTED'}}))
    raise SystemExit(2)

with bridge.guard(sys.argv[1:]):
    signal.signal(signal.SIGTERM, stop)
    channel.sendall((json.dumps({{'pid': os.getpid(), 'directory': str(directory)}}) + '\\n').encode())
    print('ready', flush=True)
    while True:
        signal.pause()
"""

CONTROLLER = """\
import json, os, signal, subprocess, sys
from pathlib import Path
sys.path.insert(0, {tools!r})
import autocode_supervision_cli as bridge
import autocode_worker_lifeline as lifeline

root = Path({root!r})
interrupted = []
def stop(signum, frame):
    if not interrupted:
        interrupted.append(signum)
        raise KeyboardInterrupt

with bridge.guard(sys.argv[1:]):
    signal.signal(signal.SIGTERM, stop)
    workers, writers, records = [], [], []
    for index in range(2):
        directory = root / str(index)
        directory.mkdir()
        record, reader, writer = lifeline.prepare(directory)
        records.append(record)
        writers.append(writer)
        child = subprocess.Popen(
            [sys.executable, '-u', str(root / 'worker.py'), str(directory), *lifeline.argv(reader)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            start_new_session=True, close_fds=True, pass_fds=(reader,))
        os.close(reader)
        workers.append(child)
        if child.stdout.readline().strip() != 'ready':
            raise SystemExit(3)
    (root / 'ready.json').write_text(json.dumps({{'pid': os.getpid(), 'records': records}}))
    try:
        while True:
            signal.pause()
    except KeyboardInterrupt:
        for writer in writers:
            os.close(writer)
        for child in workers:
            child.wait(timeout=30)
        lifeline.await_settled(records)
        (root / 'controller-result.json').write_text(json.dumps({{'status': 'PAUSED_INTERRUPTED'}}))
        raise SystemExit(2)
"""

HARNESS = """\
import os, sys, time
from pathlib import Path
sys.path.insert(0, {repo!r})
from scenarios.harness.processes import run_cli
root = Path({root!r})
run_cli([sys.executable, '-u', str(root / 'controller.py')], env=os.environ.copy(), cwd=root,
        timeout=120, lifeline={{'root': root / 'cli-calls', 'kind': 'start', 'deadline': time.monotonic() + 120}})
"""


@unittest.skipUnless(os.name == "posix", "POSIX lifeline ownership")
class NestedHarnessLifelineTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="ac454-nested-", dir="/tmp")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.owned = []
        self.addCleanup(self.stop_owned)
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(self.listener.close)
        address = str(self.root / "events.sock")
        self.listener.bind(address)
        self.listener.listen(2)
        self.listener.settimeout(15)
        (self.root / "worker.py").write_text(WORKER.format(tools=str(ROOT / "tools"), socket=address))
        (self.root / "controller.py").write_text(CONTROLLER.format(tools=str(ROOT / "tools"), root=str(self.root)))
        (self.root / "harness.py").write_text(HARNESS.format(repo=str(ROOT), root=str(self.root)))

    def stop_owned(self):
        for process in reversed(self.owned):
            with contextlib.suppress(native.psutil.Error):
                process.resume()
                process.kill()  # saved psutil identity, never a bare PID
        native.psutil.wait_procs(self.owned, timeout=5)

    def until(self, condition, message, seconds=15):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            # Poll a publication, not a timing-dependent fault trigger.
            time.sleep(0.02)
        self.fail(message)

    def receipt(self, path):
        try:
            return json.loads(Path(path).read_text())
        except (OSError, ValueError):
            return {}

    def run_loss(self, *, kill_harness):
        sentinel = subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True)
        self.owned.append(native.psutil.Process(sentinel.pid))
        self.addCleanup(lambda: sentinel.wait(timeout=5))
        self.addCleanup(lambda: sentinel.poll() is None and sentinel.kill())
        harness = subprocess.Popen(
            [sys.executable, "-u", str(self.root / "harness.py")],
            cwd=self.root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        self.owned.append(native.psutil.Process(harness.pid))
        self.addCleanup(lambda: harness.communicate(timeout=5))
        self.addCleanup(lambda: harness.poll() is None and harness.kill())
        channels = []
        worker_rows = []
        for _ in range(2):
            channel, _ = self.listener.accept()
            self.addCleanup(channel.close)
            channel.settimeout(15)
            data = bytearray()
            while not data.endswith(b"\n"):
                data.extend(channel.recv(1))
            row = json.loads(data)
            worker_rows.append(row)
            self.owned.append(native.psutil.Process(row["pid"]))
            channels.append(channel)
        ready = self.until(lambda: self.receipt(self.root / "ready.json"), "CLI never reached the worker barrier")
        controller = native.psutil.Process(ready["pid"])
        self.owned.append(controller)
        nested = [self.receipt(row["receipt"]) for row in ready["records"]]
        for row in nested:
            self.owned.append(native.psutil.Process(row["keeper"]["pid"]))
        (call,) = [
            self.receipt(path)
            for path in (self.root / "cli-calls").glob("*.json")
            if self.receipt(path).get("kind") == "cli_call"
        ]
        outer = self.until(
            lambda: (
                (
                    value
                    if all(any(p["pid"] == row["keeper"]["pid"] for p in value.get("processes", [])) for row in nested)
                    else None
                )
                if (value := self.receipt(call["receipt"]))
                else None
            ),
            "CLI keeper never retained its nested keepers",
        )
        self.owned.append(native.psutil.Process(outer["keeper"]["pid"]))
        if kill_harness:
            harness.kill()
        else:
            controller.kill()
        for channel, row in zip(channels, nested, strict=True):
            self.assertEqual(b"T", channel.recv(1), "worker could not enter graceful cleanup")
            self.assertTrue(native.live_processes([row["keeper"]]), "CLI keeper killed the nested keeper mid-cleanup")
            channel.sendall(b"X")
        for row in worker_rows:
            result = self.until(
                lambda: self.receipt(Path(row["directory"]) / "result.json"), "worker never retained its pause"
            )
            self.assertEqual("PAUSED_INTERRUPTED", result["status"])
        for row in ready["records"]:
            final = self.until(
                lambda: value if (value := self.receipt(row["receipt"])).get("phase") == "stopped" else None,
                "nested keeper never published verified cleanup",
            )
            self.assertEqual(("owner_lost", None), (final["cause"], final["cleanup_error"]))
        final = self.until(
            lambda: value if (value := self.receipt(call["receipt"])).get("phase") == "stopped" else None,
            "CLI keeper never published verified cleanup",
        )
        self.assertIsNone(final["cleanup_error"], final)
        self.until(lambda: not native.live_processes([final["keeper"], *final["processes"]]), "owned actor survived")
        self.assertIsNone(sentinel.poll(), "cleanup signalled an unrelated sentinel")
        self.assertFalse((self.root / "result.json").exists(), "harness manufactured a delivery verdict")
        admission = self.receipt(self.root / "cli-calls" / (call["nonce"] + ".json"))
        if kill_harness:
            self.assertEqual("running", admission["phase"], "a dead harness invented a CLI return")
            self.assertNotIn("exit", admission)
        else:
            harness.wait(timeout=5)
            admission = self.receipt(self.root / "cli-calls" / (call["nonce"] + ".json"))
            self.assertEqual(("interrupted", -signal.SIGKILL), (admission["phase"], admission["exit"]))
        self.assertEqual("unknown", admission["usage_status"])

    def test_harness_sigkill_lets_nested_worker_keepers_finish(self):
        self.run_loss(kill_harness=True)

    def test_cli_sigkill_lets_nested_worker_keepers_finish(self):
        self.run_loss(kill_harness=False)


class KeeperSettlementClockTests(unittest.TestCase):
    def test_owner_loss_cannot_hide_behind_provider_exit_with_an_unscheduled_watcher(self):
        import threading

        for owner_state in ("live", "missing", "reused", "zombie"):
            with self.subTest(owner_state=owner_state), tempfile.TemporaryDirectory() as temporary:
                owner = {"pid": 901, "birth_identity": 1.0, "state": "running"}
                provider = {"pid": 902, "birth_identity": 2.0, "state": "running"}
                guardian = {"pid": os.getpid(), "birth_identity": 3.0, "state": "running"}
                current = {guardian["pid"]: guardian, provider["pid"]: provider}
                if owner_state != "missing":
                    current[owner["pid"]] = {
                        **owner,
                        "birth_identity": 4.0 if owner_state == "reused" else owner["birth_identity"],
                        "state": "Z" if owner_state == "zombie" else owner["state"],
                    }
                signals, timers = [], []

                class Tree:
                    def __init__(self, *args, **kwargs):
                        self.known = {}

                    def inventory(self):
                        return [provider]

                    def admitted_sessions(self):
                        return []

                    def sample(self, **kwargs):
                        current.pop(provider["pid"], None)
                        return []

                    def signal(self, rows, sig):
                        signals.append((rows, sig))

                    def stop(self, observer):
                        pass

                class DeferredWatcher:
                    def __init__(self, *, target, daemon):
                        self.target = target

                    def start(self):
                        pass  # The watcher cannot run before the provider is found gone.

                    def join(self, timeout):
                        self.target()  # Publication has set done; closes its owned wake FD.

                class Timer:
                    def __init__(self, interval, callback, args):
                        timers.append(interval)

                    def start(self):
                        pass

                    def cancel(self):
                        pass

                receipt = Path(temporary) / "supervision.json"
                control_r, control_w = os.pipe()
                ack_r, ack_w = os.pipe()
                packet = {
                    "metadata": {
                        "schema": 1,
                        "nonce": "e" * 32,
                        "owner": owner,
                        "provider": provider,
                        "keeper": guardian,
                        "receipt": str(receipt),
                    },
                    "deadline": None,
                    "timing": elapsed_timing.admission(time.time(), time.monotonic(), None),
                }
                os.write(control_w, (json.dumps(packet) + "\n").encode())
                try:
                    with (
                        patch.object(keeper, "KeeperTree", Tree),
                        patch.object(keeper.processes, "process_table", side_effect=lambda _pids: dict(current)),
                        patch.object(
                            keeper,
                            "threading",
                            SimpleNamespace(
                                Event=threading.Event, Lock=threading.Lock, Thread=DeferredWatcher, Timer=Timer
                            ),
                        ),
                    ):
                        self.assertEqual(0, keeper.main(control_r, ack_w))
                    result = json.loads(receipt.read_text())
                    self.assertEqual("provider_stopped" if owner_state == "live" else "owner_lost", result["cause"])
                    self.assertEqual("stopped", result["phase"])
                    self.assertIsNone(result["cleanup_error"])
                    self.assertEqual(0 if owner_state == "live" else 1, len(signals))
                    self.assertEqual(0 if owner_state == "live" else 1, len(timers))
                finally:
                    for fd in (control_r, control_w, ack_r, ack_w):
                        with contextlib.suppress(OSError):
                            os.close(fd)

    def test_a_dead_root_does_not_shorten_its_nested_cleanup_window(self):
        clock = [0.0]
        alive = [True]
        admission = [True]
        forced = []
        timers = []
        provider = {"pid": 901, "birth_identity": 1.0}
        guardian = {"pid": os.getpid(), "birth_identity": 2.0}
        nested = {"pid": 902, "birth_identity": 3.0}
        sentinel = {"pid": 999, "birth_identity": 4.0}

        def monotonic():
            clock[0] += 1
            return clock[0]

        def live(rows):
            if rows == [guardian]:
                return [guardian]  # The enclosing harness remains alive after its CLI root exits.
            if admission[0]:
                admission[0] = False
                return [provider]
            return [row for row in rows if row == nested and alive[0]]

        class Tree:
            def __init__(self, *args, **kwargs):
                self.known = {}

            def admitted_sessions(self):
                return []

            def inventory(self):
                return [provider, nested]

            def sample(self, **kwargs):
                if clock[0] >= 14:
                    alive[0] = False
                return [nested] if alive[0] else []

            def signal(self, rows, sig):
                # No simulated force signal may reach the nested keeper or sentinel.
                forced.extend(row for row in rows if row in (nested, sentinel))

            def stop(self, observer):
                if alive[0]:
                    forced.append(nested)

        class Timer:
            def __init__(self, interval, callback, args):
                timers.append(interval)

            def start(self):
                pass

            def cancel(self):
                pass

        with tempfile.TemporaryDirectory(prefix="ac454-clock-") as temporary:
            path = Path(temporary) / "supervision.json"
            control_r, control_w = os.pipe()
            ack_r, ack_w = os.pipe()
            external_r, external_w = os.pipe()
            packet = {
                "metadata": {
                    "schema": 1,
                    "nonce": "a" * 32,
                    "owner": guardian,
                    "keeper": guardian,
                    "provider": provider,
                    "receipt": str(path),
                },
                "deadline": None,
                "timing": {
                    "wall_origin_seconds": 1000.0,
                    "monotonic_origin_seconds": 0.0,
                    "admitted_stage_limit_seconds": None,
                },
                "cleanup_policy": "interrupt_root",
                "kill_after_seconds": harness_processes.CLI_KILL_AFTER_SECONDS,
            }
            os.write(control_w, (json.dumps(packet) + "\n").encode())
            # Keep the harness alive: the root's death alone must not freeze a
            # nested lifeline keeper before its simulated 14 s pause is retained.
            try:
                with (
                    patch.object(keeper, "KeeperTree", Tree),
                    patch.object(keeper, "time", SimpleNamespace(monotonic=monotonic, time=lambda: 1000.0 + clock[0])),
                    patch.object(keeper.threading, "Timer", Timer),
                    patch.object(keeper.processes, "process_table", return_value={os.getpid(): guardian}),
                    patch.object(keeper.processes, "live_processes", side_effect=live),
                ):
                    result = keeper.main(control_r, ack_w, external_r)
                self.assertEqual(0, result)
                self.assertEqual([], forced)
                self.assertFalse(alive[0])
                final = json.loads(path.read_text())
                self.assertEqual(
                    ("stopped", "provider_stopped", None), (final["phase"], final["cause"], final["cleanup_error"])
                )
                self.assertEqual([], timers, "root-death settlement does not invent a new owner-loss timer")
            finally:
                for fd in (control_w, ack_r, external_w):
                    os.close(fd)
