"""Initial provider inspection and persistence cannot disable its deadlines."""

import json
import shutil
import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import autocode_process as processes
from autocode_activity import CHANGE_IDLE_LIMIT, JOB_IDLE_LIMIT, ActivityMonitor


class StartupDeadlineTests(unittest.TestCase):
    def run_provider(
        self,
        phase,
        *,
        idle=False,
        monitor_for=None,
        inspection_ticks=(10,),
        observer_at=None,
        running_at_ticks=None,
        published=None,
    ):
        # Drive the real supervisor with a fake provider, clock and scheduler.
        # A suspended event wait resumes at the next clock tick; nothing sleeps.
        class Suspended(BaseException):
            pass

        class Child:
            pid = 424242
            returncode = None

            def poll(self):
                if phase == "fast_exit":
                    self.returncode = 7
                return self.returncode

            def wait(self, timeout=None):
                return self.returncode

        child = Child()

        class Clock:
            value = 0.0

            def __call__(self):
                return self.value

            def set(self, value):
                self.value = value

            def supervise_at(self, value):
                self.set(value)
                for thread in list(threads):
                    if thread.target.__name__ == "supervise":
                        thread.tick()
                return child.returncode

        clock, timers, threads = Clock(), [], []

        class Timer:
            def __init__(self, interval, function, args=()):
                self.interval, self.function, self.args = interval, function, args
                self.due = None
                timers.append(self)

            def start(self):
                self.due = clock() + self.interval

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
                if self.target.__name__ == "observe" and observer_at is not None:
                    clock.set(observer_at)
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
            for instant in inspection_ticks:
                clock.set(instant)
                for timer in list(timers):
                    if timer.due is not None and timer.due <= clock():
                        timer.due = None
                        timer.function(*timer.args)
                for thread in list(threads):
                    # The controller is stalled here. Resume supervision only;
                    # process discovery progresses when the controller yields.
                    if thread.target.__name__ != "own_processes":
                        thread.tick()
                if running_at_ticks is not None:
                    running_at_ticks.append((instant, child.returncode is None))
            running_after_cap.append(child.returncode is None)
            # A provider that escaped enforcement finishes naturally after 10s.
            if child.returncode is None:
                child.returncode = 0

        row = {
            "pid": child.pid,
            "parent": 1,
            "group": child.pid,
            "started": "birth",
            "birth_time": 1.0,
            "birth_identity": 1.0,
            "state": "sleeping",
        }
        inspected = False

        def table(pids=None):
            nonlocal inspected
            if not inspected:
                inspected = True
                if phase == "inspection":
                    stall()
            return {child.pid: row} if child.returncode is None and child.pid in (pids or ()) else {}

        checkpointed = False

        def checkpoint(rows):
            nonlocal checkpointed
            owned[:] = rows
            if not checkpointed:
                checkpointed = True
                if phase == "checkpoint":
                    stall()

        class Monitor:
            idle_limit, tool_limit = 5, 0
            timeout = None

            def poll(self, **kwargs):
                return {
                    "idle_seconds": 0,
                    "tool_elapsed_seconds": None,
                    "idle_limit_seconds": 5,
                    "tool_limit_seconds": 0,
                }

        monitor = monitor_for(clock) if monitor_for else Monitor() if idle or phase == "fast_exit" else None
        proc = MagicMock()
        proc._ident = (child.pid, 1.0)
        proc.create_time.return_value = 1.0
        proc.children.return_value = []

        def terminate(pid, sig):
            self.assertEqual(child.pid, pid)
            child.returncode = -sig

        with (
            patch.object(processes.time, "monotonic", lambda: clock()),
            patch.object(processes.threading, "Timer", Timer),
            patch.object(processes.threading, "Thread", Thread),
            patch.object(processes.threading.Event, "wait", event_wait),
            patch.object(processes, "process_table", table),
            patch.object(processes, "process_ids", return_value=[child.pid]),
            patch.object(processes.psutil, "Process", return_value=proc),
            patch.object(processes.os, "getpgid", return_value=child.pid),
            patch.object(processes.os, "killpg", terminate),
            patch.object(processes.ProcessTree, "stop", lambda tree, child: None),
        ):
            code, expired = processes.wait_for_stage(
                child,
                None if idle else 5,
                checkpoint,
                activity=monitor,
                activity_checkpoint=published.append if published is not None else None,
            )
        return code, expired, owned, monitor, running_after_cap

    def assert_bounded(self, phase, *, idle=False):
        code, expired, owned, monitor, running = self.run_provider(phase, idle=idle)
        self.assertTrue(expired)
        self.assertEqual(-signal.SIGTERM, code)
        self.assertEqual([False], running)
        if phase == "checkpoint":
            self.assertEqual(424242, owned[0]["pid"])
        if idle:
            self.assertEqual("idle", monitor.timeout["kind"])

    def test_hard_cap_during_initial_checkpoint(self):
        self.assert_bounded("checkpoint")

    def test_hard_cap_during_initial_inspection(self):
        self.assert_bounded("inspection")

    def test_idle_cap_during_initial_checkpoint(self):
        self.assert_bounded("checkpoint", idle=True)

    def test_idle_cap_during_initial_inspection(self):
        self.assert_bounded("inspection", idle=True)

    def test_idle_stop_reason_comes_from_the_stages_own_monitor(self):
        # The supervisor stops the stage first when the observer's last poll lags; its reason must still be
        # the one the real monitor run_role built names: the limit, its origin and the stage's advice.
        events = Path(tempfile.mkdtemp(prefix="idle-reason-")) / "events.jsonl"
        self.addCleanup(shutil.rmtree, events.parent, True)
        events.touch()
        for origin, hint, expected in (
            (
                "runner_default",
                CHANGE_IDLE_LIMIT,
                "(5 seconds, runner default; change it with autocode resume --max-idle-seconds N)",
            ),
            (
                "user_explicit",
                JOB_IDLE_LIMIT,
                "(5 seconds, set explicitly; an exact job retry runs under the same limit; "
                "a different limit needs a new run)",
            ),
            (None, CHANGE_IDLE_LIMIT, "(5 seconds; change it with autocode resume --max-idle-seconds N)"),
        ):
            with self.subTest(origin=origin, hint=hint):

                def monitor_for(clock):
                    return ActivityMonitor(
                        events, idle_seconds=5, tool_seconds=0, clock=clock, idle_origin=origin, idle_hint=hint
                    )

                _, expired, _, monitor, _ = self.run_provider("inspection", idle=True, monitor_for=monitor_for)
                self.assertTrue(expired)
                self.assertEqual("idle", monitor.timeout["kind"])
                self.assertEqual(
                    "No new provider activity within the inactivity limit " + expected, monitor.timeout["reason"]
                )

    def test_watchdog_cannot_reap_before_identity_is_captured(self):
        code, expired, owned, _, _ = self.run_provider("fast_exit")
        self.assertEqual(7, code)
        self.assertFalse(expired)
        self.assertEqual([424242], [row["pid"] for row in owned])
        self.assertEqual(1.0, owned[0]["birth_identity"])

    def test_queued_tool_start_is_observed_during_root_inspection_without_extending_tool_cap(self):
        with tempfile.TemporaryDirectory() as temp:
            events = Path(temp) / "events.jsonl"
            events.write_text(
                json.dumps({"type": "item.started", "item": {"id": "queued-tool", "type": "command_execution"}}) + "\n"
            )
            running = []

            def monitor_for(clock):
                return ActivityMonitor(events, idle_seconds=5, tool_seconds=20, clock=clock)

            code, expired, _, monitor, _ = self.run_provider(
                "inspection", idle=True, monitor_for=monitor_for, inspection_ticks=(6, 20), running_at_ticks=running
            )
            self.assertEqual([(6, True), (20, False)], running)
            self.assertEqual(-signal.SIGTERM, code)
            self.assertTrue(expired)
            self.assertEqual("tool", monitor.timeout["kind"])

    def delayed_lock_monitor(self, clock, *, after_read=None):
        events = Path(tempfile.mkdtemp(prefix="sample-time-")) / "events.jsonl"
        self.addCleanup(shutil.rmtree, events.parent, True)

        class DelayedLock:
            entered = False

            def __enter__(self):
                if not self.entered:
                    self.entered = True
                    clock.set(3)

            def __exit__(self, *args):
                pass

        class Monitor(ActivityMonitor):
            def _read(self, now):
                super()._read(now)
                if after_read is not None:
                    after_read(clock)

        monitor = Monitor(events, idle_seconds=5, tool_seconds=20, clock=clock)
        monitor._lock = DelayedLock()
        return monitor

    def test_monitor_lock_wait_is_not_added_twice_to_idle_age(self):
        running, published = [], []
        code, expired, _, monitor, _ = self.run_provider(
            "checkpoint",
            idle=True,
            monitor_for=self.delayed_lock_monitor,
            observer_at=1,
            inspection_ticks=(3, 5),
            running_at_ticks=running,
            published=published,
        )
        self.assertEqual([(3, True), (5, False)], running)
        self.assertEqual(-signal.SIGTERM, code)
        self.assertTrue(expired)
        self.assertEqual("idle", monitor.timeout["kind"])
        self.assertTrue(published)
        self.assertTrue(all("_sampled_monotonic" not in value for value in published))

    def test_event_io_after_sampling_does_not_restart_the_idle_deadline(self):
        running = []
        stopped_while_reading = []

        def after_read(clock):
            stopped_while_reading.append(clock.supervise_at(5))
            clock.set(6)

        def monitor_for(clock):
            return self.delayed_lock_monitor(clock, after_read=after_read)

        code, expired, _, monitor, _ = self.run_provider(
            "checkpoint",
            idle=True,
            monitor_for=monitor_for,
            observer_at=1,
            inspection_ticks=(6,),
            running_at_ticks=running,
        )
        self.assertEqual([-signal.SIGTERM], stopped_while_reading)
        self.assertEqual([(6, False)], running)
        self.assertEqual(-signal.SIGTERM, code)
        self.assertTrue(expired)
        self.assertEqual("idle", monitor.timeout["kind"])

    def test_legacy_monitor_keeps_conservative_invocation_time(self):
        running = []

        def monitor_for(clock):
            class Legacy:
                idle_limit, tool_limit, timeout = 5, 0, None

                def poll(self, **kwargs):
                    clock.set(3)
                    return {
                        "idle_seconds": 3,
                        "tool_elapsed_seconds": None,
                        "idle_limit_seconds": 5,
                        "tool_limit_seconds": 0,
                    }

            return Legacy()

        code, expired, _, monitor, _ = self.run_provider(
            "checkpoint",
            idle=True,
            monitor_for=monitor_for,
            observer_at=1,
            inspection_ticks=(3,),
            running_at_ticks=running,
        )
        self.assertEqual([(3, False)], running)
        self.assertEqual(-signal.SIGTERM, code)
        self.assertTrue(expired)
        self.assertEqual("idle", monitor.timeout["kind"])

    def test_malformed_sample_timestamp_refuses_instead_of_granting_time(self):
        for stamp in (True, None, "3", float("nan"), float("inf"), -1, 2):
            with self.subTest(stamp=stamp):

                def monitor_for(clock):
                    class Invalid:
                        idle_limit, tool_limit, timeout = 5, 0, None

                        def poll(self, **kwargs):
                            return {
                                "idle_seconds": 0,
                                "tool_elapsed_seconds": None,
                                "idle_limit_seconds": 5,
                                "tool_limit_seconds": 0,
                                "_sampled_monotonic": stamp,
                            }

                    return Invalid()

                with self.assertRaisesRegex(processes.ProcessError, "Activity supervision failed"):
                    self.run_provider(
                        "checkpoint", idle=True, monitor_for=monitor_for, observer_at=1, inspection_ticks=(3,)
                    )

    def test_new_tool_observed_after_lock_wait_is_not_charged_before_it_started(self):
        running = []

        def monitor_for(clock):
            monitor = self.delayed_lock_monitor(clock)
            monitor.tool_limit = 5
            monitor.path.write_text(
                json.dumps({"type": "item.started", "item": {"id": "delayed-tool", "type": "command_execution"}}) + "\n"
            )
            return monitor

        code, expired, _, monitor, _ = self.run_provider(
            "checkpoint",
            idle=True,
            monitor_for=monitor_for,
            observer_at=1,
            inspection_ticks=(6, 8),
            running_at_ticks=running,
        )
        self.assertEqual([(6, True), (8, False)], running)
        self.assertEqual(-signal.SIGTERM, code)
        self.assertTrue(expired)
        self.assertEqual("tool", monitor.timeout["kind"])
