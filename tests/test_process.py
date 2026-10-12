"""Real POSIX process-tree regressions; no inference or network access."""

import os
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import autocode_process as processes
from autocode_activity import ActivityMonitor


def cleanup_fixture_worker(worker):
    """A provider's correctly reaped child can disappear during fixture cleanup."""
    try:
        if worker.is_running() and worker.status() != processes.psutil.STATUS_ZOMBIE:
            worker.kill()
        worker.wait(timeout=3)
    except processes.psutil.NoSuchProcess:
        pass


class ProcessTests(unittest.TestCase):
    def test_fixture_cleanup_handles_reaping_without_hiding_live_workers_or_denial(self):
        live = MagicMock()
        live.is_running.return_value = True
        live.status.return_value = processes.psutil.STATUS_RUNNING
        cleanup_fixture_worker(live)
        live.kill.assert_called_once_with()
        live.wait.assert_called_once_with(timeout=3)
        for operation in ("status", "kill", "wait"):
            with self.subTest(disappears_during=operation):
                worker = MagicMock()
                worker.is_running.return_value = True
                worker.status.return_value = processes.psutil.STATUS_RUNNING
                getattr(worker, operation).side_effect = processes.psutil.NoSuchProcess(123)
                cleanup_fixture_worker(worker)
        denied = MagicMock()
        denied.is_running.return_value = True
        denied.status.side_effect = processes.psutil.AccessDenied(123)
        with self.assertRaises(processes.psutil.AccessDenied):
            cleanup_fixture_worker(denied)

    def test_process_id_enumeration_retries_transient_macos_sysctl_denial(self):
        with patch.object(processes.psutil, "pids", side_effect=[PermissionError("sysctl table refresh"), [101]]):
            self.assertEqual([101], processes.process_ids())

    def test_stage_preflight_inspects_controller_not_unrelated_processes(self):
        import autocode as runner

        class Prepared(RuntimeError):
            pass

        pid = os.getpid()
        controller = MagicMock()
        controller.create_time.return_value = 1790019574.123
        controller._ident = (pid, 1790019574.123)
        controller.ppid.return_value = os.getppid()
        controller.status.return_value = "running"
        controller._proc.name.return_value = "fixture-controller"

        def inspect(selected):
            self.assertEqual(pid, selected, "Preflight must not inspect unrelated processes")
            return controller

        with (
            patch.object(processes.psutil, "pids", return_value=[pid, *range(pid + 1, pid + 4097)]) as pids,
            patch.object(processes.psutil, "Process", side_effect=inspect) as metadata,
            patch.object(processes.os, "getpgid", return_value=pid),
            patch.object(runner.artifacts, "reserve", side_effect=Prepared("preflight passed")),
        ):
            with self.assertRaisesRegex(Prepared, "preflight passed"):
                runner.run_role(
                    role="requirements",
                    prompt="Fixture",
                    sandbox="read-only",
                    workspace=Path("."),
                    run_dir=Path("."),
                    schema=Path("unused.json"),
                    model=None,
                    state={
                        "iteration": 1,
                        "next_stage": "recognize_workflow",
                        "settings": {"roles": {"requirements": {}}},
                    },
                    allow_write=False,
                    dry_run=False,
                )
        pids.assert_called_once_with()
        metadata.assert_called_once_with(pid)

    def test_preflight_enumeration_failure_stops_before_metadata_inspection(self):
        with (
            patch.object(processes.psutil, "pids", side_effect=PermissionError("enumeration denied")) as pids,
            patch.object(processes.time, "sleep"),
            patch.object(processes.psutil, "Process") as metadata,
        ):
            with self.assertRaisesRegex(processes.ProcessError, "Cannot enumerate"):
                processes.preflight()
        self.assertEqual(3, pids.call_count)
        metadata.assert_not_called()

    def test_preflight_requires_accessible_controller_identity(self):
        pid = os.getpid()
        for error, message in (
            (processes.psutil.AccessDenied(pid), "access denied"),
            (SystemError("identity unavailable"), "SystemError"),
            (processes.psutil.NoSuchProcess(pid), "controller"),
        ):
            with (
                self.subTest(error=type(error).__name__),
                patch.object(processes.psutil, "pids", return_value=[pid]) as pids,
                patch.object(processes.psutil, "Process", side_effect=error) as metadata,
            ):
                with self.assertRaisesRegex(processes.ProcessError, message):
                    processes.preflight()
                pids.assert_called_once_with()
                metadata.assert_called_once_with(pid)

    def test_native_process_table_uses_birth_identity_without_shell_commands(self):
        process = MagicMock()
        process.create_time.return_value = 1790019574.123
        process._ident = (101, 1790019574.123)
        process.ppid.return_value = 90
        process.status.return_value = "sleeping"
        process._proc.name.return_value = "mcp-server-darw"
        with (
            patch.object(processes.psutil, "Process", return_value=process),
            patch.object(processes.os, "getpgid", return_value=101),
            patch.object(processes.psutil, "pids", return_value=[101]),
        ):
            row = processes.process_table()[101]
        self.assertEqual("mcp-server-darw", row["executable"])
        process.name.assert_not_called()
        process.cmdline.assert_not_called()
        self.assertTrue(ActivityMonitor._mcp_helper(row))
        self.assertEqual(1790019574.123, processes.identity(row)["birth_time"])
        self.assertTrue(processes.matches(processes.identity(row), row))
        self.assertEqual(1790019574.123, processes.identity(row)["birth_identity"])
        self.assertFalse(processes.matches({**processes.identity(row), "birth_identity": 0}, row))
        legacy = {key: value for key, value in processes.identity(row).items() if key != "birth_identity"}
        self.assertTrue(processes.matches(legacy, row))
        self.assertFalse(processes.matches({**legacy, "birth_time": 0}, row))
        legacy.pop("birth_time")
        self.assertTrue(processes.matches(legacy, row))

    def test_stable_identity_ignores_display_time_but_never_falls_back_on_mismatch(self):
        row = {
            "pid": 101,
            "started": "local display",
            "group": 101,
            "birth_time": 1790019574.123,
            "birth_identity": 1790019574.123,
        }
        saved = processes.identity(row)
        shifted = {**row, "started": "different timezone", "birth_time": row["birth_time"] + 2}
        self.assertTrue(processes.matches(saved, shifted))
        for changed in ({"pid": 102}, {"birth_identity": row["birth_identity"] + 0.001}, {"birth_identity": None}):
            with self.subTest(changed=changed):
                self.assertFalse(processes.matches(saved, {**row, **changed}))
        legacy = {key: value for key, value in saved.items() if key != "birth_identity"}
        self.assertFalse(processes.matches(saved, legacy))
        self.assertFalse(processes.matches(legacy, shifted))
        self.assertFalse(processes.matches({**saved, "birth_identity": None}, {**row, "birth_identity": None}))

    def test_linux_identity_is_the_start_time_since_boot_and_a_clock_step_does_not_move_it(self):
        # #1117: psutil's create_time() adds a boot_time() re-read from the wall clock, so a clock step
        # moved the identity of every live process and each looked dead.
        process = MagicMock()
        process._ident = (101, 123.456)
        process.create_time.return_value = 1790019574.123
        with patch.object(processes.psutil, "OSX", False), patch.object(processes.psutil, "LINUX", True):
            before = processes._birth_identity(process)
            process.create_time.return_value += 1.5  # the wall clock was stepped
            after = processes._birth_identity(process)
        self.assertEqual((123.456, 123.456), (before, after))
        row = {"pid": 101, "birth_identity": before, "birth_time": 1790019574.123}
        self.assertTrue(processes.matches(processes.identity(row), {**row, "birth_time": row["birth_time"] + 1.5}))
        self.assertFalse(processes.matches(row, {**row, "birth_identity": 124.0}))

    def test_other_platforms_keep_the_epoch_create_time(self):
        process = MagicMock()
        process._ident = (101, 123.456)
        process.create_time.return_value = 1790019574.123
        with patch.object(processes.psutil, "OSX", False), patch.object(processes.psutil, "LINUX", False):
            self.assertEqual(1790019574.123, processes._birth_identity(process))

    @unittest.skipUnless(processes.psutil.LINUX, "the clock-step identity is Linux's: psutil adds boot_time() there")
    def test_a_live_process_still_matches_its_receipt_after_the_wall_clock_is_stepped(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        saved = processes.identity(processes.process_table({child.pid})[child.pid])
        from psutil import _pslinux

        real = _pslinux.boot_time
        with (
            patch.object(_pslinux, "boot_time", lambda: real() + 3),
            patch.object(processes.psutil, "boot_time", lambda: real() + 3),
        ):
            moved = processes.process_table({child.pid})[child.pid]
            self.assertNotEqual(saved["birth_time"], moved["birth_time"], "the display time did move")
            self.assertTrue(processes.matches(saved, moved))
            self.assertEqual([child.pid], [row["pid"] for row in processes.live_processes([saved])])

    def test_a_linux_receipt_from_before_the_fix_survives_a_small_clock_step(self):
        # Saved as an epoch start time; the live identity is now the start time since boot.
        saved = {"pid": 101, "birth_identity": 1790019574.123, "birth_time": 1790019574.123}
        live = {"pid": 101, "birth_identity": 123.456, "birth_time": 1790019574.123}
        self.assertTrue(processes.matches(saved, live))
        self.assertTrue(processes.matches(saved, {**live, "birth_time": live["birth_time"] + 2}))
        self.assertTrue(processes.matches(saved, {**live, "birth_time": live["birth_time"] - 4.9}))
        self.assertFalse(processes.matches(saved, {**live, "birth_time": live["birth_time"] + 60}), "another process")
        self.assertFalse(processes.matches(saved, {**live, "pid": 102}))
        self.assertFalse(processes.matches({**saved, "birth_identity": None}, live))
        self.assertTrue(processes.matches({**saved, "birth_identity": 123.456}, live), "a new receipt is exact")
        self.assertFalse(processes.matches({**saved, "birth_identity": 123.457}, live))

    def test_signal_rechecks_stable_identity_before_killing(self):
        pid = 2**22 - 2
        row = {"pid": pid, "started": "same display", "birth_time": 1790019574.123, "birth_identity": 1790019574.123}
        tree = processes.ProcessTree(pid, lambda _rows: None)
        with (
            patch.object(
                processes, "process_table", return_value={pid: {**row, "birth_identity": row["birth_identity"] + 0.001}}
            ),
            patch.object(processes.os, "kill") as kill,
        ):
            tree.signal([row], signal.SIGTERM)
            kill.assert_not_called()
        with (
            patch.object(
                processes,
                "process_table",
                return_value={pid: {**row, "started": "changed display", "birth_time": row["birth_time"] + 2}},
            ),
            patch.object(processes.os, "kill") as kill,
        ):
            tree.signal([row], signal.SIGTERM)
            kill.assert_called_once_with(pid, signal.SIGTERM)

    def test_owned_access_denial_fails_closed_and_a_vanished_pid_is_safe(self):
        with patch.object(processes.psutil, "Process", side_effect=processes.psutil.AccessDenied(101)):
            with self.assertRaisesRegex(processes.ProcessError, "101.*access denied"):
                processes.process_table({101})
        with patch.object(processes.psutil, "Process", side_effect=processes.psutil.NoSuchProcess(101)):
            self.assertEqual({}, processes.process_table({101}))

    def test_a_pid_whose_proc_entry_vanishes_mid_read_is_gone_not_fatal(self):
        # A live program run's child was declared FAILED: "Cannot inspect process 30956: FileNotFoundError".
        vanished = FileNotFoundError(2, "No such file or directory", "/proc/101/stat")
        with patch.object(processes.psutil, "Process", side_effect=vanished):
            self.assertEqual({}, processes.process_table({101}))
            with patch.object(processes, "process_ids", return_value={101}):
                self.assertEqual({}, processes.process_table())
        with patch.object(processes.psutil, "Process", side_effect=OSError(5, "Input/output error")):
            with self.assertRaisesRegex(processes.ProcessError, "Cannot inspect process 101: OSError"):
                processes.process_table({101})

    def test_descendant_discovery_denial_fails_closed_without_another_scan(self):
        row = {"pid": 101, "parent": 90, "group": 101, "birth_identity": 123, "state": "running"}
        parent = MagicMock(pid=101, _ident=(101, 123))
        parent.create_time.return_value = 123
        for error in (PermissionError("discovery denied"), processes.psutil.AccessDenied(101)):
            checkpoint = MagicMock()
            tree = processes.ProcessTree(101, checkpoint)
            with (
                self.subTest(error=type(error).__name__),
                patch.object(processes, "process_table", return_value={101: row}) as table,
                patch.object(processes.psutil, "Process", return_value=parent),
                patch.object(processes.process_children, "descendants", side_effect=error) as descendants,
                patch.object(processes, "process_ids") as scan,
            ):
                with self.assertRaisesRegex(
                    processes.ProcessError, "Cannot inspect descendants of owned process 101"
                ) as raised:
                    tree.sample()
                self.assertIs(error, raised.exception.__cause__)
                descendants.assert_called_once_with(parent)
                table.assert_called_once_with({101})
                scan.assert_not_called()
                checkpoint.assert_not_called()
                self.assertEqual({101: processes.identity(row)}, tree.known)

    def test_optional_native_name_failure_retains_owned_identity(self):
        for error in (
            processes.psutil.AccessDenied(101),
            PermissionError("native name denied"),
            SystemError("proc_cmdline returned a result with an exception set"),
        ):
            with self.subTest(error=type(error).__name__):
                process = MagicMock()
                process.create_time.return_value = 1790019574.123
                process._ident = (101, 1790019574.123)
                process.ppid.return_value = 90
                process.status.return_value = "sleeping"
                process._proc.name.side_effect = error
                with (
                    patch.object(processes.psutil, "Process", return_value=process),
                    patch.object(processes.os, "getpgid", return_value=101),
                ):
                    row = processes.process_table({101})[101]
                self.assertIsNone(row["executable"])
                self.assertFalse(ActivityMonitor._mcp_helper(row))
                self.assertEqual([row], processes.live_processes([processes.identity(row)], {101: row}))
                process.name.assert_not_called()
                process.cmdline.assert_not_called()

    def test_native_identity_system_error_fails_closed(self):
        for field in ("create_time", "ppid", "status"):
            with self.subTest(field=field):
                process = MagicMock()
                getattr(process, field).side_effect = SystemError("native identity unavailable")
                with patch.object(processes.psutil, "Process", return_value=process):
                    with self.assertRaisesRegex(processes.ProcessError, "101.*SystemError.*blocked"):
                        processes.process_table({101})

    def test_provider_finishes_when_optional_native_name_is_unavailable(self):
        backend = type(processes.psutil.Process()._proc)
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(.2)"], start_new_session=True)
        owned = []
        try:
            with patch.object(backend, "name", side_effect=SystemError("native name unavailable")):
                code, expired = processes.wait_for_stage(child, 5, lambda rows: owned.extend(rows))
            self.assertEqual(0, code)
            self.assertFalse(expired)
            self.assertIn(child.pid, [row["pid"] for row in owned])
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)

    def test_fast_startup_exit_identity_is_recorded_before_observer_can_reap(self):
        # A concurrent observer's child.poll() can reap a fast-exiting provider
        # before ProcessTree.sample records its birth identity. That left
        # active_stage.processes empty and blocked bounded startup recovery.
        class QuietMonitor:
            idle_limit = 0
            tool_limit = 0

            def poll(self, processes=None, root_pid=None):
                return {
                    "idle_seconds": 0,
                    "tool_elapsed_seconds": None,
                    "idle_limit_seconds": 0,
                    "tool_limit_seconds": 0,
                }

        # Hold the fixture until supervision starts, so native inspection does
        # not race an unrelated early exit under host load.
        child = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdin.read(1); raise SystemExit(7)"],
            stdin=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        owned = []
        reaped_before_return = []

        def exit_after_capture(tree, _release):
            # Finish before the discovery worker samples, but only after the
            # actual root identity has been captured. Early observation must
            # never reap this still-blocked provider.
            self.assertIn(child.pid, tree.known)
            child.stdin.write("x")
            child.stdin.flush()
            reaped_before_return.append(child.wait(timeout=5))

        try:
            code, expired = processes.wait_for_stage(
                child,
                None,
                lambda rows: owned.__setitem__(slice(None), rows),
                activity=QuietMonitor(),
                on_root=exit_after_capture,
            )
            self.assertEqual(7, code)
            self.assertFalse(expired)
            self.assertEqual([7], reaped_before_return)
            self.assertIn(child.pid, [row["pid"] for row in owned])
            self.assertTrue(owned[0].get("birth_identity") is not None)
        finally:
            child.stdin.close()
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)

    def test_unavailable_root_identity_is_still_a_hold_not_an_invented_receipt(self):
        # A process that is already gone before supervision starts has no
        # recoverable birth identity. Recovery must keep refusing an empty
        # receipt rather than inventing one.
        checkpointed = []
        tree = processes.ProcessTree(2**22 - 3, lambda rows: checkpointed.append(list(rows)))
        with patch.object(processes, "process_table", return_value={}):
            self.assertIsNone(tree.capture_root())
        self.assertEqual({}, tree.known)
        self.assertEqual([], checkpointed)

    def test_capture_root_failure_fails_closed_and_does_not_leak_the_child(self):
        # capture_root() can fail before the observer starts; cleanup must not
        # join a never-started thread (which would
        # mask the ProcessError with RuntimeError and leak the provider child).
        class QuietMonitor:
            idle_limit = 0
            tool_limit = 0

            def poll(self, processes=None, root_pid=None):
                return {
                    "idle_seconds": 0,
                    "tool_elapsed_seconds": None,
                    "idle_limit_seconds": 0,
                    "tool_limit_seconds": 0,
                }

        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
        original_table = processes.process_table

        def denying_table(pids=None):
            if pids == {child.pid}:
                raise processes.ProcessError("fixture: root inspection denied")
            return original_table(pids)

        owned = []
        try:
            with patch.object(processes, "process_table", denying_table):
                with self.assertRaisesRegex(processes.ProcessError, "fixture: root inspection denied"):
                    processes.wait_for_stage(
                        child, None, lambda rows: owned.__setitem__(slice(None), rows), activity=QuietMonitor()
                    )
            self.assertIsNotNone(child.poll(), "A failed capture must still stop its child")
            self.assertEqual([], owned)
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)

    def wait_ready(self, root, child, *, ready="ready", go="go"):
        deadline = time.monotonic() + 15
        while not (root / ready).exists():
            if child.poll() is not None or time.monotonic() >= deadline:
                self.fail(f"Provider fixture failed to initialize: {ready}")
            time.sleep(0.01)
        if go:
            (root / go).touch()

    def activity_child(
        self,
        body,
        *,
        idle=0.45,
        tool=1.5,
        total=None,
        sample=None,
        require_worker=False,
        startup_grace=0,
        tool_ready=False,
    ):
        """Run a real event-writing worker without making cleanup speed an assertion."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            events = root / "events.jsonl"
            worker = root / "provider.py"
            worker.write_text(
                "import json,os,subprocess,sys,time\nfrom pathlib import Path\n"
                "def emit(row): print(json.dumps(row), flush=True)\n"
                "Path('ready').touch()\nwhile not Path('go').exists(): time.sleep(.01)\n" + body
            )
            snapshots = []
            owned = []

            def checkpoint_activity(value):
                snapshots.append(value.copy())
                if tool_ready and value.get("active_tool_count", 0):
                    (root / "tool-observed").touch()

            with events.open("w") as stream:
                child = subprocess.Popen([sys.executable, str(worker)], cwd=root, stdout=stream, start_new_session=True)
                try:
                    self.wait_ready(root, child)
                    if tool_ready:
                        self.wait_ready(root, child, ready="tool-started", go=None)
                    monitor = ActivityMonitor(events, idle_seconds=idle, tool_seconds=tool)
                    if sample is None:
                        code, expired = processes.wait_for_stage(
                            child,
                            total,
                            lambda rows: owned.__setitem__(slice(None), rows),
                            activity=monitor,
                            activity_checkpoint=checkpoint_activity,
                            startup_grace=startup_grace,
                        )
                    else:
                        with patch.object(processes.ProcessTree, "sample", sample(child)):
                            code, expired = processes.wait_for_stage(
                                child,
                                total,
                                lambda rows: owned.__setitem__(slice(None), rows),
                                activity=monitor,
                                activity_checkpoint=checkpoint_activity,
                                startup_grace=startup_grace,
                            )
                    self.assertEqual([], processes.live_processes(owned))
                    self.assertFalse((root / "late-write").exists())
                    if require_worker:
                        self.assertTrue((root / "worker.pid").is_file(), "The helper fixture must actually launch")
                    worker_pid = int((root / "worker.pid").read_text()) if (root / "worker.pid").exists() else None
                    if worker_pid is not None:
                        self.assertIn(worker_pid, [row["pid"] for row in owned])
                    return code, expired, snapshots, monitor.expired()
                finally:
                    if child.poll() is None:
                        child.kill()
                        child.wait()

    def test_meaningful_events_keep_a_long_provider_alive(self):
        body = """for index in range(9):
    emit({'type':'item.completed','item':{'id':str(index),'type':'command_execution','command':'true','exit_code':0}})
    time.sleep(.4)
