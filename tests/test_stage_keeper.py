"""The per-attempt stage keeper (#454), exercised with real processes.

Waits are bounded and causal: providers announce themselves on a pipe and block on
another until the test lets them go. A process that is not this test's child (the
keeper, or a provider whose controller was killed) is reparented and may linger as a
zombie on hosts whose init reaps late; a zombie counts as gone.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import psutil

import autocode_process as processes
import autocode_stage_keeper as stage_keeper

TOOLS = Path(stage_keeper.__file__).resolve().parent

# argv: INFO_FD HOLD_FD MODE. Announces itself on INFO_FD, then blocks on HOLD_FD.
PROVIDER = r'''
import json, os, signal, subprocess, sys
info, hold, mode = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
row = {"pid": os.getpid(), "ppid": os.getppid(), "sid": os.getsid(0), "pgid": os.getpgid(0)}
if mode == "ignore-term":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
if mode == "group-child":
    helper = subprocess.Popen([sys.executable, "-c", "import os, sys; os.read(int(sys.argv[1]), 1)", str(hold)],
                              pass_fds=(hold,))
    row["helper"] = helper.pid
if mode == "daemon":
    # Double fork into another session: only the controller's receipt still links it.
    ready_r, ready_w = os.pipe()
    if os.fork() == 0:
        os.setsid()
        if os.fork() == 0:
            os.write(ready_w, str(os.getpid()).encode())
            os.read(hold, 1)
            os._exit(0)
        os._exit(0)
    os.close(ready_w)
    os.wait()
    row["daemon"] = int(os.read(ready_r, 32))
os.write(info, (json.dumps(row) + "\n").encode())
os.close(info)
os.read(hold, 1)
if mode == "self-term":
    os.kill(os.getpid(), signal.SIGTERM)
sys.exit(int(mode) if mode.isdigit() else 0)
'''

# A controller in its own process: launches a held provider behind a keeper, prints the
# record, then blocks until killed.
CONTROLLER = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
import autocode_stage_keeper as stage_keeper
base, run_dir, provider, info, hold = sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]), int(sys.argv[6])
record = {}
keeper = stage_keeper.Lifeline(base, run_dir, record)
child = keeper.launch([sys.executable, "-c", provider, str(info), str(hold), "hold"],
                      pass_fds=(info, hold), start_new_session=True)
print(json.dumps(record), flush=True)
sys.stdin.read()
'''


def gone(pid, birth_identity=None):
    try:
        process = psutil.Process(pid)
        if birth_identity is not None and processes.process_table({pid}).get(pid, {}).get("birth_identity") != birth_identity:
            return True
        return process.status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


class StageKeeperTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="stage-keeper-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        self.started = []  # psutil handles for everything a test starts, cleaned up by identity
        self.addCleanup(self.stop_started)
        self.attempts = 0

    def stop_started(self):
        for process in reversed(self.started):
            try:
                if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                    process.kill()
            except psutil.NoSuchProcess:
                pass
        psutil.wait_procs([p for p in self.started if p.is_running()], timeout=3)

    def own(self, pid):
        try:
            self.started.append(psutil.Process(pid))
        except psutil.NoSuchProcess:
            pass

    def await_condition(self, predicate, message, timeout=15):
        deadline = time.monotonic() + timeout
        pause = threading.Event()
        while time.monotonic() < deadline:
            if predicate():
                return
            pause.wait(.02)
        self.fail("timed out awaiting " + message)

    def lifeline(self):
        self.attempts += 1
        record = {}
        keeper = stage_keeper.Lifeline(self.run_dir / "iterations/001" / f"builder-{self.attempts:02d}", self.run_dir, record)
        self.addCleanup(keeper.close)
        return keeper, record

    def held(self, mode="0", **options):
        """Launch PROVIDER behind a keeper; return (keeper, record, child, announcement, hold_w)."""
        keeper, record = self.lifeline()
        info_r, info_w = os.pipe()
        hold_r, hold_w = os.pipe()
        self.addCleanup(close_all, info_r, hold_w)
        try:
            child = keeper.launch([sys.executable, "-c", PROVIDER, str(info_w), str(hold_r), mode],
                                  pass_fds=(info_w, hold_r), start_new_session=True, **options)
        finally:
            os.close(info_w)
            os.close(hold_r)
        self.own(child.pid)
        self.addCleanup(reap, child)
        announcement = json.loads(read_line(info_r))
        for key in ("helper", "daemon"):
            if key in announcement:
                self.own(announcement[key])
        return keeper, record, child, announcement, hold_w

    def keeper_pid(self, record):
        return record["supervision"]["keeper"]["pid"]

    def report(self, record):
        path = Path(record["supervision"]["report"])
        self.await_condition(path.is_file, "the supervision report")
        return json.loads(path.read_text())

    def test_exec_preserves_pid_exit_status_and_session(self):
        keeper, record, child, seen, hold = self.held("7")
        self.assertEqual(child.pid, record["pid"])
        self.assertEqual({"pid": child.pid, "ppid": os.getpid(), "sid": child.pid, "pgid": child.pid}, seen)
        keeper_row = record["supervision"]["keeper"]
        guard = psutil.Process(keeper_row["pid"])
        self.assertEqual(keeper_row["birth_identity"], processes.process_table({guard.pid})[guard.pid]["birth_identity"])
        self.assertNotIn(guard.pid, [p.pid for p in psutil.Process(os.getpid()).children(recursive=True)])
        self.assertEqual([], psutil.Process(child.pid).children(recursive=True), "the keeper is not the provider's child")
        self.assertNotIn(os.getpgid(guard.pid), (child.pid, os.getpgid(0)))
        os.write(hold, b"x")
        self.assertEqual(7, keeper.supervise(child.wait, timeout=15))
        self.await_condition(lambda: gone(guard.pid), "the released keeper to exit")
        self.assertFalse(Path(record["supervision"]["report"]).exists())

    def test_exec_preserves_environment_and_default_sigpipe(self):
        env_program = shutil.which("env")
        environment = {"PATH": os.environ["PATH"], "STAGE_KEEPER_MARKER": "kept as given"}  # C locale: no LANG/LC_*
        with (self.root / "direct.txt").open("w") as out:
            subprocess.run([env_program], env=environment, stdout=out, check=True)
        keeper, record = self.lifeline()
        with (self.root / "kept.txt").open("w") as out:
            child = keeper.launch([env_program], env=environment, stdout=out, start_new_session=True)
        self.own(child.pid)
        self.assertEqual(0, keeper.supervise(child.wait, timeout=15))
        self.assertEqual(sorted((self.root / "direct.txt").read_text().splitlines()),
                         sorted((self.root / "kept.txt").read_text().splitlines()))
        keeper, record = self.lifeline()
        child = keeper.launch(["/bin/sh", "-c", "kill -PIPE $$; exit 3"], start_new_session=True)
        self.own(child.pid)
        self.assertEqual(-signal.SIGPIPE, keeper.supervise(child.wait, timeout=15),
                         "SIGPIPE must reach the provider with its default action, as Popen leaves it")

    def test_signal_exit_is_preserved(self):
        keeper, record, child, _, hold = self.held("self-term")
        os.write(hold, b"x")
        self.assertEqual(-signal.SIGTERM, keeper.supervise(child.wait, timeout=15))

    def test_release_leaves_provider_untouched(self):
        keeper, record, child, _, hold = self.held("0")
        guard = self.keeper_pid(record)
        keeper.release()
        self.await_condition(lambda: gone(guard), "the released keeper to exit")
        self.assertIsNone(child.poll(), "a released keeper never touches the provider")
        os.write(hold, b"x")
        self.assertEqual(0, child.wait(timeout=15))
        self.assertFalse(Path(record["supervision"]["report"]).exists())

    def test_lifeline_eof_stops_provider_group_and_records_cause(self):
        keeper, record, child, seen, hold = self.held("group-child")
        keeper.close()  # no release: the controller gave up while still alive
        self.assertEqual(-signal.SIGTERM, child.wait(timeout=15))
        self.await_condition(lambda: gone(seen["helper"]), "the provider's group member to stop")
        report = self.report(record)
        self.assertEqual("lifeline_closed_by_live_owner", report["cause"], report)
        self.assertTrue(report["owner_alive_after_stop"])
        self.assertEqual("stopped", report["outcome"])
        self.assertEqual(child.pid, report["target"]["pid"])
        self.assertEqual(sorted((child.pid, seen["helper"])), report["live_at_detection"])
        terms = [row["pids"] for row in report["signals"] if row["signal"] == "SIGTERM"]
        self.assertEqual([sorted((child.pid, seen["helper"]))], terms)

    def test_controller_sigkill_stops_provider_and_reports_supervisor_lost(self):
        info_r, info_w = os.pipe()
        hold_r, hold_w = os.pipe()
        self.addCleanup(close_all, info_r, hold_w)
        controller = subprocess.Popen(
            [sys.executable, "-c", CONTROLLER, str(TOOLS), str(self.run_dir / "iterations/001/builder-01"),
             str(self.run_dir), PROVIDER, str(info_w), str(hold_r)],
            pass_fds=(info_w, hold_r), stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        os.close(info_w)
        os.close(hold_r)
        self.own(controller.pid)
        self.addCleanup(reap, controller)
        provider = json.loads(read_line(info_r))
        self.own(provider["pid"])
        record = json.loads(controller.stdout.readline())
        target = processes.process_table({provider["pid"]})[provider["pid"]]["birth_identity"]
        controller.kill()
        controller.wait(timeout=15)
        self.await_condition(lambda: gone(provider["pid"], target), "the orphaned provider to stop")
        report = self.report(record)
        self.assertEqual("supervisor_lost", report["cause"], report)
        self.assertEqual(controller.pid, report["owner"]["pid"])
        self.assertEqual("stopped", report["outcome"])

    def test_term_ignoring_provider_is_killed_after_grace(self):
        keeper, record, child, _, hold = self.held("ignore-term")
        keeper.close()
        self.assertEqual(-signal.SIGKILL, child.wait(timeout=15))
        report = self.report(record)
        self.assertEqual(["SIGSTOP", "SIGTERM", "SIGCONT", "SIGKILL"],
                         [row["signal"] for row in report["signals"]][:4], report)
        self.assertEqual("stopped", report["outcome"])

    def test_receipt_descendant_is_stopped_but_mismatched_identity_is_not(self):
        keeper, record, child, seen, hold = self.held("daemon")
        daemon = seen["daemon"]
        self.assertNotEqual(child.pid, psutil.Process(daemon).ppid(), "only the receipt links the daemon")
        unrelated = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"],
                                     stdin=subprocess.PIPE, start_new_session=True)
        self.own(unrelated.pid)
        self.addCleanup(reap, unrelated)
        table = processes.process_table({child.pid, daemon, unrelated.pid})
        rows = {pid: processes.identity(row) for pid, row in table.items()}
        reused = {**rows[unrelated.pid], "birth_identity": rows[unrelated.pid]["birth_identity"] - 1}
        (self.run_dir / "active-processes.json").write_text(json.dumps(
            {"run_dir": str(self.run_dir), "pid": child.pid, "processes": [rows[child.pid], rows[daemon], reused]}))
        keeper.close()
        self.assertEqual(-signal.SIGTERM, child.wait(timeout=15))
        self.await_condition(lambda: gone(daemon), "the receipt's descendant to stop")
        report = self.report(record)
        self.assertEqual("stopped", report["outcome"], report)
        self.assertIn(daemon, report["live_at_detection"])
        self.assertNotIn(unrelated.pid, [pid for row in report["signals"] for pid in row["pids"]])
        self.assertIsNone(unrelated.poll(), "a pid whose birth identity differs is never signalled")

    def test_receipt_for_another_provider_is_ignored(self):
        keeper, record, child, _, hold = self.held("0")
        unrelated = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"],
                                     stdin=subprocess.PIPE, start_new_session=True)
        self.own(unrelated.pid)
        self.addCleanup(reap, unrelated)
        row = processes.identity(processes.process_table({unrelated.pid})[unrelated.pid])
        (self.run_dir / "active-processes.json").write_text(json.dumps(
            {"run_dir": str(self.run_dir), "pid": unrelated.pid, "processes": [row]}))
        keeper.close()
        self.assertEqual(-signal.SIGTERM, child.wait(timeout=15))
        self.report(record)
        self.assertIsNone(unrelated.poll(), "a receipt for a different provider pid establishes no ownership")

    def test_two_attempts_are_independent(self):
        first, first_record, first_child, _, first_hold = self.held("0")
        second, second_record, second_child, _, second_hold = self.held("0")
        self.assertNotEqual(self.keeper_pid(first_record), self.keeper_pid(second_record))
        first.close()
        self.assertEqual(-signal.SIGTERM, first_child.wait(timeout=15))
        self.assertEqual("stopped", self.report(first_record)["outcome"])
        self.assertIsNone(second_child.poll(), "closing one attempt's lifeline leaves the other provider alone")
        os.write(second_hold, b"x")
        self.assertEqual(0, second.supervise(second_child.wait, timeout=15))
        self.await_condition(lambda: gone(self.keeper_pid(second_record)), "the second keeper to exit")
        self.assertFalse(Path(second_record["supervision"]["report"]).exists())

    def test_arm_failure_fails_closed_before_exec(self):
        marker = self.root / "provider-ran"
        keeper, record = self.lifeline()
        before = {p.pid for p in psutil.Process(os.getpid()).children()}
        with patch.object(stage_keeper, "_import_paths", return_value=[]), \
                self.assertRaisesRegex(stage_keeper.SupervisionError, "did not arm"):
            keeper.launch([sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"], start_new_session=True)
        self.assertFalse(marker.exists(), "the provider must never start without an armed keeper")
        self.assertEqual({}, record)
        self.assertEqual(before, {p.pid for p in psutil.Process(os.getpid()).children()}, "the launcher was reaped")
        self.assertEqual([], list(self.run_dir.rglob("*.supervision-spec.json")))

    def test_missing_program_raises_like_popen(self):
        keeper, record = self.lifeline()
        with self.assertRaises(FileNotFoundError):
            keeper.launch([str(self.root / "missing-provider")], start_new_session=True)
        self.assertEqual({}, record)

    def test_replaced_popen_receives_the_provider_command_unsupervised(self):
        launched = []

        class Scripted:
            pid = 4242

            def __init__(self, command, **options):
                launched.append((command, options))

        keeper, record = self.lifeline()
        with patch.object(subprocess, "Popen", Scripted):
            child = keeper.launch(["codex", "exec"], cwd=str(self.root))
        self.assertIsInstance(child, Scripted)
        self.assertEqual([(["codex", "exec"], {"cwd": str(self.root)})], launched)
        self.assertEqual({"pid": 4242}, record)


def read_line(fd, timeout=30):
    data = b""
    deadline = time.monotonic() + timeout
    while not data.endswith(b"\n"):
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
            raise TimeoutError("provider did not announce itself")
        chunk = os.read(fd, 4096)
        if not chunk:
            raise EOFError("provider exited before announcing itself")
        data += chunk
    return data


def close_all(*descriptors):
    for fd in descriptors:
        os.close(fd)


def reap(child):
    if child.poll() is None:
        child.kill()
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    for stream in (child.stdin, child.stdout, child.stderr):
        if stream:
            stream.close()


if __name__ == "__main__":
    unittest.main()
