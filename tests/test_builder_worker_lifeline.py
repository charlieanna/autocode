"""A parallel Builder never outlives the Orchestrator that launched it (#454).

Public CLI, real workers, keepers and Git; only the provider is scripted. M1's
provider is held until a release file appears, so the controller is killed or
interrupted at an event barrier, never after a sleep.
"""

import contextlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_dispatch as dispatch
import autocode_process as processes
import autocode_worker_lifeline as lifeline

from . import test_build_blackbox as bb

psutil = processes.psutil


def alive(process):
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def descendants(child):
    """psutil handles for a live, unreaped child's whole tree: its PID cannot have been reused."""
    try:
        return psutil.Process(child.pid).children(recursive=True) if child.poll() is None else []
    except psutil.Error:
        return []


def stop(owned):
    """Kill and wait, so nothing still writes into the test's directory once it is removed."""
    for process in owned:
        try:
            process.resume()
            process.kill()  # psutil checks birth identity before signalling
        except psutil.Error:
            pass
    psutil.wait_procs(owned, timeout=10)


def describe(process):
    try:
        return f"pid={process.pid} ppid={process.ppid()} status={process.status()} cmdline={process.cmdline()}"
    except psutil.Error as error:
        return f"pid={process.pid} inspect failed: {error}"


class BuilderWorkerLifelineTests(unittest.TestCase):
    command = bb.BuildBlackbox.command
    invoke = bb.BuildBlackbox.invoke
    seed = bb.BuildBlackbox.seed
    state = bb.BuildBlackbox.state
    events = bb.BuildBlackbox.events
    build = bb.BuildBlackbox.build
    candidate = bb.BuildBlackbox.candidate

    def setUp(self):
        bb.BuildBlackbox.setUp(self)
        self.owned = []  # psutil handles keep birth identity: cleanup never signals a reused PID
        self.addCleanup(self.stop_owned)

    def stop_owned(self):
        (self.root / "release").touch()
        stop(list(reversed(self.owned)))

    def until(self, found, message, seconds=30):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = found()
            if value:
                return value
            time.sleep(0.02)
        self.fail(message)

    def worker(self, milestone):
        return next(row for row in self.state()["orchestration_batch"]["workers"] if row["milestone_id"] == milestone)

    def result(self, milestone):
        path = Path(self.worker(milestone)["run_dir"]) / "result.json"
        return json.loads(path.read_text()) if path.is_file() else None

    def receipt(self, row):
        path = Path(row["supervision"]["receipt"])
        return json.loads(path.read_text()) if path.is_file() else None

    def starts(self):
        counts = {}
        for event in self.events():
            counts[event["milestone"]] = counts.get(event["milestone"], 0) + 1
        return counts

    def start_controller(self):
        controller = subprocess.Popen(
            self.command("autocode_build", ["--run-dir", str(self.run), "--no-chat"]),
            cwd=self.root,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        self.owned.append(psutil.Process(controller.pid))

        def collect():
            # Taken before the kill: a test that fails before its barriers has recorded nothing else.
            tree = descendants(controller)
            if controller.poll() is None:
                controller.kill()
            stop(tree)
            stdout, stderr = controller.communicate(timeout=10)
            (self.root / "controller.log").write_text(stdout + stderr)

        self.addCleanup(collect)
        return controller

    def hold_builder(self):
        """M1's provider is held and retained by its worker's keeper; M2 and M3 are built."""
        self.seed()
        self.env["BUILD_AUDIT_FAULT"] = "hold"
        sentinel = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"], start_new_session=True)

        def stop_sentinel():
            sentinel.kill()
            sentinel.wait(timeout=10)

        self.addCleanup(stop_sentinel)
        controller = self.start_controller()
        provider = self.until(
            lambda: next((e for e in self.events() if e["milestone"] == "M1"), None), "M1 provider never started"
        )
        self.until(
            lambda: all((self.result(m) or {}).get("status") == "BUILT" for m in ("M2", "M3")),
            "M2 and M3 did not build",
        )
        row = self.worker("M1")
        if row.get("supervision"):
            self.until(
                lambda: any(p["pid"] == provider["pid"] for p in (self.receipt(row) or {}).get("processes", [])),
                "M1's keeper never retained its provider",
            )
        tree = psutil.Process(controller.pid).children(recursive=True)
        self.owned.extend(tree)
        return controller, sentinel, row, tree

    def survivors(self, tree, seconds=12):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and any(alive(p) for p in tree):
            time.sleep(0.05)
        return [describe(p) for p in tree if alive(p)]

    def assert_stopped_with_owner(self, kill):
        controller, sentinel, row, tree = self.hold_builder()
        kill(controller)
        controller.communicate(timeout=10)
        survivors = self.survivors(tree)
        if not row.get("supervision"):
            self.assertTrue(survivors, "Unchanged source did not reproduce the orphaned Builder")
            self.fail("Builder processes outlived their killed controller: " + "; ".join(survivors))
        self.assertFalse(survivors, "Builder processes outlived their killed controller: " + "; ".join(survivors))
        receipt = self.receipt(row)
        self.assertEqual(
            ("stopped", "owner_lost", None), (receipt["phase"], receipt["cause"], receipt["cleanup_error"]), receipt
        )
        # The keeper interrupted the worker first, so it saved its own pause before anything was killed.
        self.assertEqual("PAUSED_INTERRUPTED", (self.result("M1") or {}).get("status"), self.result("M1"))
        self.assertIsNone(sentinel.poll(), "Cleanup touched an unrelated process")
        # The stopped Builder's partial attempt is neither adopted nor replayed without an explicit retry.
        self.build(2, extra=["--resume-paused"])
        self.assertEqual({"M1": 1, "M2": 1, "M3": 1}, self.starts())
        self.assertNotIn("autocode", self.state().get("unit_handoffs", {}))
        (self.root / "release").touch()
        self.build(extra=["--resume-paused", "--retry-builder", "M1"])
        self.candidate()
        self.assertEqual({"M1": 2, "M2": 1, "M3": 1}, self.starts())

    def test_controller_sigkill_stops_held_builder_and_provider(self):
        self.assert_stopped_with_owner(lambda controller: controller.kill())

    def test_controller_group_sigkill_stops_held_builder_and_provider(self):
        # The workers lead their own sessions, so the group kill reaches only the controller.
        self.assert_stopped_with_owner(lambda controller: os.killpg(controller.pid, signal.SIGKILL))

    def test_controller_sigterm_lets_builders_save_their_pause(self):
        controller, _, row, tree = self.hold_builder()
        controller.send_signal(signal.SIGTERM)
        stdout, stderr = controller.communicate(timeout=30)
        self.assertEqual(2, controller.returncode, stdout + stderr)
        self.assertIn("PAUSED_INTERRUPTED", stderr)
        self.assertEqual([], self.survivors(tree, seconds=5))
        self.assertEqual("PAUSED_INTERRUPTED", self.result("M1")["status"], self.result("M1"))
        child = json.loads((Path(row["run_dir"]) / "state.json").read_text())
        stage = json.loads(Path(child["active_stage"]["supervision"]["receipt"]).read_text())
        self.assertEqual(("stopped", None), (stage["phase"], stage["cleanup_error"]), stage)
        (self.root / "release").touch()
        self.build(extra=["--resume-paused", "--retry-builder", "M1"])
        self.candidate()
        self.assertEqual({"M1": 2, "M2": 1, "M3": 1}, self.starts())

    def test_normal_build_discharges_every_worker_lifeline(self):
        self.seed()
        self.build()
        self.candidate()
        rows = self.state()["orchestration_history"][0]["workers"]
        self.assertEqual(3, len(rows))
        for row in rows:
            with self.subTest(milestone=row["milestone_id"]):
                self.assertIn("supervision", row, "Builder launched without a lifeline")
                receipt = self.receipt(row)
                self.assertEqual(
                    ("discharged", "controller_finished", None),
                    (receipt["phase"], receipt["cause"], receipt["cleanup_error"]),
                    receipt,
                )
                self.assertEqual([], processes.live_processes([receipt["keeper"], *receipt["processes"]]))


# A Builder worker reduced to what the Orchestrator can see: it guards itself with the real lifeline
# and, once interrupted, spends longer on its teardown (a stage's cleanup under load) than the
# Orchestrator's SIGTERM grace before it saves its pause. A later interrupt is absorbed, as
# processes.interruption_handler does for the real worker.
SLOW_TEARDOWN = 14

STAND_IN_WORKER = """\
import json, os, signal, sys, time
sys.path.insert(0, {tools!r})
import autocode_supervision_cli as supervision_cli

directory = sys.argv[1]
log = open(os.path.join(directory, "trace.log"), "a", buffering=1)
interrupted = []


def on_term(signum, frame):
    if interrupted:
        return
    interrupted.append(signum)
    time.sleep({teardown})
    with open(os.path.join(directory, "result.json"), "w") as result:
        json.dump({{"status": "PAUSED_INTERRUPTED"}}, result)
    raise SystemExit(2)


with supervision_cli.guard(sys.argv[1:]) as argv:
    signal.signal(signal.SIGTERM, on_term)
    log.write("guarded\\n")
    while True:
        signal.pause()
"""


class OrchestratorInterruptTests(unittest.TestCase):
    """The Orchestrator's own interrupt lets a worker use the kill window it declared (#454).

    Real run_workers, lifeline and keeper; only the worker is a stand-in. The Orchestrator is interrupted
    right after launch, as a SIGTERM raises KeyboardInterrupt inside run_workers.
    """

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="orchestrator-interrupt-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.directory = self.root / "worker"
        self.directory.mkdir()
        self.script = self.root / "stand_in_worker.py"
        self.script.write_text(
            STAND_IN_WORKER.format(tools=str(Path(dispatch.__file__).resolve().parent), teardown=SLOW_TEARDOWN)
        )
        self.launched = []
        self.popen = subprocess.Popen  # dispatch.subprocess is this module: patching it replaces this one too
        self.addCleanup(self.stop_launched)

    def stop_launched(self):
        """Nothing outlives the test: a regressed Orchestrator can leave the worker or its keeper running."""
        for child in self.launched:
            owned = descendants(child)
            with contextlib.suppress(OSError, psutil.Error):
                owned.append(psutil.Process(child.pid))
            for path in self.directory.glob("lifeline/*-supervision.json"):
                receipt = json.loads(path.read_text())
                for row in processes.live_processes([receipt["keeper"], receipt["provider"], *receipt["processes"]]):
                    with contextlib.suppress(psutil.Error):
                        owned.append(psutil.Process(row["pid"]))
            stop(owned)
            child.wait(timeout=10)

    def launch(self, args, **kwargs):
        args = list(args)
        args[1] = str(self.script)  # the worker script, always argv[1]
        child = self.popen(args, **kwargs)
        self.launched.append(child)
        return child

    def interrupted_run_workers(self):
        state = {"status": "RUNNING"}
        batch = {
            "workers": [
                {
                    "status": "PENDING",
                    "run_dir": str(self.directory),
                    "workspace": str(self.directory),
                    "milestone_id": "M1",
                }
            ]
        }
        trace = self.directory / "trace.log"

        def guarded_then_interrupt(*args, **kwargs):
            # The worker has armed its keeper: the interrupt meets a guarded, working Builder.
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and not (trace.exists() and "guarded" in trace.read_text()):
                time.sleep(0.05)
            raise KeyboardInterrupt("SIGTERM")

        with (
            patch.object(dispatch.s, "assert_no_legacy_process"),
            patch.object(dispatch.autocode_status, "persist"),
            patch.object(dispatch.interaction_timing, "mark", guarded_then_interrupt),
            patch.object(dispatch.subprocess, "Popen", self.launch),
        ):
            with self.assertRaises(dispatch.s.Paused) as raised:
                dispatch.run_workers(state, self.root, batch)
        self.assertEqual("PAUSED_INTERRUPTED", raised.exception.status)
        return batch["workers"][0]

    def test_a_worker_whose_teardown_outlasts_the_sigterm_grace_still_saves_its_pause(self):
        row = self.interrupted_run_workers()
        result = self.directory / "result.json"
        self.assertTrue(result.is_file(), "The worker was stopped before it saved its pause")
        self.assertEqual("PAUSED_INTERRUPTED", json.loads(result.read_text())["status"])
        receipt = json.loads(Path(row["supervision"]["receipt"]).read_text())
        # Its keeper finished the cleanup it began when the Orchestrator's pipe closed, and verified it.
        self.assertEqual(
            ("stopped", "owner_lost", None), (receipt["phase"], receipt["cause"], receipt["cleanup_error"]), receipt
        )
        self.assertIn(lifeline.settled(row["supervision"]), lifeline.ADOPTABLE)
        self.assertEqual([], processes.live_processes([receipt["keeper"], receipt["provider"]]))


if __name__ == "__main__":
    unittest.main()