"""
        code, expired, snapshots, _ = self.activity_child(body, idle=2, tool=5)
        self.assertEqual(0, code)
        self.assertFalse(expired)
        self.assertTrue(snapshots)

    def test_quiet_running_tool_has_its_own_deadline(self):
        body = """emit({'type':'item.started','item':{'id':'test','type':'command_execution','command':'quiet tests','status':'in_progress'}})
Path('tool-started').touch()
while not Path('tool-observed').exists(): time.sleep(.01)
time.sleep(.8)
emit({'type':'item.completed','item':{'id':'test','type':'command_execution','command':'quiet tests','exit_code':0}})
"""
        code, expired, snapshots, _ = self.activity_child(body, idle=2, tool=5, tool_ready=True)
        self.assertEqual(0, code)
        self.assertFalse(expired)
        self.assertTrue(any(row.get("active_tool_count", 0) for row in snapshots))

    def test_repeated_events_and_output_noise_do_not_keep_provider_alive(self):
        body = """while True:
    print('still here', flush=True)
    emit({'type':'item.completed','item':{'id':'one','type':'command_execution','command':'true','exit_code':0}})
    time.sleep(.06)
"""
        code, expired, _, reason = self.activity_child(body, idle=0.35)
        self.assertNotEqual(0, code)
        self.assertTrue(expired)
        self.assertEqual("idle", reason["kind"])

    def test_explicit_stage_cap_still_wins_over_continuing_activity(self):
        body = """index = 0
