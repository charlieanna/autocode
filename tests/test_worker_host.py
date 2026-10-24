"""Real persistent Linux services, controlling PTYs and loopback SSH; offline providers.

The dedicated worker-host CI job prepares its disposable user manager/SSH host.
Routine unit tests cannot infer this capability from mocks or a Linux label.
"""

import json
import os
import select
import shlex
import signal
import subprocess
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if sys.platform == "linux":
    import fcntl
    import pty
    import termios

import autocode_client_actions as actions
import autocode_taskrun as taskrun
import autocode_worker_service as services

from tests.test_agent_step import fixture_workspace
from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS

ROOT = Path(__file__).resolve().parents[1]
CORE = [sys.executable, str(ROOT / "tools" / "autocode.py")]


@unittest.skipUnless(
    os.environ.get("AUTOCODE_REQUIRE_WORKER_HOST") == "1", "requires the dedicated persistent Linux/SSH host job"
)
class PersistentHost(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        capability = services.UserServices().capability()
        if not capability["available"]:
            raise AssertionError(f"Required host was not prepared: {capability}")

    def setUp(self):
        self.workspace, fixture = fixture_workspace(self)
        self.env = {
            **os.environ,
            **fixture,
            "TERM": "xterm-256color",
            "AUTOCODE_WORKER_HOME": str(self.workspace.parent / "launches"),
        }
        self.receipts = []
        self.gates = {}
        self.evidence = []
        self.addCleanup(self.cleanup_workers)

    def cleanup_workers(self):
        for receipt in self.receipts:
            subprocess.run(
                ["systemctl", "--user", "kill", "--signal=KILL", services.unit_name(receipt["launch_id"])],
                env=self.env,
                capture_output=True,
                timeout=5,
            )
            subprocess.run(
                ["systemctl", "--user", "stop", services.unit_name(receipt["launch_id"])],
                env=self.env,
                capture_output=True,
                timeout=15,
            )
        destination = os.environ.get("AUTOCODE_WORKER_EVIDENCE")
        if destination:
            folder = Path(destination)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / (self._testMethodName + ".json")).write_text(json.dumps(self.evidence, indent=2))
            logs = Path(self.env["AUTOCODE_WORKER_HOME"])
            for path in logs.glob("*.log"):
                (folder / (self._testMethodName + "-" + path.name)).write_bytes(path.read_bytes()[-20000:])

    def hold(self, stage):
        ready, gate = self.workspace.parent / (stage + "-ready"), self.workspace.parent / (stage + "-gate")
        os.mkfifo(gate)
        self.gates[stage] = (ready, gate)
        provider = Path(self.env["PATH"].split(os.pathsep)[0]) / "codex"
        marker = '    task = data.get("current_task") or {}'
        source = provider.read_text()
        self.assertIn(marker, source)
        inserted = (
            f"    if stage == {stage!r} and not Path({str(ready)!r}).exists():\n"
            f"        Path({str(ready)!r}).touch()\n"
            f"        with open({str(gate)!r}) as fixture_gate:\n"
            "            fixture_gate.read()\n"
        )
        provider.write_text(source.replace(marker, inserted + marker, 1))

    def release(self, stage):
        descriptor = os.open(self.gates[stage][1], os.O_WRONLY | os.O_NONBLOCK)
        try:
            os.write(descriptor, b"continue")
        finally:
            os.close(descriptor)

    def start(self):
        destination = self.workspace / ".autocode" / "runs" / "durable-host"
        self.run, receipt = taskrun.TaskRun.start_durable(
            self.workspace,
            destination,
            BRIEF,
            command=CORE,
            env=self.env,
            options=(*FIXTURE_OPTIONS, "--max-parallel-builders", "1"),
        )
        self.receipts.append(receipt)
        self.bound(receipt)

    def bound(self, receipt):
        deadline = time.monotonic() + 30
        while True:
            observed = self.run.inspect_launch(receipt)
            if observed.get("ready"):
                return observed
            self.assertLess(time.monotonic(), deadline, observed)
            time.sleep(0.05)

    def wait(self, predicate, *, seconds=120):
        deadline = time.monotonic() + seconds
        while True:
            view = self.run.status()
            if predicate(view):
                self.evidence.append({"sampled": time.monotonic(), "view": view})
                return view
            self.assertLess(time.monotonic(), deadline, view)
            time.sleep(0.05)

    def dispatch(self, view, command=None):
        receipt = self.run.dispatch_durable(
            command or actions.named(view, self.run.workspace, self.run.run_dir), view=view
        )
        self.receipts.append(receipt)
        return self.bound(receipt)

    def plan(self):
        for _ in range(8):
            view = self.wait(lambda value: not actions.working(value))
            need = view["needs"]
            if need["kind"] == "approve_plan":
                return view
            command = actions.named(view, self.run.workspace, self.run.run_dir)
            if need["kind"] == "answer":
                decisions = actions.decisions(view, command)
                self.assertTrue(decisions)
                self.assertTrue(all(row.key.startswith("requirements-review-") for row in decisions), need)
                command = actions.fill(view, command, {row.key: "plan" for row in decisions})
            else:
                self.assertEqual("continue", need["kind"], need)
            self.dispatch(view, command)
        self.fail("Offline fixture never reached plan approval")

    def at_stage(self, stage):
        return self.wait(
            lambda view: (
                self.gates[stage][0].exists()
                and any(
                    row.get("active") and row.get("stage") == stage
                    for row in view.get("usage", {}).get("accounting", {}).get("attempts", [])
                )
            )
        )

    @staticmethod
    def attempts(view):
        return [
            (row.get("stage"), row.get("events"), row.get("output"))
            for row in view.get("usage", {}).get("accounting", {}).get("attempts", [])
        ]

    def viewer(self, *, ssh=False, fresh=False):
        remote_env = {
            key: self.env[key]
            for key in (
                "PATH",
                "AUTOCODE_HOME",
                "XDG_CONFIG_HOME",
                "CODEX_HOME",
                "PYTHONDONTWRITEBYTECODE",
                "TERM",
                "AUTOCODE_WORKER_HOME",
                "XDG_RUNTIME_DIR",
                "DBUS_SESSION_BUS_ADDRESS",
            )
            if key in self.env
        }
        argv = (
            [
                *CORE,
                "chat",
                "--execution",
                "durable_local",
                "--workspace",
                str(self.workspace),
                BRIEF,
                "--",
                *FIXTURE_OPTIONS,
            ]
            if fresh
            else [*CORE, "chat", "--workspace", str(self.run.workspace), "--run-dir", str(self.run.run_dir)]
        )
        if ssh:
            argv = [
                "ssh",
                "-F",
                os.environ["AUTOCODE_WORKER_SSH_CONFIG"],
                "-tt",
                "fixture",
                shlex.join(["env", *(f"{key}={value}" for key, value in remote_env.items()), *argv]),
            ]
        leader, follower = pty.openpty()

        def terminal():
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        child = subprocess.Popen(
            argv,
            stdin=follower,
            stdout=follower,
            stderr=follower,
            env=self.env,
            cwd=self.workspace,
            preexec_fn=terminal,
        )
        os.close(follower)
        self.addCleanup(lambda: child.poll() is None and child.kill())
        screen, deadline = bytearray(), time.monotonic() + 30
        while b"Builder" not in screen and b"Tester" not in screen and b"Requirements" not in screen:
            self.assertIsNone(child.poll(), bytes(screen))
            self.assertLess(time.monotonic(), deadline, bytes(screen))
            if select.select([leader], [], [], 0.1)[0]:
                screen.extend(os.read(leader, 65536))
        return child, leader

    def leave(self, kind):
        child, leader = self.viewer(ssh=kind == "ssh_disconnect")
        started = time.monotonic()
        try:
            if kind == "quit":
                os.write(leader, b"/quit\r")
            elif kind == "ctrl_d":
                os.write(leader, b"\x04")
            elif kind in ("sigkill", "ssh_disconnect"):
                child.kill()
            elif kind == "sighup":
                child.send_signal(signal.SIGHUP)
            elif kind == "pty_close":
                os.close(leader)
                leader = None
            child.wait(timeout=5)
            self.evidence.append(
                {"viewer_event": kind, "exit": child.returncode, "response_seconds": time.monotonic() - started}
            )
        finally:
            if leader is not None:
                os.close(leader)

    def test_builder_and_slow_tester_survive_each_viewer_loss_then_complete_once(self):
        for stage in ("terra", "sol", "astra_checkpoint"):
            self.hold(stage)
        self.start()
        approved = self.plan()
        # Two viewers using the same exact displayed token converge on one launch.
        command = actions.named(approved, self.run.workspace, self.run.run_dir)
        with ThreadPoolExecutor(2) as pool:
            pending = [pool.submit(self.run.dispatch_durable, command, view=approved) for _ in range(2)]
            accepted = []
            for result in pending:
                try:
                    accepted.append(result.result())
                except taskrun.TaskRunError as error:
                    self.assertIn("run lock", str(error))
        self.assertTrue(accepted)
        self.assertEqual(1, len({receipt["launch_id"] for receipt in accepted}))
        self.receipts.extend(accepted)
        self.bound(accepted[0])
        before = self.at_stage("terra")
        identity = self.attempts(before)
        epoch = before["execution"]["controller_epoch"]
        for kind in ("quit", "ctrl_d", "sighup", "sigkill", "pty_close", "ssh_disconnect"):
            with self.subTest(event=kind):
                self.leave(kind)
                after = self.run.status()
                self.assertTrue(actions.working(after))
                self.assertEqual(identity, self.attempts(after))
                self.assertEqual(epoch, after["execution"]["controller_epoch"])
        self.release("terra")
        tested = self.wait(
            lambda view: (
                any(self.gates[stage][0].exists() for stage in ("sol", "astra_checkpoint")) and actions.working(view)
            )
        )
        edited = {name: (self.workspace / name).read_bytes() for name in ("greet.py", "test_greet.py", "README.md")}
        self.leave("ctrl_d")
        self.assertEqual(self.attempts(tested), self.attempts(self.run.status()))
        self.assertEqual(edited, {name: (self.workspace / name).read_bytes() for name in edited})
        for stage in ("sol", "astra_checkpoint"):
            if self.gates[stage][0].exists():
                self.release(stage)
        done = self.wait(lambda view: not actions.working(view))
        # Some unit boundaries are ready rather than terminal: only take a current continue offer.
        for _ in range(8):
            if done.get("done"):
                break
            self.assertEqual("continue", done["needs"]["kind"], done)
            self.dispatch(done)
            done = self.wait(lambda view: not actions.working(view))
        self.assertTrue(done["done"], done)
        self.assertTrue(done["delivery"]["verified_complete"], done.get("delivery"))
        self.assertEqual(1, sum(stage == "terra" for stage, _, _ in self.attempts(done)))

    def test_worker_loss_retains_the_accepted_attempt_and_read_only_reconnect_does_not_replay(self):
        self.hold("requirements_gather")
        self.start()
        before = self.at_stage("requirements_gather")
        service = services.UserServices(env=self.env).inspect(before["execution"]["launch_id"])
        self.assertTrue(service["checked"])
        self.assertGreater(service["pid"], 1)
        os.kill(service["pid"], signal.SIGKILL)
        after = self.wait(lambda view: (view.get("controller") or {}).get("kind") != "running")
        self.assertFalse(after["done"])
        self.assertEqual(self.attempts(before), self.attempts(after))
        for _ in range(3):
            self.assertEqual(self.attempts(before), self.attempts(self.run.status()))
        self.assertNotEqual("TASK_COMPLETE", after["status"])

    def test_fresh_full_screen_launch_detaches_and_is_found_through_public_listings(self):
        self.hold("requirements_gather")
        child, leader = self.viewer(fresh=True)
        try:
            reply = subprocess.run(
                [*CORE, "runs", "--workspace", str(self.workspace), "--json"],
                env=self.env,
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            rows = json.loads(reply.stdout)["runs"]
            self.assertEqual(1, len(rows))
            self.run = taskrun.TaskRun(
                self.workspace, Path(rows[0]["run_dir"]), command=tuple(CORE), env=self.env, timeout=10
            )
            listed = subprocess.run(
                [*CORE, "worker", "launches", "--workspace", str(self.workspace)],
                env=self.env,
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            self.receipts.extend(json.loads(listed.stdout)["launches"])
            before = self.at_stage("requirements_gather")
            os.write(leader, b"/quit\r")
            child.wait(timeout=5)
            self.assertEqual(2, child.returncode)
            after = self.run.status()
            self.assertTrue(actions.working(after))
            self.assertEqual(self.attempts(before), self.attempts(after))
            self.release("requirements_gather")
            waiting = self.wait(lambda view: not actions.working(view))
            self.assertFalse(waiting["done"])
        finally:
            os.close(leader)

    def test_worker_loss_after_edits_keeps_source_routes_and_unfinished_verification(self):
        for stage in ("sol", "astra_checkpoint"):
            self.hold(stage)
        self.start()
        self.dispatch(self.plan())
        before = self.wait(
            lambda view: any(self.gates[stage][0].exists() for stage in self.gates) and actions.working(view)
        )
        files = {name: (self.workspace / name).read_bytes() for name in ("greet.py", "test_greet.py", "README.md")}
        observed = services.UserServices(env=self.env).inspect(before["execution"]["launch_id"])
        self.assertTrue(observed["checked"])
        self.assertGreater(observed["pid"], 1)
        os.kill(observed["pid"], signal.SIGKILL)
        after = self.wait(lambda view: (view.get("controller") or {}).get("kind") != "running")
        self.assertFalse(after["done"])
        self.assertIsNone(after.get("delivery"))
        self.assertEqual(before["routes"], after["routes"])
        self.assertEqual(self.attempts(before), self.attempts(after))
        self.assertEqual(files, {name: (self.workspace / name).read_bytes() for name in files})

    def test_repeated_ctrl_c_only_requests_a_boundary_pause(self):
        self.hold("requirements_gather")
        self.start()
        before = self.at_stage("requirements_gather")
        child, leader = self.viewer()
        try:
            os.write(leader, b"\x03")
            self.wait(lambda view: (view.get("pause") or {}).get("requested") is True)
            os.write(leader, b"\x03\x03")
            after = self.run.status()
            self.assertIsNone(child.poll())
            self.assertTrue(actions.working(after))
            self.assertEqual(self.attempts(before), self.attempts(after))
            self.release("requirements_gather")
            paused = self.wait(lambda view: not actions.working(view))
            self.assertFalse(paused["done"])
            self.assertNotEqual("RUNNING", paused["status"])
            os.write(leader, b"\x04")
            child.wait(timeout=5)
        finally:
            os.close(leader)
