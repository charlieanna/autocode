"""Ordinary readonly Kilo startup checks overlap without accepting partial proof."""

import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import autocode_captured_process as captured_process
import autocode_tool_containment as containment
import autocode_toolchain as toolchain


class ParallelToolchainTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = Path(directory.name).resolve()
        core_state = patch.dict(os.environ, {"XDG_STATE_HOME": str(self.workspace / "core-state")})
        core_state.start()
        self.addCleanup(core_state.stop)
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
        self.setup_calls, self.selected_calls = [], []
        self.kernel_finished = False

    def response(self, command, *, exit_code=0):
        return {"input": {"command": command}, "result": {"metadata": {"exit": exit_code}, "output": command}}

    def configure(self, observe, *, transport="kilocode", allow_write=False):
        variable = "KILO_CONFIG_CONTENT" if transport == "kilocode" else "OPENCODE_CONFIG_CONTENT"
        environment = {
            "PATH": "/bin",
            variable: json.dumps({"agent": {"stage": {"permission": {"bash": "allow"}}}}),
        }

        def native(argv, **kwargs):
            selected = "--params" in argv and json.loads(argv[-1])["command"] in self.probes
            (self.selected_calls if selected else self.setup_calls).append((argv, kwargs))
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
                    (self.scratch / "conformance-capture").touch()
                    response = self.response(command)
                    self.kernel_finished = True
            return subprocess.CompletedProcess(argv, 0, json.dumps(response), "")

        with (
            patch.object(containment, "backend", return_value=containment.BUBBLEWRAP),
            patch.object(containment.shutil, "which", return_value="/owned/provider"),
            patch.object(containment.subprocess, "run", side_effect=AssertionError("metadata bypassed owned capture")),
            patch.object(captured_process, "run_metadata", side_effect=native),
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
        self.assertEqual([15, 60, 60, 60], [kw["timeout"] for _, kw in self.setup_calls])
        self.assertEqual(3, len(self.selected_calls))
        environments = []
        for argv, kwargs in self.selected_calls:
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
                self.selected_calls.clear()
                observed = []

                def observe(command):
                    observed.append(command)
                    return self.response(command)

                with patch.object(containment, "ThreadPoolExecutor") as workers:
                    self.configure(observe, transport="opencode", allow_write=writable)
                workers.assert_not_called()
                self.assertEqual(self.probes, observed)
                self.assertEqual(3, len(self.selected_calls))

    def test_empty_selection_retains_the_kernel_proof_without_dispatching_workers(self):
        self.probes = []
        with patch.object(containment, "ThreadPoolExecutor") as workers:
            self.configure(lambda command: self.fail(command))
        workers.assert_not_called()
        self.assertEqual([], self.selected_calls)
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
                self.assertEqual([], self.selected_calls)
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
            patch.object(containment.subprocess, "run", side_effect=AssertionError("metadata bypassed owned capture")),
            patch.object(captured_process, "run_metadata", return_value=result) as captured,
        ):
            self.assertEqual({}, serial())
            self.assertEqual({}, owned())
        serial_call, snapshot_call = captured.call_args_list
        self.assertEqual("changed", serial_call.kwargs["env"]["CONFIG"])
        self.assertEqual("initial", snapshot_call.kwargs["env"]["CONFIG"])
        self.assertEqual(prefix[:-1], snapshot_call.args[0])
        self.assertEqual(prefix, serial_call.args[0])