while True:
    emit({'type':'item.completed','item':{'id':str(index),'type':'command_execution','command':'true','exit_code':0}})
    index += 1
    time.sleep(.06)
"""
        code, expired, _, _ = self.activity_child(body, idle=1.5, total=0.35)
        self.assertNotEqual(0, code)
        self.assertTrue(expired)

    def exercise_blocked_monitor(self, *, total, idle=300):
        # Establish the blocked phase first, then advance the original limit.
        # Physical waits below are cleanup guards, never the deadline oracle.
        with tempfile.TemporaryDirectory() as temp:
            entered, release, exited = (threading.Event() for _ in range(3))
            clock, timers, observed_exit, errors = [0.0], [], [], []

            class Child:
                pid, returncode = 424242, None

                def poll(self):
                    return self.returncode

                def wait(self, timeout=None):
                    if not exited.wait(2):
                        raise subprocess.TimeoutExpired("fixture provider", timeout)
                    return self.returncode

                def kill(self):
                    self.returncode = -signal.SIGKILL
                    exited.set()

            child = Child()
            row = {"pid": child.pid, "birth_identity": 1.0}

            class Tree:
                def __init__(self, pid, checkpoint):
                    self.known, self.checkpoint = {}, checkpoint

                def capture_root(self):
                    self.known[child.pid] = row
                    self.checkpoint([row])

                def sample(self, **kwargs):
                    return [row]

                def stop(self, child):
                    if child.poll() is None:
                        child.kill()

            class Timer:
                def __init__(self, interval, function, args=()):
                    self.interval, self.function, self.args, self.due = interval, function, args, None
                    timers.append(self)

                def start(self):
                    self.due = clock[0] + self.interval

                def cancel(self):
                    self.due = None

            class BlockedMonitor(ActivityMonitor):
                holding_lock = False

                def poll(self, *args, **kwargs):
                    with self._lock:
                        self.holding_lock = True
                        entered.set()
                        try:
                            if not release.wait(2):
                                raise RuntimeError("fixture monitor cleanup guard expired")
                            return super().poll(*args, **kwargs)
                        finally:
                            self.holding_lock = False

            monitor = BlockedMonitor(Path(temp) / "events.jsonl", idle_seconds=idle, clock=lambda: clock[0])

            def advance_after_monitor_blocks():
                try:
                    if not entered.wait(2):
                        raise RuntimeError("fixture never entered the blocked monitor phase")
                    clock[0] = 0.15
                    for timer in list(timers):
                        if timer.due is not None and timer.due <= clock[0]:
                            timer.due = None
                            timer.function(*timer.args)
                    if not exited.wait(2):
                        raise RuntimeError("independent supervisor did not stop blocked provider")
                    observed_exit.append((child.returncode, monitor.holding_lock))
                except Exception as error:
                    errors.append(error)
                finally:
                    release.set()

            def terminate(pid, sig):
                self.assertEqual(child.pid, pid)
                child.returncode = -sig
                exited.set()

            advancer = threading.Thread(target=advance_after_monitor_blocks)
            advancer.start()
            try:
                with (
                    patch.object(processes.time, "monotonic", lambda: clock[0]),
                    patch.object(processes.threading, "Timer", Timer),
                    patch.object(processes, "ProcessTree", Tree),
                    patch.object(processes.os, "getpgid", return_value=child.pid),
                    patch.object(processes.os, "killpg", terminate),
                ):
                    code, expired = processes.wait_for_stage(child, total, lambda _rows: None, activity=monitor)
                advancer.join(timeout=2)
                self.assertFalse(advancer.is_alive())
                self.assertEqual([], errors)
                self.assertTrue(entered.is_set())
                self.assertTrue(expired)
                self.assertEqual(-signal.SIGTERM, code)
                self.assertEqual([(code, True)], observed_exit)
                self.assertEqual(0.15, clock[0])
                return monitor.timeout["kind"]
            finally:
                release.set()
                child.kill()
                advancer.join(timeout=2)

    def test_hard_cap_stops_worker_while_monitor_holds_its_lock(self):
        self.assertEqual("stage", self.exercise_blocked_monitor(total=0.15))

    def test_idle_limit_stops_worker_while_monitor_holds_its_lock(self):
        self.assertEqual("idle", self.exercise_blocked_monitor(total=None, idle=0.15))

    def test_monitor_and_snapshot_errors_still_stop_and_clean_worker(self):
        root_captured = threading.Event()
        poll_entered = threading.Event()

        class BrokenMonitor:
            def poll(self, *args, **kwargs):
                if not root_captured.wait(15):
                    raise RuntimeError("fixture root capture guard expired")
                poll_entered.set()
                raise RuntimeError("fixture event read failed")

            def snapshot(self):
                raise RuntimeError("fixture snapshot failed")

        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
        rescued = threading.Event()
        owned = []

        def rescue_broken_test():
            if child.poll() is None:
                rescued.set()
                child.kill()

        # Guard fixture cleanup after genuine admission, not the speed of native
        # root lookup or host scheduling. A rescue kill must still fail the test.
        safety_timer = threading.Timer(15, rescue_broken_test)
        guard_started = False
        captured = []

        def after_root_capture(tree, release_writer_token):
            nonlocal guard_started
            captured.extend(tree.known.values())
            safety_timer.start()
            guard_started = True
            root_captured.set()

        try:
            with self.assertRaisesRegex(processes.ProcessError, "Activity supervision failed"):
                processes.wait_for_stage(
                    child,
                    None,
                    lambda rows: owned.__setitem__(slice(None), rows),
                    activity=BrokenMonitor(),
                    on_root=after_root_capture,
                )
            # Freeze the guard outcome before checking real exit: the rescue
            # must not kill a leaked worker between the result assertions.
            safety_timer.cancel()
            if guard_started:
                safety_timer.join(timeout=2)
            self.assertFalse(safety_timer.is_alive())
            self.assertTrue(root_captured.is_set())
            self.assertTrue(poll_entered.is_set())
            self.assertIn(child.pid, [row["pid"] for row in captured])
            self.assertIn(child.pid, [row["pid"] for row in owned])
            self.assertFalse(rescued.is_set(), "The monitor error failed to stop its worker")
            self.assertIsNotNone(child.poll())
            self.assertEqual([], processes.live_processes(owned))
        finally:
            if guard_started:
                safety_timer.cancel()
                safety_timer.join(timeout=2)
            if child.poll() is None:
                child.kill()
                child.wait()

    def test_idle_watchdog_fires_while_process_sampling_is_stalled(self):
        original_sample = processes.ProcessTree.sample
        stopped_during_stall = []

        def factory(child):
            calls = 0

            def sample(tree, *args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    time.sleep(0.7)
                    stopped_during_stall.append(child.poll())
                return original_sample(tree, *args, **kwargs)

            return sample

        code, expired, _, reason = self.activity_child("time.sleep(30)\n", idle=0.2, sample=factory)
        self.assertNotEqual(0, code)
        self.assertTrue(expired)
        self.assertEqual("idle", reason["kind"])
        self.assertIsNotNone(stopped_during_stall[0])

    def test_quiet_descendant_without_start_event_gets_bounded_tool_grace(self):
        # OpenCode currently emits a completed tool row but no start row. A prior
        # turn event is still required before process fallback may suppress idle
        # (#1098); real providers emit session/turn traffic before tools run.
        body = """emit({'type':'turn.started','turn_id':'one'})
