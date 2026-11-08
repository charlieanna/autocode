"""Ordinary readonly Kilo startup checks overlap without accepting partial proof."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import autocode_captured_process as captured_process
import autocode_process as processes
import autocode_tool_containment as containment
import autocode_toolchain as toolchain


class ParallelToolchainTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = Path(directory.name).resolve()
        self.control = self.workspace / ".autocode" / ("tool-containment-" + "a" * 32)
        self.scratch = self.control / "scratch"
        self.scratch.mkdir(parents=True)
        for name in ("shell", "policy.bwrap"):
            (self.control / name).write_text("runner-owned fixture\n")
        self.boundary = {
            "profile": str(self.control / "policy.bwrap"),
            "shell": str(self.control / "shell"),
            "scratch": str(self.scratch),
        }
        self.probes = [f"selected-{index}" for index in range(3)]
        self.agent = {
            "permission": [{"permission": "bash", "pattern": "*", "action": "allow"}],
            "tools": {"bash": True, "read": False},
        }
        self.serial_calls, self.owned_calls = [], []
        self.kernel_finished = False

    def response(self, command, *, exit_code=0):
        return {"input": {"command": command}, "result": {"metadata": {"exit": exit_code}, "output": command}}

    def configure(self, observe, *, transport="kilocode", allow_write=False):
        variable = "KILO_CONFIG_CONTENT" if transport == "kilocode" else "OPENCODE_CONFIG_CONTENT"
        environment = {
            "PATH": "/bin",
            variable: json.dumps({"agent": {"stage": {"permission": {"bash": "allow"}}}}),
        }

        def native(argv, *, owned=False, **kwargs):
            (self.owned_calls if owned else self.serial_calls).append((argv, kwargs))
            if argv[-1] == "--version":
                version = "7.8.3" if transport == "kilocode" else containment.SUPPORTED_VERSION
                return subprocess.CompletedProcess(argv, 0, version, "")
            response = self.agent
            if "--params" in argv:
                command = json.loads(argv[-1])["command"]
                if command in self.probes:
                    self.assertTrue(self.kernel_finished)
                    response = observe(command)
                    if isinstance(response, subprocess.CompletedProcess):
                        return response
                else:
                    self.assertFalse(owned, "kernel safety probes must keep their serial path")
                    (self.scratch / "conformance-capture").touch()
                    response = self.response(command)
                    self.kernel_finished = True
            return subprocess.CompletedProcess(argv, 0, json.dumps(response), "")

        with (
            patch.object(containment, "backend", return_value=containment.BUBBLEWRAP),
            patch.object(containment.shutil, "which", return_value="/owned/provider"),
            patch.object(containment.subprocess, "run", side_effect=native),
            patch.object(captured_process, "run", side_effect=lambda *a, **kw: native(*a, owned=True, **kw)),
            patch.object(containment, "prepare", return_value=self.boundary),
            patch.object(toolchain, "discover", return_value={"read_roots": [], "probes": self.probes}),
            patch.object(containment, "verify"),
            patch.object(containment, "_retain", wraps=containment._retain) as retain,
        ):
            try:
                return containment.configure(
                    [transport, "run", "--agent", "stage", "--model", "selected/model"],
                    environment,
                    self.workspace,
                    allow_write=allow_write,
                    request={},
                    transport=transport,
                )
            finally:
                self.retained = retain.call_count

    def test_selected_checks_overlap_and_receipts_keep_configured_order(self):
        together = threading.Barrier(3, timeout=5)
        last_finished = threading.Event()

        def observe(command):
            together.wait()
            if command == self.probes[-1]:
                last_finished.set()
            else:
                self.assertTrue(last_finished.wait(5))
            return self.response(command)

        _, boundary = self.configure(observe)
        proof = json.loads(Path(boundary["conformance"]).read_text())
        self.assertEqual(
            [self.response(command)["result"]["output"] for command in self.probes],
            [row["output"] for row in proof["toolchain_checks"]],
        )
        self.assertEqual(self.probes, [row["command"] for row in proof["toolchain_checks"]])
        self.assertEqual(1, self.retained)
        self.assertEqual([15, 60, 60, 60], [kw["timeout"] for _, kw in self.serial_calls])
        self.assertEqual(3, len(self.owned_calls))
        environments = []
        for argv, kwargs in self.owned_calls:
            self.assertEqual({"cwd", "env", "timeout"}, set(kwargs))
            self.assertEqual(60, kwargs["timeout"])
            self.assertEqual(10000, json.loads(argv[-1])["timeout"])
            environments.append(kwargs["env"])
        self.assertTrue(all(env == environments[0] for env in environments))
        self.assertNotIn("AUTOCODE_TOOL_CONTAINMENT", environments[0])

    def test_no_more_than_three_checks_run_at_once_and_all_are_checked(self):
        self.probes = [f"selected-{index}" for index in range(8)]
        first_wave = threading.Barrier(4, timeout=5)
        release = threading.Event()
        lock = threading.Lock()
        active = maximum = 0
        completed = []

        def observe(command):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            try:
                if command in self.probes[:3]:
                    first_wave.wait()
                    self.assertTrue(release.wait(5))
                return self.response(command)
            finally:
                with lock:
                    active -= 1
                    completed.append(command)

        with ThreadPoolExecutor(max_workers=1) as caller:
            future = caller.submit(self.configure, observe)
            try:
                first_wave.wait()
                self.assertFalse(future.done())
                self.assertEqual(3, active)
            finally:
                release.set()
            future.result(timeout=10)
        self.assertEqual(3, maximum)
        self.assertCountEqual(self.probes, completed)

    def test_failure_priority_is_input_order_and_every_dispatched_worker_settles(self):
        together = threading.Barrier(3, timeout=5)
        later_failed, release_first, release_tail = (threading.Event() for _ in range(3))
        tail_finished = threading.Event()

        def observe(command):
            together.wait()
            if command == self.probes[1]:
                later_failed.set()
                raise RuntimeError("failure-index-1")
            if command == self.probes[0]:
                self.assertTrue(release_first.wait(5))
                raise RuntimeError("failure-index-0")
            self.assertTrue(release_tail.wait(5))
            tail_finished.set()
            return self.response(command)

        with ThreadPoolExecutor(max_workers=1) as caller:
            future = caller.submit(self.configure, observe)
            try:
                self.assertTrue(later_failed.wait(5))
                self.assertFalse(future.done())
                release_first.set()
                self.assertFalse(tail_finished.is_set())
                self.assertFalse(future.done())
            finally:
                release_first.set()
                release_tail.set()
            with self.assertRaisesRegex(RuntimeError, "failure-index-0"):
                future.result(timeout=10)
        self.assertTrue(tail_finished.is_set())
        self.assertEqual(0, self.retained)
        self.assertFalse((self.control / "conformance.json").exists())

    def test_every_failure_blocks_receipt_after_all_checks_settle(self):
        denial = "The user has specified a rule which prevents you from using this specific tool call"
        cases = (
            (lambda: subprocess.CompletedProcess([], 0, "not JSON", ""), RuntimeError, "auditable JSON"),
            (
                lambda: subprocess.CompletedProcess([], 1, "", denial + " private-secret"),
                containment.NativePermissionDenied,
                "Native tool permissions deny",
            ),
            (lambda: subprocess.CompletedProcess([], 2, "", "private-secret"), RuntimeError, "exit 2"),
            (lambda: self.response(self.probes[0], exit_code=True), RuntimeError, "Selected toolchain failed"),
            (lambda: self.response("wrong-command"), RuntimeError, "Selected toolchain failed"),
            (lambda: subprocess.TimeoutExpired("probe", 60), subprocess.TimeoutExpired, "60 seconds"),
            (lambda: KeyboardInterrupt("probe interrupted"), KeyboardInterrupt, "probe interrupted"),
        )
        for produce, exception, message in cases:
            with self.subTest(message=message):
                together = threading.Barrier(3, timeout=5)
                finished = []

                def observe(command):
                    try:
                        together.wait()
                        if command == self.probes[0]:
                            value = produce()
                            if isinstance(value, BaseException):
                                raise value
                            return value
                        return self.response(command)
                    finally:
                        finished.append(command)

                with self.assertRaisesRegex(exception, message) as raised:
                    self.configure(observe)
                self.assertNotIn("private-secret", str(raised.exception))
                self.assertCountEqual(self.probes, finished)
                self.assertEqual(0, self.retained)
                self.assertFalse((self.control / "conformance.json").exists())

    def test_opencode_readonly_and_writable_checks_keep_the_serial_path(self):
        for writable in (False, True):
            with self.subTest(writable=writable):
                self.owned_calls.clear()
                observed = []

                def observe(command):
                    observed.append(command)
                    return self.response(command)

                self.configure(observe, transport="opencode", allow_write=writable)
                self.assertEqual(self.probes, observed)
                self.assertEqual([], self.owned_calls)

    def test_empty_selection_retains_the_kernel_proof_without_dispatching_workers(self):
        self.probes = []
        with patch.object(containment, "ThreadPoolExecutor") as workers:
            self.configure(lambda command: self.fail(command))
        workers.assert_not_called()
        self.assertEqual([], self.owned_calls)
        self.assertEqual(1, self.retained)

    def test_final_native_policy_and_kernel_failures_precede_parallel_dispatch(self):
        for agent in (
            {"permission": [], "tools": {"bash": True}},
            {"permission": [{"permission": "bash", "pattern": "*", "action": "ask"}], "tools": {"bash": True}},
            {
                "permission": [{"permission": "bash", "pattern": "*", "action": "allow"}],
                "tools": {"bash": True, "read": True},
            },
        ):
            with self.subTest(agent=agent):
                self.agent = agent
                with self.assertRaises((RuntimeError, ValueError)):
                    self.configure(lambda command: self.fail(command))
                self.assertEqual([], self.owned_calls)
                self.assertEqual(0, self.retained)

    def test_supervised_debug_snapshots_configuration_but_serial_setup_stays_live(self):
        child = {"CONFIG": "initial"}
        prefix = ["/provider", "debug", "agent", "selected"]
        serial = containment._native_debug(prefix, self.workspace, child)
        owned = containment._native_debug(prefix, self.workspace, child, supervised=True)
        child["CONFIG"] = "changed"
        prefix.append("changed")
        result = subprocess.CompletedProcess([], 0, "{}", "")
        with (
            patch.object(containment.subprocess, "run", return_value=result) as raw,
            patch.object(captured_process, "run", return_value=result) as captured,
        ):
            self.assertEqual({}, serial())
            self.assertEqual({}, owned())
        self.assertEqual("changed", raw.call_args.kwargs["env"]["CONFIG"])
        self.assertEqual("initial", captured.call_args.kwargs["env"]["CONFIG"])
        self.assertEqual(prefix[:-1], captured.call_args.args[0])
        self.assertEqual(prefix, raw.call_args.args[0])


@unittest.skipUnless(sys.platform == "linux", "owned descendant cleanup requires supported Linux")
class ParallelOwnedProcessTests(unittest.TestCase):
    def test_timeout_settles_each_detached_tree_before_parallel_failure_returns(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ready = root / "ready"
            os.mkfifo(ready)
            descriptor = os.open(ready, os.O_RDWR)
            self.addCleanup(os.close, descriptor)
            script = root / "probe.py"
            script.write_text(
                "import json, os, pathlib, subprocess, sys\n"
                "params = json.loads(sys.argv[-1])\n"
                "worker = subprocess.Popen([sys.executable, '-c', "
                "'import os; reader, writer = os.pipe(); os.read(reader, 1)'], start_new_session=True)\n"
                "pathlib.Path(os.environ['PROBE_ROOT'], params['command']).write_text(str(worker.pid))\n"
                "with open(os.environ['READY_FIFO'], 'w') as ready: ready.write('R')\n"
                "reader, writer = os.pipe(); os.read(reader, 1)\n"
            )
            real_wait = captured_process.supervisor.wait
            receipts = []
            together = threading.Barrier(3, timeout=10)

            def expire_after_ready(child, timeout):
                self.assertEqual(60, timeout)
                self.assertEqual(b"R", os.read(descriptor, 1))
                together.wait()  # All three private groups and detached workers are live.
                code, expired, receipt = real_wait(child, 0)
                receipts.append(receipt)
                return code, expired, receipt

            sentinel = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)
            try:
                debug = containment._native_debug(
                    [sys.executable, str(script)],
                    root,
                    {**os.environ, "PROBE_ROOT": str(root), "READY_FIFO": str(ready)},
                    supervised=True,
                )
                with (
                    patch.object(captured_process.supervisor, "wait", expire_after_ready),
                    self.assertRaises(subprocess.TimeoutExpired),
                ):
                    containment._parallel_toolchain_checks(debug, root, ["selected-0", "selected-1", "selected-2"])
                self.assertEqual(3, len(receipts))
                owned = [identity for receipt in receipts for identity in receipt["owned"]]
                self.assertEqual([], processes.live_processes(owned))
                owned_sets = [{identity["pid"] for identity in receipt["owned"]} for receipt in receipts]
                self.assertTrue(all(len(pids) == 2 for pids in owned_sets))
                self.assertEqual(
                    6, len(set.union(*owned_sets)), "parallel families must not claim each other's children"
                )
                workers = {int((root / f"selected-{index}").read_text()) for index in range(3)}
                self.assertTrue(workers.issubset({identity["pid"] for identity in owned}))
                self.assertIsNone(sentinel.poll())
            finally:
                sentinel.stdin.close()
                sentinel.wait(timeout=5)
