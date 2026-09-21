"""Real POSIX process-tree regressions; no inference or network access."""
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode_process as processes
from autocode_activity import ActivityMonitor


class ProcessTests(unittest.TestCase):
    def test_process_table_retains_executable_name_without_changing_identity(self):
        output = '101 90 101 Mon Sep 21 12:39:34 2026 S /Applications/Pencil App/mcp-server-darwin-arm64\n'
        result = subprocess.CompletedProcess([], 0, stdout=output, stderr='')
        with patch.object(processes.subprocess, 'run', return_value=result) as inspect:
            row = processes.process_table()[101]
        self.assertEqual('mcp-server-darwin-arm64', row['executable'])
        self.assertEqual({'pid': 101, 'started': 'Mon Sep 21 12:39:34 2026', 'group': 101}, processes.identity(row))
        self.assertEqual(['ps', '-axo', 'pid=,ppid=,pgid=,lstart=,stat=,comm='], inspect.call_args.args[0])

    def activity_child(self, body, *, idle=.45, tool=1.5, total=None, sample=None, require_worker=False):
        """Run a real event-writing worker without making cleanup speed an assertion."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            events = root / 'events.jsonl'
            worker = root / 'provider.py'
            worker.write_text('import json,os,subprocess,sys,time\nfrom pathlib import Path\n'
                              'def emit(row): print(json.dumps(row), flush=True)\n' + body)
            snapshots = []
            owned = []
            with events.open('w') as stream:
                child = subprocess.Popen([sys.executable, str(worker)], cwd=root, stdout=stream,
                                         start_new_session=True)
                monitor = ActivityMonitor(events, idle_seconds=idle, tool_seconds=tool)
                try:
                    if sample is None:
                        code, expired = processes.wait_for_stage(child, total,
                            lambda rows: owned.__setitem__(slice(None), rows), activity=monitor,
                            activity_checkpoint=lambda value: snapshots.append(value.copy()))
                    else:
                        with patch.object(processes.ProcessTree, 'sample', sample(child)):
                            code, expired = processes.wait_for_stage(child, total,
                                lambda rows: owned.__setitem__(slice(None), rows), activity=monitor,
                                activity_checkpoint=lambda value: snapshots.append(value.copy()))
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
    time.sleep(.12)
"""
        code, expired, snapshots, _ = self.activity_child(body)
        self.assertEqual(0, code)
        self.assertFalse(expired)
        self.assertTrue(snapshots)

    def test_quiet_running_tool_has_its_own_deadline(self):
        body = """emit({'type':'item.started','item':{'id':'test','type':'command_execution','command':'quiet tests','status':'in_progress'}})
time.sleep(.8)
emit({'type':'item.completed','item':{'id':'test','type':'command_execution','command':'quiet tests','exit_code':0}})
"""
        code, expired, snapshots, _ = self.activity_child(body, idle=.3, tool=1.8)
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
        body = """worker = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(.8)'], start_new_session=True)
Path('worker.pid').write_text(str(worker.pid))
worker.wait()
emit({'type':'tool_use','sessionID':'one','part':{'id':'part1','tool':'bash','state':{'status':'completed','input':{'command':'quiet tests'},'metadata':{'exit':0}}}})
"""
        code, expired, snapshots, _ = self.activity_child(body, idle=.4, tool=1.8)
        self.assertEqual(0, code)
        self.assertFalse(expired)
        self.assertTrue(snapshots)

    def test_idle_mcp_helper_does_not_delay_provider_inactivity_timeout(self):
        body = """import shutil
helper = Path('mcp-server-fixture')
shutil.copyfile('/bin/sleep', helper)
helper.chmod(0o700)
worker = subprocess.Popen([str(helper.resolve()), '30'], start_new_session=True)
Path('worker.pid').write_text(str(worker.pid))
time.sleep(30)
"""
        code, expired, snapshots, reason = self.activity_child(body, idle=.6, tool=3, require_worker=True)
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
        code, expired, _, reason = self.activity_child(body, idle=.4, tool=.8)
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