worker = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(3)'], start_new_session=True)
Path('worker.pid').write_text(str(worker.pid))
worker.wait()
emit({'type':'tool_use','sessionID':'one','part':{'id':'part1','tool':'bash','state':{'status':'completed','input':{'command':'quiet tests'},'metadata':{'exit':0}}}})
"""
        code, expired, snapshots, _ = self.activity_child(body, idle=2, tool=8)
        self.assertEqual(0, code)
        self.assertFalse(expired)
        self.assertTrue(snapshots)
        self.assertTrue(any(row.get("process_fallback") for row in snapshots))

    def test_silent_descendant_without_events_expires_idle_not_tool(self):
        # Hung transport: live child, empty stage log — idle, not tool (#1098).
        body = """worker = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
Path('worker.pid').write_text(str(worker.pid))
time.sleep(30)
"""
        code, expired, snapshots, reason = self.activity_child(body, idle=0.4, tool=8, require_worker=True)
        self.assertNotEqual(0, code)
        self.assertTrue(expired)
        self.assertEqual("idle", reason["kind"])
        self.assertFalse(any(row.get("process_fallback") for row in snapshots))

    def test_startup_grace_allows_a_brief_unreported_launch(self):
        # The quiet interval must exceed idle, with enough grace for CI scheduling.
        # A near-boundary exit tests host load rather than startup-grace behavior.
        code, expired, _, reason = self.activity_child("time.sleep(.5)\n", idle=0.1, tool=2, startup_grace=3, total=10)
        self.assertEqual(0, code)
        self.assertFalse(expired, reason)

    def test_startup_grace_still_expires_for_a_silent_worker(self):
        code, expired, _, reason = self.activity_child(
            "time.sleep(30)\n", idle=0.1, tool=2, startup_grace=0.5, total=10
        )
        self.assertNotEqual(0, code)
        self.assertTrue(expired)
        self.assertEqual("idle", reason["kind"])

    def test_idle_mcp_helper_does_not_delay_provider_inactivity_timeout(self):
        body = """helper = Path('mcp-server-fixture')
