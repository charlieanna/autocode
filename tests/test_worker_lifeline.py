"""A Builder worker's lifeline (#454): the Orchestrator's declaration and receipt classification,
and how the worker's entry point reports a lifeline it refused."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_process as processes
import autocode_supervision_cli as supervision_cli
import autocode_worker_lifeline as lifeline


class LifelineFixture(unittest.TestCase):
    """One launch's record, and receipts written as its keeper would."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.me = processes.identity(processes.process_table({os.getpid()})[os.getpid()])
        self.record = {
            "schema": 1,
            "nonce": "a" * 32,
            "owner": self.me,
            "receipt": str(self.root / "lifeline" / ("a" * 32 + "-supervision.json")),
            "started_at": "then",
        }

    def write(self, **changes):
        value = {
            "schema": 1,
            "nonce": "a" * 32,
            "owner": self.me,
            "keeper": {"pid": 2**30, "birth_identity": 1.0},
            "provider": {"pid": 2**30 + 1, "birth_identity": 2.0},
            "receipt": self.record["receipt"],
            "phase": "armed",
            "cause": None,
            "cleanup_error": None,
            "processes": [],
            **changes,
        }
        path = Path(self.record["receipt"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return value


class WorkerLifelineTests(LifelineFixture):
    def test_prepare_declares_a_fresh_lifeline_without_a_deadline(self):
        record, read_fd, write_fd = lifeline.prepare(self.root)
        try:
            self.assertEqual({"schema", "nonce", "owner", "receipt", "started_at"}, set(record))
            self.assertTrue(processes.matches(record["owner"], self.me))
            self.assertEqual(self.root / "lifeline" / f"{record['nonce']}-supervision.json", Path(record["receipt"]))
            self.assertFalse(Path(record["receipt"]).exists())
            self.assertEqual(["--owner-lifeline-fd", str(read_fd)], lifeline.argv(read_fd))
            # The worker is the Orchestrator's child; here this process stands in for both.
            with patch.object(supervision_cli.os, "getppid", return_value=os.getpid()):
                packet, remaining = supervision_cli.declaration(read_fd)
            self.assertIsNone(remaining)
            self.assertEqual((None, None), (packet["deadline"], packet["timeout_seconds"]))
            # Its keeper's SIGTERM interrupts a running stage, whose provider guard waits up to 8 s for
            # the stage keeper, which allows its provider 2 s before SIGKILL: the window covers both.
            self.assertEqual(lifeline.KILL_AFTER_SECONDS, packet["kill_after_seconds"])
            self.assertGreaterEqual(lifeline.KILL_AFTER_SECONDS, 8 + 2)
            self.assertEqual(
                {key: record[key] for key in ("nonce", "owner", "receipt")},
                {key: packet[key] for key in ("nonce", "owner", "receipt")},
            )
        finally:
            os.close(read_fd)
            os.close(write_fd)
        second, read_fd, write_fd = lifeline.prepare(self.root)
        os.close(read_fd)
        os.close(write_fd)
        self.assertNotEqual(record["nonce"], second["nonce"])

    def test_classify_adopts_only_verified_cleanup(self):
        cases = [
            ({"phase": "armed"}, "running"),
            ({"phase": "stopping", "cause": "owner_lost"}, "running"),
            ({"phase": "discharged", "cause": "controller_finished"}, "discharged"),
            ({"phase": "stopped", "cause": "owner_lost"}, "stopped"),
            ({"phase": "stopped", "cause": "provider_stopped"}, "stopped"),
            ({"phase": "stopped", "cause": "owner_lost", "cleanup_error": "ProcessError"}, "uncertain"),
            ({"phase": "discharged", "cause": "owner_lost"}, "uncertain"),
            ({"phase": "discharged", "cause": "controller_finished", "cleanup_error": "x"}, "uncertain"),
            ({"phase": "uncertain", "cause": "keeper_failure"}, "uncertain"),
            (None, "uncertain"),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(expected, lifeline.classify(value))
        self.assertEqual({"discharged", "stopped", "never_armed"}, lifeline.ADOPTABLE)

    def test_settled_reads_only_the_receipt_bound_to_this_launch(self):
        self.assertEqual("never_armed", lifeline.settled(self.record))
        self.assertIsNone(lifeline.read(self.record))
        self.write()
        self.assertEqual("running", lifeline.settled(self.record))
        self.write(phase="stopped", cause="owner_lost")
        self.assertEqual("stopped", lifeline.settled(self.record))
        for changes in ({"nonce": "b" * 32}, {"owner": {**self.me, "birth_identity": 1.5}}, {"schema": 2}):
            with self.subTest(changes=changes):
                self.write(phase="stopped", cause="owner_lost", **changes)
                self.assertIsNone(lifeline.read(self.record))
                self.assertEqual("uncertain", lifeline.settled(self.record))
        Path(self.record["receipt"]).write_text("{not json")
        self.assertEqual("uncertain", lifeline.settled(self.record))

    def test_owned_adds_the_keeper_and_its_inventory_to_the_sampled_tree(self):
        sampled = [{"pid": 2**30 + 2, "birth_identity": 3.0}]
        self.assertEqual(sampled, lifeline.owned(self.record, sampled))
        self.assertEqual([], lifeline.owned(None))
        self.write(
            phase="stopped", keeper=self.me, processes=[{"pid": 2**30 + 3, "birth_identity": 4.0}, {"pid": "x"}, None]
        )
        owned = lifeline.owned(self.record, sampled)
        self.assertEqual([*sampled, self.me, {"pid": 2**30 + 3, "birth_identity": 4.0}], owned)
        # A keeper still running keeps the worker's milestone busy even when nothing else was sampled.
        self.assertEqual([os.getpid()], [row["pid"] for row in processes.live_processes(lifeline.owned(self.record))])
        self.write(phase="stopped", nonce="b" * 32, keeper=self.me)
        self.assertEqual(sampled, lifeline.owned(self.record, sampled))


class VirtualClock:
    """Time that only await_settled's sleeping advances; a scripted event may change the receipt meanwhile."""

    def __init__(self):
        self.now = 0.0
        self.events = []  # (virtual time, callable), run when the clock passes it

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        for event in [event for event in self.events if event[0] <= self.now]:
            self.events.remove(event)
            event[1]()


class AwaitSettledTests(LifelineFixture):
    def wait(self, seconds=None, *, at=None, then=None, events=()):
        clock = VirtualClock()
        clock.events.extend(events)
        if at is not None:
            clock.events.append((at, then))
        with patch.object(lifeline, "time", clock):
            if seconds is None:
                lifeline.await_settled([self.record])
            else:
                lifeline.await_settled([self.record], seconds)
        return clock.now

    def test_a_live_keeper_is_waited_for_as_long_as_the_window_it_was_declared(self):
        self.assertGreater(lifeline.SETTLE_SECONDS, lifeline.KILL_AFTER_SECONDS)
        self.write(keeper=self.me, phase="stopping", cause="owner_lost")
        # Publication is not exit: the keeper remains alive for another second.
        alive = [self.me]
        with patch.object(processes, "live_processes", side_effect=lambda rows: rows if alive else []):
            waited = self.wait(
                at=12,
                then=lambda: self.write(keeper=self.me, phase="stopped", cause="owner_lost"),
                events=[(13, alive.clear)],
            )
        self.assertGreaterEqual(waited, 13)
        self.assertLess(waited, lifeline.SETTLE_SECONDS)
        self.assertEqual("stopped", lifeline.settled(self.record))

    def test_a_live_keeper_that_never_exits_is_waited_for_only_the_bound(self):
        for phase, cause in (("stopping", "owner_lost"), ("stopped", "owner_lost"), ("discharged", "controller_finished")):
            with self.subTest(phase=phase):
                self.write(keeper=self.me, phase=phase, cause=cause)
                waited = self.wait()
                self.assertGreaterEqual(waited, lifeline.SETTLE_SECONDS)
                self.assertLess(waited, lifeline.SETTLE_SECONDS + 0.05)

    def test_a_keeper_that_died_unpublished_is_not_waited_for(self):
        for changes in ({}, {"keeper": {"pid": "x"}}, {"keeper": None}):
            with self.subTest(changes=changes):
                self.write(phase="armed", **changes)  # the default keeper is a process that does not exist
                self.assertEqual("running", lifeline.settled(self.record))
                self.assertFalse(lifeline.pending(self.record))
                self.assertEqual(0, self.wait(10))

    def test_authenticated_terminal_receipts_with_a_live_keeper_remain_pending(self):
        self.assertFalse(lifeline.pending(self.record))  # no receipt: the keeper never armed
        self.assertFalse(lifeline.pending(None))
        for phase, cause in (("armed", None), ("stopping", "owner_lost"), ("stopped", "owner_lost"), ("discharged", "controller_finished")):
            with self.subTest(phase=phase):
                self.write(keeper=self.me, phase=phase, cause=cause)
                self.assertTrue(lifeline.pending(self.record))
        for changes in (
            {"nonce": "b" * 32},
            {"owner": {**self.me, "birth_identity": 1.5}},
            {"phase": "discharged", "cause": "owner_lost"},
            {"cleanup_error": "ProcessError"},
            {"phase": "uncertain", "cause": "keeper_failure"},
            {"keeper": None},
        ):
            with self.subTest(changes=changes):
                self.write(**{"keeper": self.me, "phase": "stopped", "cause": "owner_lost", **changes})
                self.assertFalse(lifeline.pending(self.record))

    def test_a_reused_keeper_pid_after_terminal_publication_is_not_waited_for(self):
        reused = {**self.me, "birth_identity": self.me["birth_identity"] + 1, "state": "sleeping"}
        for phase, cause in (("stopped", "owner_lost"), ("discharged", "controller_finished")):
            with self.subTest(phase=phase):
                self.write(keeper=self.me, phase=phase, cause=cause)
                with patch.object(processes, "process_table", return_value={self.me["pid"]: reused}):
                    self.assertFalse(lifeline.pending(self.record))
                    self.assertEqual(0, self.wait(10))

    def test_a_zombie_keeper_after_terminal_publication_is_not_waited_for(self):
        zombie = {**self.me, "state": "Z"}
        for phase, cause in (("stopped", "owner_lost"), ("discharged", "controller_finished")):
            with self.subTest(phase=phase):
                self.write(keeper=self.me, phase=phase, cause=cause)
                with patch.object(processes, "process_table", return_value={self.me["pid"]: zombie}):
                    self.assertFalse(lifeline.pending(self.record))
                    self.assertEqual(0, self.wait(10))

    def test_keeper_inspection_failure_after_terminal_publication_remains_an_error(self):
        for phase, cause in (("stopped", "owner_lost"), ("discharged", "controller_finished")):
            with self.subTest(phase=phase):
                self.write(keeper=self.me, phase=phase, cause=cause)
                with (
                    patch.object(processes, "process_table", side_effect=processes.ProcessError("inspection denied")),
                    self.assertRaisesRegex(processes.ProcessError, "inspection denied"),
                ):
                    self.wait(10)


# The worker's main() with a stage that fails on its own, and the keeper's SIGTERM arriving as the pause is written.
FAILING_WORKER_DRIVER = """\
import os, signal, sys
sys.path.insert(0, {tools!r})
import autocode_builder_worker as worker

write_json = worker.runner.write_json
signalled = []


def failing_stage(*args):
    raise ValueError("the Builder stage failed")


def write_json_after_a_sigterm(path, value):
    if not signalled:
        signalled.append(path)
        os.kill(os.getpid(), signal.SIGTERM)
    write_json(path, value)


worker.execute = failing_stage
worker.runner.write_json = write_json_after_a_sigterm
sys.exit(worker.main(sys.argv[1]))
"""


class WorkerEntryTests(unittest.TestCase):
    """Only the lifeline's own failures are reported as lifeline errors; the worker's keep their traceback."""

    script = Path(__file__).resolve().parents[1] / "tools" / "autocode_builder_worker.py"

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def run_worker(self, *argv, pass_fds=()):
        child = subprocess.Popen(
            [sys.executable, str(self.script), *argv],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            pass_fds=pass_fds,
        )
        for fd in pass_fds:
            os.close(fd)
        self.addCleanup(lambda: child.poll() is None and child.kill())
        stdout, stderr = child.communicate(timeout=60)
        return child.returncode, stdout + stderr

    def test_a_refused_lifeline_is_named_and_does_no_work(self):
        code, output = self.run_worker(str(self.root), "start", supervision_cli.FLAG, "1")
        self.assertEqual(2, code, output)
        self.assertIn("Builder lifeline: Owner lifeline must be an inherited pipe descriptor", output)
        self.assertNotIn("Traceback", output)
        self.assertEqual([], list(self.root.iterdir()))

    def test_the_workers_own_failure_keeps_its_traceback_under_a_sound_lifeline(self):
        record, read_fd, write_fd = lifeline.prepare(self.root)
        try:
            # No state.json: main() fails on its own, inside a lifeline that was armed and then discharged.
            code, output = self.run_worker(
                str(self.root / "missing"), "start", *lifeline.argv(read_fd), pass_fds=(read_fd,)
            )
        finally:
            os.close(write_fd)
        self.assertEqual(1, code, output)
        self.assertIn("Traceback", output)
        self.assertIn("FileNotFoundError: [Errno 2] No such file or directory: '" + str(self.root / "missing"), output)
        self.assertNotIn("Builder lifeline", output)
        self.assertEqual("discharged", lifeline.settled(record), lifeline.read(record))

    def test_a_sigterm_while_a_failed_builders_pause_is_written_does_not_cost_it_the_pause(self):
        # The keeper's owner-loss SIGTERM is not an interrupt the failed stage saw: the handler had been
        # released with the stage, SIGTERM was at its default, and the worker died with no result (#454).
        workspace = self.root / "workspace"
        directory = workspace / "run"
        directory.mkdir(parents=True)
        (directory / "state.json").write_text(json.dumps({"workspace": str(workspace)}))
        driver = self.root / "driver.py"
        driver.write_text(FAILING_WORKER_DRIVER.format(tools=str(self.script.parent)))
        child = subprocess.run(
            [sys.executable, str(driver), str(directory)], capture_output=True, text=True, timeout=120
        )
        output = child.stdout + child.stderr
        self.assertEqual(2, child.returncode, output)
        self.assertEqual(
            {"status": "PAUSED_ORCHESTRATOR_WORKER", "reason": "the Builder stage failed"},
            json.loads((directory / "result.json").read_text()),
            output,
        )
        self.assertEqual("PAUSED_ORCHESTRATOR_WORKER", json.loads((directory / "state.json").read_text())["status"])


if __name__ == "__main__":
    unittest.main()
