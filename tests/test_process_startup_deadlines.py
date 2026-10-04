"""Initial provider inspection and persistence cannot disable its deadlines."""
from pathlib import Path
import shutil
import signal
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import autocode_process as processes
from autocode_activity import CHANGE_IDLE_LIMIT, JOB_IDLE_LIMIT, ActivityMonitor


class StartupDeadlineTests(unittest.TestCase):
    def run_provider(self, phase, *, idle=False, monitor_for=None):
        # Drive the real supervisor with a fake provider, clock and scheduler.
        # A suspended event wait resumes at the next clock tick; nothing sleeps.
        class Suspended(BaseException):
            pass

        class Child:
            pid = 424242
            returncode = None

            def poll(self):
                if phase == 'fast_exit':
                    self.returncode = 7
                return self.returncode

            def wait(self, timeout=None):
                return self.returncode

        child = Child()
        clock, timers, threads = [0.0], [], []

        class Timer:
            def __init__(self, interval, function, args=()):
                self.interval, self.function, self.args = interval, function, args
                self.due = None
                timers.append(self)

            def start(self):
                self.due = clock[0] + self.interval

            def cancel(self):
                self.due = None

        ticking = [False]

        class Thread:
            def __init__(self, target, daemon=False):
                self.target = target

            def tick(self):
                previous, ticking[0] = ticking[0], True
                try:
                    self.target()
                except Suspended:
                    pass
                finally:
                    ticking[0] = previous

            def start(self):
                threads.append(self)
                if self.target.__name__ != "own_processes":
                    self.tick()

            def is_alive(self):
                return False

            def join(self, timeout=None):
                pass

        def event_wait(event, timeout=None):
            if event.is_set():
                return True
            if ticking[0]:
                raise Suspended()
            # Let the independent process worker progress when the controller
            # waits; background waits still yield to this deterministic clock.
            for thread in threads:
                if thread.target.__name__ == "own_processes":
                    thread.tick()
            return event.is_set()

        running_after_cap, owned = [], []

        def stall():
            clock[0] = 10.0
            for timer in list(timers):
                if timer.due is not None and timer.due <= clock[0]:
                    timer.due = None
                    timer.function(*timer.args)
            for thread in list(threads):
                thread.tick()
            running_after_cap.append(child.returncode is None)
            # A provider that escaped enforcement finishes naturally after 10s.
            if child.returncode is None:
                child.returncode = 0

        row = {'pid': child.pid, 'parent': 1, 'group': child.pid, 'started': 'birth',
               'birth_time': 1.0, 'birth_identity': 1.0, 'state': 'sleeping'}
        inspected = False

        def table(pids=None):
            nonlocal inspected
            if not inspected:
                inspected = True
                if phase == 'inspection':
                    stall()
            return {child.pid: row} if child.returncode is None and child.pid in (pids or ()) else {}

        checkpointed = False

        def checkpoint(rows):
            nonlocal checkpointed
            owned[:] = rows
            if not checkpointed:
                checkpointed = True
                if phase == 'checkpoint':
                    stall()

        class Monitor:
            idle_limit, tool_limit = 5, 0
            timeout = None

            def poll(self, **kwargs):
                return {'idle_seconds': 0, 'tool_elapsed_seconds': None,
                        'idle_limit_seconds': 5, 'tool_limit_seconds': 0}

        monitor = (monitor_for(lambda: clock[0]) if monitor_for
                   else Monitor() if idle or phase == 'fast_exit' else None)
        proc = MagicMock()
        proc._ident = (child.pid, 1.0)
        proc.create_time.return_value = 1.0
        proc.children.return_value = []

        def terminate(pid, sig):
            self.assertEqual(child.pid, pid)
            child.returncode = -sig

        with patch.object(processes.time, 'monotonic', lambda: clock[0]), \
             patch.object(processes.threading, 'Timer', Timer), \
             patch.object(processes.threading, 'Thread', Thread), \
             patch.object(processes.threading.Event, 'wait', event_wait), \
             patch.object(processes, 'process_table', table), \
             patch.object(processes, 'process_ids', return_value=[child.pid]), \
             patch.object(processes.psutil, 'Process', return_value=proc), \
             patch.object(processes.os, 'getpgid', return_value=child.pid), \
             patch.object(processes.os, 'killpg', terminate), \
             patch.object(processes.ProcessTree, 'stop', lambda tree, child: None):
            code, expired = processes.wait_for_stage(child, None if idle else 5, checkpoint,
                                                     activity=monitor)
        return code, expired, owned, monitor, running_after_cap

    def assert_bounded(self, phase, *, idle=False):
        code, expired, owned, monitor, running = self.run_provider(phase, idle=idle)
        self.assertTrue(expired)
        self.assertEqual(-signal.SIGTERM, code)
        self.assertEqual([False], running)
        if phase == 'checkpoint':
            self.assertEqual(424242, owned[0]['pid'])
        if idle:
            self.assertEqual('idle', monitor.timeout['kind'])

    def test_hard_cap_during_initial_checkpoint(self):
        self.assert_bounded('checkpoint')

    def test_hard_cap_during_initial_inspection(self):
        self.assert_bounded('inspection')

    def test_idle_cap_during_initial_checkpoint(self):
        self.assert_bounded('checkpoint', idle=True)

    def test_idle_cap_during_initial_inspection(self):
        self.assert_bounded('inspection', idle=True)

    def test_idle_stop_reason_comes_from_the_stages_own_monitor(self):
        # The supervisor stops the stage first when the observer's last poll lags; its reason must still be
        # the one the real monitor run_role built names: the limit, its origin and the stage's advice.
        events = Path(tempfile.mkdtemp(prefix='idle-reason-')) / 'events.jsonl'
        self.addCleanup(shutil.rmtree, events.parent, True)
        events.touch()
        for origin, hint, expected in (
                ('runner_default', CHANGE_IDLE_LIMIT,
                 '(5 seconds, runner default; change it with --resume-paused --max-idle-seconds N)'),
                ('user_explicit', JOB_IDLE_LIMIT,
                 '(5 seconds, set explicitly; an exact job retry runs under the same limit; '
                 'a different limit needs a new run)'),
                (None, CHANGE_IDLE_LIMIT, '(5 seconds; change it with --resume-paused --max-idle-seconds N)')):
            with self.subTest(origin=origin, hint=hint):
                def monitor_for(clock):
                    return ActivityMonitor(events, idle_seconds=5, tool_seconds=0, clock=clock,
                                           idle_origin=origin, idle_hint=hint)
                _, expired, _, monitor, _ = self.run_provider('inspection', idle=True, monitor_for=monitor_for)
                self.assertTrue(expired)
                self.assertEqual('idle', monitor.timeout['kind'])
                self.assertEqual('No new provider activity within the inactivity limit ' + expected,
                                 monitor.timeout['reason'])

    def test_watchdog_cannot_reap_before_identity_is_captured(self):
        code, expired, owned, _, _ = self.run_provider('fast_exit')
        self.assertEqual(7, code)
        self.assertFalse(expired)
        self.assertEqual([424242], [row['pid'] for row in owned])
        self.assertEqual(1.0, owned[0]['birth_identity'])