helper.symlink_to('/bin/sleep')
worker = subprocess.Popen([str(helper.resolve()), '30'], start_new_session=True)
Path('worker.pid').write_text(str(worker.pid))
time.sleep(30)
"""
        backend = type(processes.psutil.Process()._proc)
        with patch.object(backend, "name", return_value="mcp-server-fixture"):
            code, expired, snapshots, reason = self.activity_child(body, idle=3, tool=5, require_worker=True)
        self.assertNotEqual(0, code)
        self.assertTrue(expired)
        self.assertEqual("idle", reason["kind"])
        self.assertTrue(snapshots)
        self.assertFalse(any(row.get("process_fallback") for row in snapshots))

    def test_tool_deadline_stops_detached_writer_and_retains_identity(self):
        body = """emit({'type':'item.started','item':{'id':'test','type':'command_execution','command':'stuck tests','status':'in_progress'}})
worker = subprocess.Popen([sys.executable, '-c', "import time; from pathlib import Path; time.sleep(10); Path('late-write').write_text('leaked')"], start_new_session=True)
Path('worker.pid').write_text(str(worker.pid))
time.sleep(30)
"""
        code, expired, _, reason = self.activity_child(body, idle=2, tool=4)
        self.assertNotEqual(0, code)
        self.assertTrue(expired)
        self.assertEqual("tool", reason["kind"])

    def exercise_tree(self, *, expire):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            worker = root / "worker.py"
            worker.write_text(
                "from pathlib import Path\nimport os,signal\nsignal.signal(signal.SIGUSR1,lambda *_:None)\nPath('worker.pid').write_text(str(os.getpid()))\nprint('worker-ready',flush=True)\nsignal.pause()\nPath('late-write').write_text('leaked')\n"
            )
            parent = root / "parent.py"
            parent.write_text(
                "import subprocess,sys\nsubprocess.Popen([sys.executable,'worker.py'],start_new_session=True)\nsys.stdin.read(1)\n"
            )
            child = subprocess.Popen(
                [sys.executable, str(parent)],
                cwd=root,
                start_new_session=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
            )
            saved = []
            timers, workers = [], []
            original_timer = threading.Timer

            def timer(interval, callback, args=()):
                result = original_timer(interval, callback, args=args)
                if interval == 30:
                    timers.append(result)
                return result

            def checkpoint(rows):
                saved[:] = rows
                worker_pid = root / "worker.pid"
                if workers or not worker_pid.exists():
                    return
                value = worker_pid.read_text().strip()
                if not value:
                    return
                pid = int(value)
                if pid not in [row["pid"] for row in rows]:
                    return
                workers.append(processes.psutil.Process(pid))
                # Release the provider only once its detached writer is owned.
                # Fire the real termination callback explicitly for the timeout
                # case; the 30s timer is only a guard for a broken handshake.
                if expire:
                    timers[0].function(*timers[0].args)
                else:
                    child.stdin.write("x")
                    child.stdin.flush()

            stage_started = False
            try:
                # Ownership checkpoints publish membership changes, not fixture
                # file readiness. Wait until the detached writer has published
                # its pid before supervision can checkpoint that membership.
                self.assertTrue(select.select([child.stdout], [], [], 15)[0], "fixture worker did not become ready")
                self.assertEqual("worker-ready", child.stdout.readline().strip())
                stage_started = True
                with patch.object(threading, "Timer", timer):
                    code, expired = processes.wait_for_stage(child, 30, checkpoint)
                pid = int((root / "worker.pid").read_text())
                self.assertIn(pid, [p["pid"] for p in saved])
                self.assertEqual([], processes.live_processes(saved))
                return code, expired
            finally:
                try:
                    if child.poll() is None:
                        if not stage_started:
                            processes.ProcessTree(child.pid, lambda _rows: None).stop(child)
                        else:
                            child.kill()
                        child.wait()
                finally:
                    child.stdin.close()
                    child.stdout.close()
                for worker_process in workers:
                    cleanup_fixture_worker(worker_process)

    def test_timeout_stops_detached_tool_processes(self):
        code, expired = self.exercise_tree(expire=True)
        self.assertTrue(expired)
        self.assertNotEqual(0, code)

    def test_watchdog_enforces_timeout_when_process_sampling_stalls(self):
        """A stuck ps/sample call cannot silently turn a bounded stage unbounded."""
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
        original_sample = processes.ProcessTree.sample
        calls = 0
        exit_during_stall = []

        def stalled_sample(tree, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                # Keep sampling blocked until the real watchdog stops this
                # worker; a fixed sleep instead races host scheduling latency.
                exit_during_stall.append(child.wait(timeout=3))
            return original_sample(tree, *args, **kwargs)

        try:
            with patch.object(processes.ProcessTree, "sample", stalled_sample):
                code, expired = processes.wait_for_stage(child, 0.05, lambda _rows: None)
            self.assertTrue(expired)
            self.assertNotEqual(0, code)
            # Verify the watchdog stopped the worker before sampling returned;
            # total cleanup time also includes host-dependent ps latency.
            self.assertEqual([code], exit_during_stall)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()

    def test_hard_cap_escalates_when_provider_ignores_term(self):
        script = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(30)"
        child = subprocess.Popen(
            [sys.executable, "-u", "-c", script], stdout=subprocess.PIPE, text=True, start_new_session=True
        )
        original = processes.ProcessTree.sample
        observed_exit = []

        def stalled(tree, *args, **kwargs):
            if kwargs.get("initial"):
                time.sleep(3)
                observed_exit.append(child.poll())
            return original(tree, *args, **kwargs)

        try:
            self.assertTrue(select.select([child.stdout], [], [], 15)[0])
            self.assertEqual("ready", child.stdout.readline().strip())
            with patch.object(processes.ProcessTree, "sample", stalled):
                code, expired = processes.wait_for_stage(child, 0.05, lambda _rows: None)
            self.assertTrue(expired)
            self.assertEqual(-signal.SIGKILL, code)
            self.assertEqual([code], observed_exit)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=3)
            child.stdout.close()

    def test_cleanup_scan_failure_kills_previously_frozen_workers(self):
        child = subprocess.Popen(["/bin/sleep", "30"], start_new_session=True)
        tree = processes.ProcessTree(child.pid, lambda _rows: None)
        try:
            tree.sample(initial=True)
            original = tree.sample
            calls = 0

            def fail_during_cleanup(**kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise processes.ProcessError("fixture ancestry failure")
                return original(**kwargs)

            with patch.object(tree, "sample", side_effect=fail_during_cleanup):
                with self.assertRaisesRegex(processes.ProcessError, "fixture ancestry failure"):
                    tree.stop(child)
            child.wait(timeout=3)
            self.assertEqual([], processes.live_processes(list(tree.known.values())))
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=3)

    def test_normal_provider_exit_stops_background_writers(self):
        code, expired = self.exercise_tree(expire=False)
        self.assertFalse(expired)
        self.assertEqual(0, code)

    def test_checkpoint_failure_still_cleans_up_child(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)

        def checkpoint(rows):
            raise OSError("fixture disk full")

        with self.assertRaisesRegex(OSError, "disk full"):
            processes.wait_for_stage(child, 5, checkpoint)
        self.assertIsNotNone(child.poll())

    def test_reused_pid_is_not_a_live_owned_process(self):
        row = processes.process_table()[os.getpid()]
        self.assertEqual(
            [], processes.live_processes([{**processes.identity(row), "birth_identity": row["birth_identity"] - 1}])
        )
        legacy = {key: value for key, value in processes.identity(row).items() if key != "birth_identity"}
        self.assertEqual([], processes.live_processes([{**legacy, "started": "old process"}]))

    def test_sigterm_cleans_up_before_interrupt_propagates(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)

        def checkpoint(rows):
            os.kill(os.getpid(), signal.SIGTERM)

        with processes.interruption_handler(), self.assertRaises(KeyboardInterrupt):
            processes.wait_for_stage(child, 5, checkpoint)
        self.assertIsNotNone(child.poll())

    def test_second_signal_while_interrupt_unwinds_does_not_skip_cleanup(self):
        # A terminal close delivers SIGHUP twice (the kernel and the shell), and people press
        # Ctrl-C again. Raising a second time while the first interrupt unwinds skipped this
        # cleanup, or left a lock held so the controller hung ignoring signals (#454).
        self.addCleanup(signal.signal, signal.SIGHUP, signal.signal(signal.SIGHUP, signal.SIG_DFL))
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
        self.addCleanup(lambda: child.poll() is None and (child.kill(), child.wait()))

        def checkpoint(rows):
            os.kill(os.getpid(), signal.SIGHUP)

        install, landed = signal.signal, []

        def second_signal_lands_in_cleanup(sig, handler):
            # Where the real race landed: the second handler runs as cleanup takes the signals over.
            if not landed:
                landed.append(sig)
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            return install(sig, handler)

        with (
            processes.interruption_handler(),
            self.assertRaises(KeyboardInterrupt) as caught,
            patch.object(signal, "signal", second_signal_lands_in_cleanup),
        ):
            processes.wait_for_stage(child, 5, checkpoint)
        self.assertTrue(landed)
        self.assertIsNotNone(child.poll(), "the provider is stopped before the interrupt propagates")
        self.assertEqual("SIGHUP", str(caught.exception))

    def test_only_the_first_signal_interrupts(self):
        for sig in (signal.SIGTERM, signal.SIGHUP):
            self.addCleanup(signal.signal, sig, signal.signal(sig, signal.SIG_DFL))
        self.addCleanup(signal.signal, signal.SIGINT, signal.signal(signal.SIGINT, signal.default_int_handler))
        again = []
        with processes.interruption_handler():
            with self.assertRaises(KeyboardInterrupt) as caught:
                os.kill(os.getpid(), signal.SIGINT)
                time.sleep(15)  # ends as soon as the handler raises
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                try:
                    signal.getsignal(sig)(sig, None)  # absorbed while the first interrupt is handled
                except KeyboardInterrupt:
                    again.append(signal.Signals(sig).name)
        self.assertEqual([], again, "a second signal must not interrupt the first one's cleanup")
        self.assertEqual("SIGINT", str(caught.exception))
        self.assertIs(signal.default_int_handler, signal.getsignal(signal.SIGINT), "nothing holds it past the scope")

    def test_an_interrupted_stage_absorbs_later_signals_until_its_invocation_ends(self):
        # The stage's scope closes before its caller saves the pause and the CLI exits. A second
        # signal there escaped as a bare KeyboardInterrupt or killed the controller, leaving the
        # run RUNNING (#454). A later stage of the same invocation can still be interrupted.
        reached = []

        def original(signum, frame):
            reached.append(signal.Signals(signum).name)

        for sig in (signal.SIGTERM, signal.SIGHUP):
            self.addCleanup(signal.signal, sig, signal.signal(sig, original))
        self.addCleanup(signal.signal, signal.SIGINT, signal.signal(signal.SIGINT, signal.default_int_handler))
        with processes.interrupts_held():
            with self.assertRaises(KeyboardInterrupt), processes.interruption_handler():
                signal.raise_signal(signal.SIGHUP)
            for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
                try:
                    signal.raise_signal(sig)  # while the pause is saved
                except KeyboardInterrupt:
                    reached.append("SIGINT")
            self.assertEqual([], reached)
            with self.assertRaises(KeyboardInterrupt) as caught, processes.interruption_handler():
                signal.raise_signal(signal.SIGINT)
            self.assertEqual("SIGINT", str(caught.exception))
        # A caller's own handlers hear of them once, afterwards; Ctrl-C's default was answered.
        self.assertEqual(["SIGTERM", "SIGHUP"], reached)
        self.assertEqual(
            [original, original, signal.default_int_handler],
            [signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)],
        )

    def test_a_process_that_ends_with_the_invocation_goes_on_ignoring_them(self):
        # The CLI restored the defaults as main() returned, so a late Ctrl-C or hangup killed it
        # after its pause was saved: exit -2, -15 or -1 instead of 2 (#454).
        def own(signum, frame):
            pass

        self.addCleanup(signal.signal, signal.SIGTERM, signal.signal(signal.SIGTERM, signal.SIG_DFL))
        self.addCleanup(signal.signal, signal.SIGHUP, signal.signal(signal.SIGHUP, own))
        self.addCleanup(signal.signal, signal.SIGINT, signal.signal(signal.SIGINT, signal.default_int_handler))
        with processes.interrupts_held(until_exit=True):
            pass
        self.assertEqual(
            [signal.SIG_DFL, own, signal.default_int_handler],
            [signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)],
            "nothing changes without an interrupted stage",
        )
        with processes.interrupts_held(until_exit=True):
            with processes.interrupts_held():  # main() inside cli()
                with self.assertRaises(KeyboardInterrupt), processes.interruption_handler():
                    signal.raise_signal(signal.SIGTERM)
        self.assertEqual(
            [signal.SIG_IGN, own, signal.SIG_IGN],
            [signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)],
        )

    def test_a_nested_scope_shares_the_stages_one_interrupt(self):
        # A scope inside a stage's (a captured command, say) recorded the stage's own handler as
        # the disposition to restore, so the stage's later signals were no longer absorbed.
        reached = []

        def original(signum, frame):
            reached.append(signal.Signals(signum).name)

        for sig in (signal.SIGTERM, signal.SIGHUP):
            self.addCleanup(signal.signal, sig, signal.signal(sig, original))
        self.addCleanup(signal.signal, signal.SIGINT, signal.signal(signal.SIGINT, signal.default_int_handler))
        with processes.interrupts_held():
            with self.assertRaises(KeyboardInterrupt) as caught, processes.interruption_handler():
                with processes.interruption_handler():
                    signal.raise_signal(signal.SIGTERM)
            self.assertEqual("SIGTERM", str(caught.exception))
            signal.raise_signal(signal.SIGHUP)  # while the pause is saved
            self.assertEqual([], reached)
        self.assertEqual(["SIGHUP"], reached)
        self.assertEqual([original, original], [signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGHUP)])

    def test_ignored_or_replaced_sigint_is_left_alone(self):
        # A background job inherits SIGINT ignored; a caller may install its own handler.
        def own(signum, frame):
            pass

        for handler in (signal.SIG_IGN, own):
            with self.subTest(handler=handler):
                previous = signal.signal(signal.SIGINT, handler)
                try:
                    with processes.interruption_handler():
                        self.assertIs(handler, signal.getsignal(signal.SIGINT))
                finally:
                    signal.signal(signal.SIGINT, previous)

    def signal_during_cleanup(self, sig, joined):
        """Stop a provider that exited on its own while sig arrives as its process worker is joined."""
        join = processes.process_receipts.ReceiptWorker.join
        child = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
        self.addCleanup(lambda: child.poll() is None and (child.kill(), child.wait()))

        def signalled_join(worker):
            os.kill(os.getpid(), sig)
            join(worker)
            joined.append(child.poll())

        with patch.object(processes.process_receipts.ReceiptWorker, "join", signalled_join):
            return processes.wait_for_stage(child, 5, lambda rows: None)

    def test_a_signal_during_cleanup_interrupts_once_the_cleanup_finished(self):
        # A hangup as the provider ends raised inside the cleanup, most often while it joined the
        # process worker, and cut it short; ignored instead, it was lost and the run went on (#454).
        for sig in (signal.SIGTERM, signal.SIGHUP):
            self.addCleanup(signal.signal, sig, signal.signal(sig, signal.SIG_DFL))
        self.addCleanup(signal.signal, signal.SIGINT, signal.signal(signal.SIGINT, signal.default_int_handler))
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=signal.Signals(sig).name):
                joined = []
                with processes.interruption_handler(), self.assertRaises(KeyboardInterrupt) as caught:
                    self.signal_during_cleanup(sig, joined)
                self.assertEqual(signal.Signals(sig).name, str(caught.exception))
                self.assertEqual([0], joined, "the provider is collected before the interrupt propagates")

    def test_a_deferred_signal_keeps_the_callers_own_disposition(self):
        # Deferred, not turned into an interrupt: under nohup the hangup stays ignored.
        self.addCleanup(signal.signal, signal.SIGHUP, signal.signal(signal.SIGHUP, signal.SIG_IGN))
        joined = []
        with processes.interruption_handler():
            self.assertEqual((0, False), self.signal_during_cleanup(signal.SIGHUP, joined))
        self.assertEqual([0], joined)


class ProcessCleanupReapTests(unittest.TestCase):
    def reap_failure(self, *, phase, reused=False, deny_kill=False):
        """Inject a reap timeout, without real processes or wall-clock waits."""
        pid = 2**22 - 10
        owned = {
            "pid": pid,
            "started": "owned fixture",
            "birth_identity": 1.0,
            "birth_time": 1.0,
            "state": "sleeping",
            "parent": 90,
            "group": pid,
        }
        current = dict(owned)
        sentinel = {**owned, "pid": pid + 1, "group": pid + 1}
        tree = processes.ProcessTree(pid, lambda _rows: None)
        tree.known[pid] = owned
        child = MagicMock(pid=pid)
        child.poll.return_value = None
        reap_error = subprocess.TimeoutExpired("controlled child", 1)
        sent, clock = [], {"now": 0.0}

        def reap(*, timeout):
            if reused:
                current["birth_identity"] = 2.0
            raise reap_error

        child.wait.side_effect = reap

        def table(selected=None):
            rows = {pid: current, sentinel["pid"]: sentinel}
            return rows if selected is None else {value: rows[value] for value in selected if value in rows}

        def send(value, sig):
            sent.append((value, sig))
            if deny_kill and sig == signal.SIGKILL:
                raise PermissionError("controlled signal denial")

        def sample(**_kwargs):
            # Empty discovery plus an unreaped Popen is precisely the disputed
            # observation: it cannot authorize cleanup success.
            signal_seen = signal.SIGTERM if phase == "term" else signal.SIGKILL
            return [] if (pid, signal_seen) in sent else [owned]

        def advance(seconds):
            clock["now"] += seconds

        with (
            patch.object(tree, "sample", side_effect=sample),
            patch.object(processes, "process_table", side_effect=table),
            patch.object(processes.os, "kill", side_effect=send),
            patch.object(processes.time, "monotonic", side_effect=lambda: clock["now"]),
            patch.object(processes.time, "sleep", side_effect=advance),
        ):
            with self.assertRaises(processes.ProcessError) as rejected:
                tree.stop(child)
        child.wait.assert_called_once_with(timeout=1)
        self.assertEqual([owned], rejected.exception.processes)
        self.assertTrue(all(value == pid for value, _sig in sent), "Cleanup must not signal the unrelated sentinel")
        return rejected.exception, reap_error, sent, pid

    def test_term_phase_reap_timeout_kills_owned_root_and_remains_typed_uncertainty(self):
        error, timeout, sent, pid = self.reap_failure(phase="term")
        self.assertIs(timeout, error.__cause__)
        self.assertIn("Cannot verify child exit", str(error))
        self.assertNotIn("controlled child", str(error))
        self.assertIn((pid, signal.SIGKILL), sent)
        self.assertEqual((pid, signal.SIGCONT), sent[-1], "A frozen root must still receive its final resume")

    def test_kill_phase_reap_timeout_retries_owned_cleanup_without_returning_success(self):
        error, timeout, sent, pid = self.reap_failure(phase="kill")
        self.assertIs(timeout, error.__cause__)
        self.assertEqual(
            2, sent.count((pid, signal.SIGKILL)), "Reap failure must execute the recorded-worker fallback too"
        )
        self.assertEqual((pid, signal.SIGCONT), sent[-1])

    def test_reap_timeout_does_not_signal_a_reused_pid(self):
        error, timeout, sent, pid = self.reap_failure(phase="term", reused=True)
        self.assertIs(timeout, error.__cause__)
        self.assertNotIn((pid, signal.SIGKILL), sent, "Recorded ownership cannot transfer to a different birth")

    def test_reap_timeout_and_denied_force_cleanup_remain_typed_with_owned_receipt(self):
        error, _timeout, sent, pid = self.reap_failure(phase="term", deny_kill=True)
        self.assertIn("permission denied", str(error))
        self.assertIsInstance(error.__cause__, PermissionError)
        self.assertIn((pid, signal.SIGKILL), sent)
        self.assertEqual((pid, signal.SIGCONT), sent[-1])

    def test_observer_reap_waits_for_captured_root_identity(self):
        # A concurrent observer's child.poll() can reap a fast-exiting provider
        # before ProcessTree.sample records its birth identity. That left
        # active_stage.processes empty and blocked bounded startup recovery.
        class QuietMonitor:
            idle_limit = 0
            tool_limit = 0

            def poll(self, processes=None, root_pid=None):
                return {
                    "idle_seconds": 0,
                    "tool_elapsed_seconds": None,
                    "idle_limit_seconds": 0,
                    "tool_limit_seconds": 0,
                }

        # Hold the fixture until supervision starts, so native inspection does
        # not race an unrelated early exit under host load.
        child = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdin.read(1); raise SystemExit(7)"],
            stdin=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        owned = []
        reaped_before_return = []
        original_poll = child.poll

        def reaping_poll():
            # Force the observer's first child poll to find the provider exited and reap it, before the
            # owner's full sample. The observer starts before root capture (#621), so this would lose the
            # identity if it polled the child before the root was captured.
            thread = threading.current_thread()
            if getattr(getattr(thread, "_target", None), "__name__", None) == "observe" and not reaped_before_return:
                child.stdin.write("x")
                child.stdin.flush()
                reaped_before_return.append(child.wait(timeout=5))
            return original_poll()

        try:
            with patch.object(child, "poll", side_effect=reaping_poll):
                code, expired = processes.wait_for_stage(
                    child, None, lambda rows: owned.__setitem__(slice(None), rows), activity=QuietMonitor()
                )
            self.assertEqual(7, code)
            self.assertFalse(expired)
            self.assertEqual([7], reaped_before_return)
            self.assertIn(child.pid, [row["pid"] for row in owned])
            self.assertTrue(owned[0].get("birth_identity") is not None)
        finally:
            child.stdin.close()
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)

    def test_monitor_cleanup_guard_starts_after_worker_observation(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
        rescued = threading.Event()
        owned, guards = [], []

        def rescue_broken_test():
            if child.poll() is None:
                rescued.set()
                child.kill()

        class BrokenMonitor:
            def poll(self, *args, **kwargs):
                # The guard starts when supervision first observes the worker (after root capture) and
                # waits out the cleanup ProcessTree.stop is allowed: 2 s after SIGTERM, 2 s after SIGKILL,
                # 1 s to reap. Only a worker that outlives all of that is a leak (#848).
                if not guards:
                    guards.append(threading.Timer(3 * (2 + 2 + 1), rescue_broken_test))
                    guards[0].start()
                raise RuntimeError("fixture event read failed")

            def snapshot(self):
                raise RuntimeError("fixture snapshot failed")

        try:
            with self.assertRaisesRegex(processes.ProcessError, "Activity supervision failed"):
                processes.wait_for_stage(
                    child, None, lambda rows: owned.__setitem__(slice(None), rows), activity=BrokenMonitor()
                )
            self.assertTrue(guards, "The injected monitor failure was never observed")
            self.assertFalse(rescued.is_set(), "The monitor error failed to stop its worker")
            self.assertIsNotNone(child.poll())
            self.assertEqual([], processes.live_processes(owned))
        finally:
            for guard in guards:
                guard.cancel()
                guard.join(timeout=2)
            if child.poll() is None:
                child.kill()
                child.wait()



if __name__ == "__main__":
    unittest.main()