@unittest.skipUnless(sys.platform == "linux", "owned descendant cleanup requires supported Linux")
class ParallelOwnedProcessTests(unittest.TestCase):
    def test_timeout_settles_each_detached_tree_before_parallel_failure_returns(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            probes = [f"selected-{index}" for index in range(3)]
            script = root / "probe.py"
            worker = (
                "import os,sys; os.write(int(sys.argv[1]),b'R'); os.close(int(sys.argv[1])); "
                "reader,writer=os.pipe(); os.read(reader,1)"
            )
            script.write_text(
                "import json,os,pathlib,subprocess,sys\n"
                "params=json.loads(sys.argv[-1])\n"
                "reader,writer=os.pipe()\n"
                f"worker=subprocess.Popen([sys.executable,'-c',{worker!r},str(writer)],"
                "pass_fds=(writer,),start_new_session=True,stdin=subprocess.DEVNULL,"
                "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
                "os.close(writer); assert os.read(reader,1)==b'R'; os.close(reader)\n"
                "def identity(pid):\n"
                " row=pathlib.Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()\n"
                " return {'pid':pid,'birth':row[19],'parent':int(row[1]),'session':int(row[3])}\n"
                "ready=pathlib.Path(os.environ['PROBE_ROOT'],params['command']+'.json')\n"
                "temporary=ready.with_suffix('.tmp')\n"
                "temporary.write_text(json.dumps({'root':identity(os.getpid()),'worker':identity(worker.pid)}))\n"
                "temporary.replace(ready)\n"
                "reader,writer=os.pipe(); os.read(reader,1)\n"
            )
            real_capture = captured_process.run_metadata
            lock = threading.Lock()
            stop, all_ready = threading.Event(), threading.Event()
            families, started, settled = {}, {}, {}
            observer_errors = []

            def owned(identity):
                path = Path(f"/proc/{identity['pid']}/stat")
                before = path.read_text().rsplit(")", 1)[1].split()
                if before[19] != identity["birth"]:
                    raise AssertionError("Fixture PID changed before pidfd capture")
                descriptor = os.pidfd_open(identity["pid"])
                try:
                    after = path.read_text().rsplit(")", 1)[1].split()
                    if after[19] != identity["birth"]:
                        raise AssertionError("Fixture PID changed during pidfd capture")
                except BaseException:
                    os.close(descriptor)
                    raise
                return descriptor

            def observe_overlap():
                try:
                    until = time.monotonic() + 12
                    while not stop.is_set():
                        for probe in probes:
                            path = root / (probe + ".json")
                            if probe in families or not path.exists():
                                continue
                            row = json.loads(path.read_text())
                            descriptors = []
                            try:
                                for name in ("root", "worker"):
                                    descriptors.append(owned(row[name]))
                                if (
                                    row["root"]["session"] != row["root"]["pid"]
                                    or row["worker"]["session"] != row["worker"]["pid"]
                                    or row["worker"]["parent"] != row["root"]["pid"]
                                ):
                                    raise AssertionError("Fixture did not establish an independent detached family")
                            except BaseException:
                                for descriptor in descriptors:
                                    os.close(descriptor)
                                raise
                            with lock:
                                families[probe] = (row, descriptors)
                        if len(families) == 3:
                            with lock:
                                descriptors = [descriptor for _, handles in families.values() for descriptor in handles]
                                if len(started) != 3 or select.select(descriptors, [], [], 0)[0]:
                                    raise AssertionError("Three fixture families were not simultaneously alive")
                                observed = time.monotonic()
                                if observed >= min(started.values()) + 2:
                                    raise AssertionError(
                                        "Three families were not ready before the first capture deadline"
                                    )
                            all_ready.set()
                            return
                        if time.monotonic() >= until:
                            raise AssertionError("Parallel fixture readiness exceeded its bound")
                        stop.wait(0.005)
                except BaseException as error:
                    observer_errors.append(error)

            def bounded_capture(argv, *, timeout, **kwargs):
                self.assertEqual(60, timeout)
                probe = json.loads(argv[-1])["command"]
                with lock:
                    started[probe] = time.monotonic()
                expired = False
                try:
                    return real_capture(argv, timeout=2, **kwargs)
                except subprocess.TimeoutExpired:
                    expired = True
                    raise
                finally:
                    with lock:
                        handles = families.get(probe, ({}, []))[1]
                        settled[probe] = {
                            "expired": expired,
                            "family_exited": len(handles) == 2
                            and all(select.select([descriptor], [], [], 0)[0] for descriptor in handles),
                        }

            sentinel = None
            observer = threading.Thread(target=observe_overlap, daemon=True)
            observer.start()
            try:
                sentinel = subprocess.Popen(
                    [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
                )
                debug = containment._native_debug(
                    [sys.executable, str(script)], root, {**os.environ, "PROBE_ROOT": str(root)}, supervised=True
                )
                with (
                    patch.dict(os.environ, {"XDG_STATE_HOME": str(root / "core-state")}),
                    patch.object(captured_process, "run_metadata", side_effect=bounded_capture),
                    self.assertRaises(subprocess.TimeoutExpired),
                ):
                    containment._parallel_toolchain_checks(debug, root, probes)
                observer.join(timeout=2)
                self.assertEqual([], observer_errors)
                self.assertTrue(all_ready.is_set(), "all three real process families must overlap before any timeout")
                self.assertEqual(set(probes), set(started))
                self.assertEqual(set(probes), set(settled), "every dispatched public capture must settle")
                self.assertTrue(all(row["expired"] and row["family_exited"] for row in settled.values()))
                owned_sets = [{identity["pid"] for identity in row.values()} for row, _ in families.values()]
                self.assertTrue(all(len(pids) == 2 for pids in owned_sets))
                self.assertEqual(6, len(set.union(*owned_sets)), "parallel families must remain independent")
                descriptors = [descriptor for _, handles in families.values() for descriptor in handles]
                self.assertEqual(descriptors, select.select(descriptors, [], [], 0)[0])
                self.assertIsNone(sentinel.poll(), "parallel cleanup touched an unrelated process")
            finally:
                stop.set()
                observer.join(timeout=2)
                cleanup_errors = ["Fixture observer did not stop"] if observer.is_alive() else []
                for _, descriptors in families.values():
                    for descriptor in descriptors:
                        try:
                            if not select.select([descriptor], [], [], 0)[0]:
                                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                                if not select.select([descriptor], [], [], 5)[0]:
                                    cleanup_errors.append("Owned fixture process did not exit")
                        except OSError as error:
                            cleanup_errors.append(str(error))
                        finally:
                            os.close(descriptor)
                if sentinel is not None:
                    sentinel.stdin.close()
                    sentinel.wait(timeout=5)
                self.assertEqual([], cleanup_errors)
