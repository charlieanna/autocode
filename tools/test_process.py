"""Real POSIX process-tree regressions; no inference or network access."""
import os
import select
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode_process as processes
from autocode_activity import ActivityMonitor


class ProcessTests(unittest.TestCase):
    def test_process_id_enumeration_retries_transient_macos_sysctl_denial(self):
        with patch.object(processes.psutil, 'pids',
                          side_effect=[PermissionError('sysctl table refresh'), [101]]):
            self.assertEqual([101], processes.process_ids())

    def test_native_process_table_uses_birth_identity_without_shell_commands(self):
        process = MagicMock()
        process.create_time.return_value = 1790019574.123
        process.ppid.return_value = 90
        process.status.return_value = 'sleeping'
        process._proc.name.return_value = 'mcp-server-darw'
        with patch.object(processes.psutil, 'Process', return_value=process), \
             patch.object(processes.os, 'getpgid', return_value=101), \
             patch.object(processes.psutil, 'pids', return_value=[101]):
            row = processes.process_table()[101]
        self.assertEqual('mcp-server-darw', row['executable'])
        process.name.assert_not_called()
        process.cmdline.assert_not_called()
        self.assertTrue(ActivityMonitor._mcp_helper(row))
        self.assertEqual(1790019574.123, processes.identity(row)['birth_time'])
        self.assertTrue(processes.matches(processes.identity(row), row))
        self.assertFalse(processes.matches({**processes.identity(row), 'birth_time': 0}, row))
        legacy = {key: value for key, value in processes.identity(row).items() if key != 'birth_time'}
        self.assertTrue(processes.matches(legacy, row))

    def test_owned_access_denial_fails_closed_and_a_vanished_pid_is_safe(self):
        with patch.object(processes.psutil, 'Process', side_effect=processes.psutil.AccessDenied(101)):
            with self.assertRaisesRegex(processes.ProcessError, '101.*access denied'):
                processes.process_table({101})
        with patch.object(processes.psutil, 'Process', side_effect=processes.psutil.NoSuchProcess(101)):
            self.assertEqual({}, processes.process_table({101}))

    def test_optional_native_name_failure_retains_owned_identity(self):
        for error in (processes.psutil.AccessDenied(101), PermissionError('native name denied'),
                      SystemError('proc_cmdline returned a result with an exception set')):
            with self.subTest(error=type(error).__name__):
                process = MagicMock()
                process.create_time.return_value = 1790019574.123
                process.ppid.return_value = 90
                process.status.return_value = 'sleeping'
                process._proc.name.side_effect = error
                with patch.object(processes.psutil, 'Process', return_value=process), \
                     patch.object(processes.os, 'getpgid', return_value=101):
                    row = processes.process_table({101})[101]
                self.assertIsNone(row['executable'])
                self.assertFalse(ActivityMonitor._mcp_helper(row))
                self.assertEqual([row], processes.live_processes([processes.identity(row)], {101: row}))
                process.name.assert_not_called()
                process.cmdline.assert_not_called()

    def test_native_identity_system_error_fails_closed(self):
        for field in ('create_time', 'ppid', 'status'):
            with self.subTest(field=field):
                process = MagicMock()
                getattr(process, field).side_effect = SystemError('native identity unavailable')
                with patch.object(processes.psutil, 'Process', return_value=process):
                    with self.assertRaisesRegex(processes.ProcessError, '101.*SystemError.*blocked'):
                        processes.process_table({101})

    def test_provider_finishes_when_optional_native_name_is_unavailable(self):
        backend = type(processes.psutil.Process()._proc)
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(.2)'],
                                 start_new_session=True)
        owned = []
        try:
            with patch.object(backend, 'name', side_effect=SystemError('native name unavailable')):
                code, expired = processes.wait_for_stage(child, 5, lambda rows: owned.extend(rows))
            self.assertEqual(0, code)
            self.assertFalse(expired)
            self.assertIn(child.pid, [row['pid'] for row in owned])
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)

    def wait_ready(self, root, child):
        deadline = time.monotonic() + 15
        while not (root / 'ready').exists():
            if child.poll() is not None or time.monotonic() >= deadline:
                self.fail('Provider fixture failed to initialize')
            time.sleep(.01)
        (root / 'go').touch()

    def activity_child(self, body, *, idle=.45, tool=1.5, total=None, sample=None, require_worker=False,
                       startup_grace=0):
        """Run a real event-writing worker without making cleanup speed an assertion."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            events = root / 'events.jsonl'
            worker = root / 'provider.py'
            worker.write_text('import json,os,subprocess,sys,time\nfrom pathlib import Path\n'
                              'def emit(row): print(json.dumps(row), flush=True)\n'
                              "Path('ready').touch()\nwhile not Path('go').exists(): time.sleep(.01)\n" + body)
            snapshots = []
            owned = []
            with events.open('w') as stream:
                child = subprocess.Popen([sys.executable, str(worker)], cwd=root, stdout=stream,
                                         start_new_session=True)
                try:
                    self.wait_ready(root, child)
                    monitor = ActivityMonitor(events, idle_seconds=idle, tool_seconds=tool)
                    if sample is None:
                        code, expired = processes.wait_for_stage(child, total,
                            lambda rows: owned.__setitem__(slice(None), rows), activity=monitor,
                            activity_checkpoint=lambda value: snapshots.append(value.copy()),
                            startup_grace=startup_grace)
                    else:
                        with patch.object(processes.ProcessTree, 'sample', sample(child)):
                            code, expired = processes.wait_for_stage(child, total,
                                lambda rows: owned.__setitem__(slice(None), rows), activity=monitor,
                                activity_checkpoint=lambda value: snapshots.append(value.copy()),
                                startup_grace=startup_grace)
                    self.assertEqual([], processes.live_processes(owned))
                    self.assertFalse((root / 'late-write').exists())
                    if require_worker:
                        self.assertTrue((root / 'worker.pid').is_file(), 'The helper fixture must actually launch')
                    worker_pid = int((root / 'worker.pid').read_text()) if (root / 'worker.pid').exists() else None
                    if worker_pid is not None:
                        self.assertIn(worker_pid, [row['pid'] for row in owned])
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
time.sleep(.8)
emit({'type':'item.completed','item':{'id':'test','type':'command_execution','command':'quiet tests','exit_code':0}})
"""
        code, expired, snapshots, _ = self.activity_child(body, idle=2, tool=5)
        self.assertEqual(0, code)
        self.assertFalse(expired)
        self.assertTrue(any(row.get('active_tool_count', 0) for row in snapshots))

    def test_repeated_events_and_output_noise_do_not_keep_provider_alive(self):
        body = """while True:
    print('still here', flush=True)
    emit({'type':'item.completed','item':{'id':'one','type':'command_execution','command':'true','exit_code':0}})
    time.sleep(.06)
"""
        code, expired, _, reason = self.activity_child(body, idle=.35)
        self.assertNotEqual(0, code)
        self.assertTrue(expired)
        self.assertEqual('idle', reason['kind'])

    def test_explicit_stage_cap_still_wins_over_continuing_activity(self):
        body = """index = 0
while True:
    emit({'type':'item.completed','item':{'id':str(index),'type':'command_execution','command':'true','exit_code':0}})
    index += 1
    time.sleep(.06)
"""
        code, expired, _, _ = self.activity_child(body, idle=1.5, total=.35)
        self.assertNotEqual(0, code)
        self.assertTrue(expired)

    def exercise_blocked_monitor(self, *, total, idle=300):
        with tempfile.TemporaryDirectory() as temp:
            entered = threading.Event()
            release = threading.Event()
            observed_exit = []
            child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                                     start_new_session=True)

            class BlockedMonitor(ActivityMonitor):
                def poll(self, *args, **kwargs):
                    with self._lock:
                        entered.set()
                        release.wait(2)  # bounded even if the test assertion fails
                        return super().poll(*args, **kwargs)

            monitor = BlockedMonitor(Path(temp) / 'events.jsonl', idle_seconds=idle)

            def observe_before_release():
                try:
                    if entered.wait(1):
                        try:
                            observed_exit.append(child.wait(timeout=.8))
                        except subprocess.TimeoutExpired:
                            observed_exit.append(None)
                finally:
                    release.set()

            observer = threading.Thread(target=observe_before_release)
            observer.start()
            try:
                code, expired = processes.wait_for_stage(child, total, lambda _rows: None,
                                                          activity=monitor)
                self.assertTrue(expired)
                self.assertNotEqual(0, code)
                self.assertTrue(entered.is_set())
                observer.join(timeout=2)
                self.assertFalse(observer.is_alive())
                self.assertEqual([code], observed_exit)
                return monitor.timeout['kind']
            finally:
                release.set()
                observer.join(timeout=2)
                if child.poll() is None:
                    child.kill()
                    child.wait()

    def test_hard_cap_stops_worker_while_monitor_holds_its_lock(self):
        self.assertEqual('stage', self.exercise_blocked_monitor(total=.15))

    def test_idle_limit_stops_worker_while_monitor_holds_its_lock(self):
        self.assertEqual('idle', self.exercise_blocked_monitor(total=None, idle=.15))

    def test_monitor_and_snapshot_errors_still_stop_and_clean_worker(self):
        class BrokenMonitor:
            def poll(self, *args, **kwargs):
                raise RuntimeError('fixture event read failed')
            def snapshot(self):
                raise RuntimeError('fixture snapshot failed')

        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                                 start_new_session=True)
        rescued = threading.Event()
        owned = []

        def rescue_broken_test():
            if child.poll() is None:
                rescued.set()
                child.kill()

        safety_timer = threading.Timer(1.5, rescue_broken_test)
        safety_timer.start()
        try:
            with self.assertRaisesRegex(processes.ProcessError, 'Activity supervision failed'):
                processes.wait_for_stage(child, None, lambda rows: owned.__setitem__(slice(None), rows),
                                         activity=BrokenMonitor())
            self.assertFalse(rescued.is_set(), 'The monitor error failed to stop its worker')
            self.assertIsNotNone(child.poll())
            self.assertEqual([], processes.live_processes(owned))
        finally:
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
                    time.sleep(.7)
                    stopped_during_stall.append(child.poll())
                return original_sample(tree, *args, **kwargs)
            return sample
        code, expired, _, reason = self.activity_child('time.sleep(30)\n', idle=.2, sample=factory)
        self.assertNotEqual(0, code)
        self.assertTrue(expired)
        self.assertEqual('idle', reason['kind'])
        self.assertIsNotNone(stopped_during_stall[0])

    def test_quiet_descendant_without_start_event_gets_bounded_tool_grace(self):
        # OpenCode currently emits a completed tool row but no start row.
        body = """worker = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(3)'], start_new_session=True)
Path('worker.pid').write_text(str(worker.pid))
worker.wait()
emit({'type':'tool_use','sessionID':'one','part':{'id':'part1','tool':'bash','state':{'status':'completed','input':{'command':'quiet tests'},'metadata':{'exit':0}}}})
"""
        code, expired, snapshots, _ = self.activity_child(body, idle=2, tool=8)
        self.assertEqual(0, code)
        self.assertFalse(expired)
        self.assertTrue(snapshots)

    def test_startup_grace_allows_a_brief_unreported_launch(self):
        code, expired, _, _ = self.activity_child('time.sleep(.7)\n', idle=.2, tool=2,
                                                  startup_grace=1)
        self.assertEqual(0, code)
        self.assertFalse(expired)

    def test_idle_mcp_helper_does_not_delay_provider_inactivity_timeout(self):
        body = """helper = Path('mcp-server-fixture')
helper.symlink_to('/bin/sleep')
worker = subprocess.Popen([str(helper.resolve()), '30'], start_new_session=True)
Path('worker.pid').write_text(str(worker.pid))
time.sleep(30)
"""
        backend = type(processes.psutil.Process()._proc)
        with patch.object(backend, 'name', return_value='mcp-server-fixture'):
            code, expired, snapshots, reason = self.activity_child(body, idle=3, tool=5, require_worker=True)
        self.assertNotEqual(0, code)
        self.assertTrue(expired)
        self.assertEqual('idle', reason['kind'])
        self.assertTrue(snapshots)
        self.assertFalse(any(row.get('process_fallback') for row in snapshots))

    def test_tool_deadline_stops_detached_writer_and_retains_identity(self):
        body = """emit({'type':'item.started','item':{'id':'test','type':'command_execution','command':'stuck tests','status':'in_progress'}})
worker = subprocess.Popen([sys.executable, '-c', "import time; from pathlib import Path; time.sleep(10); Path('late-write').write_text('leaked')"], start_new_session=True)
Path('worker.pid').write_text(str(worker.pid))
time.sleep(30)
"""
        code, expired, _, reason = self.activity_child(body, idle=2, tool=4)
        self.assertNotEqual(0, code)
        self.assertTrue(expired)
        self.assertEqual('tool', reason['kind'])

    def exercise_tree(self, parent_lifetime, timeout):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            worker = root / 'worker.py'
            worker.write_text("from pathlib import Path\nimport os,time\nPath('worker.pid').write_text(str(os.getpid()))\ntime.sleep(10)\nPath('late-write').write_text('leaked')\n")
            parent = root / 'parent.py'
            parent.write_text("import subprocess,sys,time\nsubprocess.Popen([sys.executable,'worker.py'],start_new_session=True)\ntime.sleep(" + str(parent_lifetime) + ")\n")
            child = subprocess.Popen([sys.executable, str(parent)], cwd=root, start_new_session=True)
            saved = []
            try:
                code, expired = processes.wait_for_stage(child, timeout, lambda rows: saved.__setitem__(slice(None), rows))
                pid = int((root / 'worker.pid').read_text())
                self.assertIn(pid, [p['pid'] for p in saved])
                self.assertEqual([], processes.live_processes(saved))
                self.assertFalse((root / 'late-write').exists())
                return code, expired
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait()

    def test_timeout_stops_detached_tool_processes(self):
        code, expired = self.exercise_tree(30, 1)
        self.assertTrue(expired)
        self.assertNotEqual(0, code)

    def test_watchdog_enforces_timeout_when_process_sampling_stalls(self):
        """A stuck ps/sample call cannot silently turn a bounded stage unbounded."""
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
        original_sample = processes.ProcessTree.sample
        calls = 0
        exit_during_stall = []

        def stalled_sample(tree, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                time.sleep(.4)  # longer than the bounded stage deadline
                exit_during_stall.append(child.poll())
            return original_sample(tree, *args, **kwargs)

        try:
            with patch.object(processes.ProcessTree, 'sample', stalled_sample):
                code, expired = processes.wait_for_stage(child, .05, lambda _rows: None)
            self.assertTrue(expired)
            self.assertNotEqual(0, code)
            # Verify the watchdog stopped the worker before sampling returned;
            # total cleanup time also includes host-dependent ps latency.
            self.assertIsNotNone(exit_during_stall[0])
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()

    def test_hard_cap_escalates_when_provider_ignores_term(self):
        script = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(30)"
        child = subprocess.Popen([sys.executable, '-u', '-c', script], stdout=subprocess.PIPE,
                                 text=True, start_new_session=True)
        original = processes.ProcessTree.sample
        observed_exit = []

        def stalled(tree, *args, **kwargs):
            if kwargs.get('initial'):
                time.sleep(3)
                observed_exit.append(child.poll())
            return original(tree, *args, **kwargs)

        try:
            self.assertTrue(select.select([child.stdout], [], [], 15)[0])
            self.assertEqual('ready', child.stdout.readline().strip())
            with patch.object(processes.ProcessTree, 'sample', stalled):
                code, expired = processes.wait_for_stage(child, .05, lambda _rows: None)
            self.assertTrue(expired)
            self.assertEqual(-signal.SIGKILL, code)
            self.assertEqual([code], observed_exit)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=3)
            child.stdout.close()

    def test_cleanup_scan_failure_kills_previously_frozen_workers(self):
        child = subprocess.Popen(['/bin/sleep', '30'], start_new_session=True)
        tree = processes.ProcessTree(child.pid, lambda _rows: None)
        try:
            tree.sample(initial=True)
            original = tree.sample
            calls = 0

            def fail_during_cleanup(**kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise processes.ProcessError('fixture ancestry failure')
                return original(**kwargs)

            with patch.object(tree, 'sample', side_effect=fail_during_cleanup):
                with self.assertRaisesRegex(processes.ProcessError, 'fixture ancestry failure'):
                    tree.stop(child)
            child.wait(timeout=3)
            self.assertEqual([], processes.live_processes(list(tree.known.values())))
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=3)

    def test_normal_provider_exit_stops_background_writers(self):
        code, expired = self.exercise_tree(.8, 5)
        self.assertFalse(expired)
        self.assertEqual(0, code)

    def test_checkpoint_failure_still_cleans_up_child(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
        def checkpoint(rows):
            raise OSError('fixture disk full')
        with self.assertRaisesRegex(OSError, 'disk full'):
            processes.wait_for_stage(child, 5, checkpoint)
        self.assertIsNotNone(child.poll())

    def test_reused_pid_is_not_a_live_owned_process(self):
        row = processes.process_table()[os.getpid()]
        self.assertEqual([], processes.live_processes([{**processes.identity(row), 'started':'old process'}]))

    def test_sigterm_cleans_up_before_interrupt_propagates(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
        def checkpoint(rows):
            os.kill(os.getpid(), signal.SIGTERM)
        with processes.interruption_handler(), self.assertRaises(KeyboardInterrupt):
            processes.wait_for_stage(child, 5, checkpoint)
        self.assertIsNotNone(child.poll())


if __name__ == '__main__':
    unittest.main()
