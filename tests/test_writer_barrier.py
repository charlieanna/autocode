"""Actual local roots for the pre-exec transport; no model/native authority claims."""

import fcntl
import hashlib
import json
import os
import resource
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_native_locators as locators
import autocode_process as processes
import autocode_writer_barrier as barrier
from autocode_activity import ActivityMonitor


class WriterBarrierTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.marker = self.root / "target-result.json"
        self.environment = {
            "PATH": "/usr/bin:/bin",
            "LC_CTYPE": "C",
            "AUTOCODE_BARRIER_TEST_VALUE": "exact-original-environment",
        }
        script = (
            "import hashlib,json,os,pathlib,sys; "
            'value=os.environ.get("AUTOCODE_BARRIER_TEST_VALUE",""); '
            "pathlib.Path(sys.argv[1]).write_text(json.dumps("
            '{"value_sha256":hashlib.sha256(value.encode()).hexdigest(),'
            '"lc_ctype":os.environ.get("LC_CTYPE"),"arguments":sys.argv[2:]}))'
        )
        self.command = [sys.executable, "-I", "-S", "-B", "-c", script, str(self.marker), "", "literal argument"]

    def start(self, gate, *, pass_fds=()):
        argv, options = gate.launch(
            {
                "env": dict(self.environment),
                "start_new_session": True,
                "pass_fds": pass_fds,
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
        )
        child = subprocess.Popen(argv, **options)
        gate.spawned(child)
        return child

    def wait(self, child, *, on_root=None):
        rows = []
        code, expired = processes.wait_for_stage(child, 3, lambda value: rows.extend(value), on_root=on_root)
        self.assertFalse(expired)
        self.assertTrue(any(row.get("pid") == child.pid and "birth_identity" in row for row in rows))
        return code

    def test_target_waits_for_actual_root_and_durable_seal_before_execute(self):
        seal_path = self.root / "controlled-seal.json"
        # Compare the target's own startup effects against the exact ordinary
        # execution, rather than guessing how its Python coerces LC_CTYPE.
        baseline_marker = self.root / "ordinary-target.json"
        baseline_command = list(self.command)
        baseline_command[6] = str(baseline_marker)
        baseline_child = subprocess.Popen(
            baseline_command,
            env=self.environment,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.assertEqual(0, self.wait(baseline_child))
        baseline = json.loads(baseline_marker.read_text())
        with barrier.WriterBarrier(self.command, self.environment, payload_directory=self.root) as gate:
            child = self.start(gate)

            def seal(actual, tree, facts):
                self.assertIs(actual, child)
                self.assertFalse(self.marker.exists())
                self.assertEqual(child.pid, tree.known[child.pid]["pid"])
                self.assertEqual(gate.command_sha256, facts["target_command_sha256"])
                with seal_path.open("x") as output:
                    json.dump({"root": tree.known[child.pid], "transport": facts}, output)
                    output.flush()
                    os.fsync(output.fileno())

            self.assertEqual(
                0,
                self.wait(
                    child, on_root=lambda tree, release_token: gate.release_after_seal(child, tree, seal, release_token)
                ),
            )
            self.assertTrue(seal_path.is_file())
            result = json.loads(self.marker.read_text())
            self.assertEqual(
                hashlib.sha256(self.environment["AUTOCODE_BARRIER_TEST_VALUE"].encode()).hexdigest(),
                result["value_sha256"],
            )
            self.assertEqual(baseline, result)
            self.assertEqual(["", "literal argument"], result["arguments"])
        # This is a controlled fsync callback, not original native admission.

    def test_parent_abort_eof_never_executes_target(self):
        gate = barrier.WriterBarrier(self.command, self.environment, payload_directory=self.root)
        child = self.start(gate)
        gate.close()
        self.assertEqual(126, self.wait(child))
        self.assertFalse(self.marker.exists())

    def test_large_owned_inventory_uses_small_exact_child_locators_after_seal(self):
        descriptor = {
            "manifest": str(self.root / "isolation.json"),
            "manifest_sha256": "a" * 64,
            "files": {"/owned/sdk/" + "x" * 240 + str(i): "b" * 64 for i in range(3660)},
        }
        boundary = {"tools": ["bash"], "allow_write": False, "native_role_isolation": descriptor}
        duplicated = {
            **self.environment,
            "AUTOCODE_NATIVE_ROLE_ISOLATION": json.dumps(descriptor),
            "AUTOCODE_TOOL_CONTAINMENT": json.dumps(boundary),
        }
        with self.assertRaisesRegex(barrier.BarrierError, "payload is unsupported"):
            barrier.WriterBarrier(self.command, duplicated, payload_directory=self.root)
        self.environment.update(
            AUTOCODE_NATIVE_ROLE_ISOLATION=json.dumps(locators.descriptor_value(descriptor)),
            AUTOCODE_TOOL_CONTAINMENT=json.dumps(locators.boundary_value(boundary)),
        )
        self.command = [
            sys.executable,
            "-I",
            "-S",
            "-B",
            "-c",
            "import json,os,pathlib,sys; pathlib.Path(sys.argv[1]).write_text(json.dumps("
            "{k:json.loads(os.environ[k]) for k in "
            "['AUTOCODE_NATIVE_ROLE_ISOLATION','AUTOCODE_TOOL_CONTAINMENT']}))",
            str(self.marker),
        ]
        with barrier.WriterBarrier(self.command, self.environment, payload_directory=self.root) as gate:
            self.assertLess(gate.facts()["payload_identity"]["bytes"], 8192)
            child = self.start(gate)

            def seal(*_):
                self.assertFalse(self.marker.exists())

            self.assertEqual(
                0, self.wait(child, on_root=lambda tree, token: gate.release_after_seal(child, tree, seal, token))
            )
        received = json.loads(self.marker.read_bytes())
        self.assertEqual(locators.descriptor_value(descriptor), received["AUTOCODE_NATIVE_ROLE_ISOLATION"])
        self.assertTrue(locators.boundary_matches(received["AUTOCODE_TOOL_CONTAINMENT"], boundary))
        self.assertEqual(3660, len(descriptor["files"]))

    def test_failed_seal_is_cleaned_by_original_supervisor_without_target(self):
        with barrier.WriterBarrier(self.command, self.environment, payload_directory=self.root) as gate:
            child = self.start(gate)

            def refused(*_):
                raise barrier.BarrierError("controlled original admission failure")

            with self.assertRaisesRegex(barrier.BarrierError, "original admission failure"):
                processes.wait_for_stage(
                    child,
                    3,
                    lambda _: None,
                    on_root=lambda tree, release_token: gate.release_after_seal(child, tree, refused, release_token),
                )
            self.assertIsNotNone(child.poll())
            self.assertFalse(self.marker.exists())

    def test_malformed_release_cannot_become_target_execution(self):
        with barrier.WriterBarrier(self.command, self.environment, payload_directory=self.root) as gate:
            child = self.start(gate)
            os.write(gate._write_fd, b'{"version":1,"nonce":"foreign","payload_sha256":"foreign"}')
            gate.close()
            self.assertEqual(126, self.wait(child))
            self.assertFalse(self.marker.exists())

    def test_release_is_single_use_and_does_not_launch_another_target(self):
        with barrier.WriterBarrier(self.command, self.environment, payload_directory=self.root) as gate:
            child = self.start(gate)
            captured = []

            def release(tree, release_token):
                captured.append((tree, release_token))
                gate.release_after_seal(child, tree, lambda *_: None, release_token)

            self.assertEqual(0, self.wait(child, on_root=release))
            before = self.marker.read_bytes()
            with self.assertRaisesRegex(barrier.BarrierError, "already consumed"):
                gate.release_after_seal(child, captured[0][0], lambda *_: None, captured[0][1])
            self.assertEqual(before, self.marker.read_bytes())

    def test_environment_or_preexec_or_release_fd_drift_refused_before_launch(self):
        with barrier.WriterBarrier(self.command, self.environment, payload_directory=self.root) as gate:
            original = {
                "env": self.environment,
                "start_new_session": True,
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            for change in (
                {"env": {**self.environment, "AUTOCODE_BARRIER_TEST_VALUE": "changed"}},
                {"preexec_fn": lambda: None},
                {"executable": "/bin/sh"},
                {"shell": True},
                {"pass_fds": (gate._write_fd,)},
            ):
                with self.subTest(change=tuple(change)), self.assertRaises(barrier.BarrierError):
                    gate.launch({**original, **change})
            self.assertFalse(self.marker.exists())

    def assert_checkout_descriptor_survives(self, inherited):
        # Shared local target fixture, not another test invocation. Inventory
        # includes every live descriptor rather than an assumed numeric bound.
        command = [
            sys.executable,
            "-I",
            "-S",
            "-B",
            "-c",
            "import json,os,pathlib,sys\n"
            "fd=int(sys.argv[2]); own=os.fstat(fd); opened=[]\n"
            'for candidate in [int(name) for name in os.listdir("/dev/fd") if name.isdecimal() and int(name)>=3]:\n'
            " try: os.fstat(candidate); opened.append(candidate)\n"
            " except OSError: pass\n"
            'pathlib.Path(sys.argv[1]).write_text(json.dumps({"inode":own.st_ino,"fds":opened}))',
            str(self.marker),
            str(inherited),
        ]
        with barrier.WriterBarrier(command, self.environment, payload_directory=self.root) as gate:
            child = self.start(gate, pass_fds=(inherited,))
            self.assertEqual(
                0,
                self.wait(
                    child,
                    on_root=lambda tree, release_token: gate.release_after_seal(
                        child, tree, lambda *_: None, release_token
                    ),
                ),
            )
        result = json.loads(self.marker.read_text())
        self.assertEqual(os.fstat(inherited).st_ino, result["inode"])
        self.assertEqual([inherited], result["fds"])

    def test_original_checkout_fd_survives_but_control_payload_fds_close_before_exec(self):
        inherited = os.open(self.root / "checkout-lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            self.assert_checkout_descriptor_survives(inherited)
        finally:
            os.close(inherited)

    def test_high_checkout_fd_survives_without_inheriting_barrier_control_aliases(self):
        soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
        if soft != resource.RLIM_INFINITY and soft <= 256:
            self.skipTest("Host descriptor limit cannot allocate FD >=256; high-FD qualification remains owed")
        original = os.open(self.root / "checkout-lock", os.O_RDWR | os.O_CREAT, 0o600)
        inherited = None
        try:
            inherited = fcntl.fcntl(original, fcntl.F_DUPFD_CLOEXEC, 256)
            self.assertGreaterEqual(inherited, 256)
            self.assert_checkout_descriptor_survives(inherited)
        finally:
            if inherited is not None:
                os.close(inherited)
            os.close(original)

    def test_original_supervisor_without_callback_keeps_existing_target_behavior(self):
        child = subprocess.Popen(
            self.command,
            env=self.environment,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.assertEqual(0, self.wait(child))
        self.assertTrue(self.marker.is_file())

    def test_descriptor_inode_aliases_in_stdio_or_pass_fds_refuse_before_launch(self):
        with barrier.WriterBarrier(self.command, self.environment, payload_directory=self.root) as gate:
            options = {
                "env": self.environment,
                "start_new_session": True,
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            for original in (gate._read_fd, gate._write_fd, gate._payload_fd):
                alias = os.dup(original)
                try:
                    for field in ("stdin", "stdout", "stderr", "pass_fds"):
                        value = (alias,) if field == "pass_fds" else alias
                        with (
                            self.subTest(field=field, original=original),
                            self.assertRaisesRegex(barrier.BarrierError, "cannot alias"),
                        ):
                            gate.launch({**options, field: value})
                    stream = os.fdopen(os.dup(alias), "rb", closefd=True)
                    try:
                        with self.assertRaisesRegex(barrier.BarrierError, "cannot alias"):
                            gate.launch({**options, "stdin": stream})
                    finally:
                        stream.close()
                finally:
                    os.close(alias)
            self.assertFalse(self.marker.exists())

    def test_original_deadline_expiry_refuses_release_after_seal_without_target(self):
        with barrier.WriterBarrier(self.command, self.environment, payload_directory=self.root) as gate:
            child = self.start(gate)
            sealed = []

            def release(tree, release_token):
                # Advance only the supervised clock at this deterministic
                # boundary. No sleep, shortened cap or second watchdog.
                later = time.monotonic() + 10
                with patch.object(processes.time, "monotonic", return_value=later):
                    gate.release_after_seal(child, tree, lambda *_: sealed.append(True), release_token)

            with self.assertRaisesRegex(processes.ProcessError, "expired before release"):
                processes.wait_for_stage(child, 3, lambda _: None, on_root=release)
            self.assertEqual([True], sealed)
            self.assertIsNotNone(child.poll())
            self.assertFalse(self.marker.exists())

    def assert_blocked_root_stopped(self, child, rows):
        self.assertIsNotNone(child.poll())
        self.assertFalse(self.marker.exists())
        captured = [row for row in rows if row.get("pid") == child.pid]
        self.assertTrue(captured)
        self.assertTrue(all(row.get("birth_identity", 0) > 0 for row in captured))

    def test_original_stage_deadline_crossed_inside_durable_seal_refuses_target(self):
        # Keep the existing 3-second public stage bound. Advancing the fake
        # monotonic clock INSIDE seal distinguishes a fresh release check from
        # an incorrect check made only before the durable transaction.
        clock, rows = [0.0], []
        seal_path = self.root / "controlled-stage-deadline-seal.json"
        with barrier.WriterBarrier(self.command, self.environment, payload_directory=self.root) as gate:
            child = self.start(gate)

            def seal(actual, tree, facts):
                self.assertIs(actual, child)
                self.assertFalse(self.marker.exists())
                with seal_path.open("x") as output:
                    json.dump({"root": tree.known[child.pid], "transport": facts}, output)
                    output.flush()
                    os.fsync(output.fileno())
                    clock[0] = 10.0

            with patch.object(processes.time, "monotonic", side_effect=lambda: clock[0]):
                with self.assertRaisesRegex(processes.ProcessError, "deadline expired before release"):
                    processes.wait_for_stage(
                        child,
                        3,
                        lambda value: rows.extend(value),
                        on_root=lambda tree, release_token: gate.release_after_seal(child, tree, seal, release_token),
                    )
            self.assertEqual(child.pid, json.loads(seal_path.read_text())["root"]["pid"])
            self.assert_blocked_root_stopped(child, rows)

    def test_original_idle_release_guard_honors_expiry_and_startup_grace_inside_seal(self):
        # A thread-aware controlled clock isolates the fresh CALLER release
        # guard: background supervisor reads remain at the initial instant.
        # The next test separately drives the ORIGINAL concurrent watchdog.
        # No timer/thread/closure is replaced and no runtime cap is enlarged.
        controller = threading.get_ident()
        for elapsed, allowed in ((2.0, False), (0.75, True)):
            with self.subTest(elapsed=elapsed, allowed=allowed):
                clock, rows, sealed = [0.0], [], []
                events = self.root / ("controlled-idle-events-" + str(elapsed) + ".jsonl")
                events.touch()

                def monotonic():
                    return clock[0] if threading.get_ident() == controller else 0.0

                monitor = ActivityMonitor(events, idle_seconds=0.5, tool_seconds=0, clock=monotonic)
                with barrier.WriterBarrier(self.command, self.environment, payload_directory=self.root) as gate:
                    child = self.start(gate)

                    def seal(actual, tree, facts):
                        self.assertFalse(self.marker.exists())
                        sealed.append(tree.known[actual.pid]["birth_identity"])
                        clock[0] = elapsed

                    with patch.object(processes.time, "monotonic", side_effect=monotonic):
                        if allowed:
                            code, expired = processes.wait_for_stage(
                                child,
                                3,
                                lambda value: rows.extend(value),
                                activity=monitor,
                                startup_grace=1,
                                on_root=lambda tree, release_token: gate.release_after_seal(
                                    child, tree, seal, release_token
                                ),
                            )
                            self.assertEqual(0, code)
                            self.assertFalse(expired)
                            self.assertIsNone(getattr(monitor, "timeout", None))
                            self.assertTrue(self.marker.is_file())
                        else:
                            with self.assertRaisesRegex(processes.ProcessError, "deadline expired before release"):
                                processes.wait_for_stage(
                                    child,
                                    3,
                                    lambda value: rows.extend(value),
                                    activity=monitor,
                                    startup_grace=1,
                                    on_root=lambda tree, release_token: gate.release_after_seal(
                                        child, tree, seal, release_token
                                    ),
                                )
                            self.assertEqual("idle", monitor.timeout["kind"])
                            self.assert_blocked_root_stopped(child, rows)
                    self.assertEqual(1, len(sealed))
                    self.assertGreater(sealed[0], 0)

    def test_original_watchdog_already_fired_during_seal_refuses_target_release(self):
        clock, rows, sealed = [0.0], [], []
        events = self.root / "controlled-already-fired-events.jsonl"
        events.touch()
        monitor = ActivityMonitor(events, idle_seconds=3, tool_seconds=0, clock=lambda: clock[0])
        with barrier.WriterBarrier(self.command, self.environment, payload_directory=self.root) as gate:
            child = self.start(gate)

            def seal(actual, tree, facts):
                self.assertIs(actual, child)
                self.assertFalse(self.marker.exists())
                sealed.append(tree.known[child.pid]["birth_identity"])
                clock[0] = 6.0
                # Observe the ORIGINAL activity supervisor stop this owned
                # blocked child before attempting release. The bounded kernel
                # wait is a handshake, not a sleep or replacement watchdog.
                self.assertLess(child.wait(timeout=3), 0)
                self.assertEqual("idle", monitor.timeout["kind"])

            with patch.object(processes.time, "monotonic", side_effect=lambda: clock[0]):
                with self.assertRaisesRegex(processes.ProcessError, "ended before release"):
                    processes.wait_for_stage(
                        child,
                        None,
                        lambda value: rows.extend(value),
                        activity=monitor,
                        on_root=lambda tree, release_token: gate.release_after_seal(child, tree, seal, release_token),
                    )
            self.assertEqual(1, len(sealed))
            self.assertGreater(sealed[0], 0)
            self.assert_blocked_root_stopped(child, rows)

    def test_owned_helper_sigterm_inside_seal_cleans_root_before_interrupt_propagates(self):
        # Only the owned isolated helper signals ITS OWN PID. The test runner,
        # controller and sibling workers never receive its SIGTERM.
        result_path = self.root / "owned-cancellation-helper.json"
        helper = """
import json,os,pathlib,signal,subprocess,sys
sys.path.insert(0,sys.argv[1])
import autocode_process as processes
import autocode_writer_barrier as barrier
root,marker,result=map(pathlib.Path,sys.argv[2:5])
environment={'PATH':'/usr/bin:/bin','LC_CTYPE':'C','AUTOCODE_BARRIER_TEST_VALUE':'exact-original-environment'}
command=[sys.executable,'-I','-S','-B','-c',
    'import pathlib,sys; pathlib.Path(sys.argv[1]).write_text("target executed")',str(marker)]
rows=[]
sealed=[]
interrupted=False
previous=signal.getsignal(signal.SIGTERM)
with barrier.WriterBarrier(command,environment,payload_directory=root) as gate:
    argv,options=gate.launch({'env':environment,'start_new_session':True,
        'stdin':subprocess.DEVNULL,'stdout':subprocess.DEVNULL,'stderr':subprocess.DEVNULL})
    child=subprocess.Popen(argv,**options)
    gate.spawned(child)
    def seal(actual,tree,facts):
        if actual is not child or marker.exists():
            raise AssertionError('Cancellation did not precede target release')
        sealed.append(tree.known[child.pid]['birth_identity'])
        os.kill(os.getpid(),signal.SIGTERM)
        raise AssertionError('Owned helper SIGTERM was not delivered as interruption')
    with processes.interruption_handler():
        try:
            processes.wait_for_stage(child,3,lambda value:rows.extend(value),
                on_root=lambda tree,release_token:gate.release_after_seal(child,tree,seal,release_token))
        except KeyboardInterrupt:
            interrupted=True
    code=child.poll()
    captured=[row for row in rows if row.get('pid')==child.pid]
restored=signal.getsignal(signal.SIGTERM)==previous
value={'helper_pid':os.getpid(),'child_pid':child.pid,'interrupted':interrupted,
    'child_code':code,'sealed':sealed,'captured':captured,'restored':restored,'target_exists':marker.exists()}
result.write_text(json.dumps(value))
ok=(interrupted and code is not None and code<0 and len(sealed)==1 and sealed[0]>0
    and captured and all(row.get('birth_identity',0)>0 for row in captured)
    and restored and not marker.exists())
raise SystemExit(0 if ok else 1)
"""
        child = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                helper,
                str(Path(barrier.__file__).resolve().parent),
                str(self.root),
                str(self.marker),
                str(result_path),
            ],
            env=self.environment,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        rows = []
        # Reuse the existing isolated-helper public 10-second safety bound.
        # The ORIGINAL supervisor owns the helper and any nested roots.
        code, expired = processes.wait_for_stage(child, 10, lambda value: rows.extend(value))
        self.assertEqual(0, code)
        self.assertFalse(expired)
        value = json.loads(result_path.read_text())
        self.assertEqual(child.pid, value["helper_pid"])
        self.assertNotEqual(os.getpid(), value["helper_pid"])
        self.assertNotEqual(value["helper_pid"], value["child_pid"])
        self.assertTrue(value["interrupted"])
        self.assertLess(value["child_code"], 0)
        self.assertTrue(value["restored"])
        self.assertFalse(value["target_exists"])
        self.assertEqual(1, len(value["sealed"]))
        self.assertGreater(value["sealed"][0], 0)
        self.assertTrue(value["captured"])
        self.assertTrue(all(row.get("birth_identity", 0) > 0 for row in value["captured"]))
        self.assertTrue(any(row.get("pid") == child.pid and row.get("birth_identity", 0) > 0 for row in rows))
        self.assertFalse(self.marker.exists())

    def test_closed_parent_standard_slots_use_private_fds_above_stdio(self):
        # Only this isolated helper closes its own 0/1/2. The test runner's and
        # agent's stdio are untouched. This drives transport, not a test suite.
        result_path = self.root / "closed-stdio-helper.json"
        helper = """
import hashlib,json,os,pathlib,subprocess,sys
sys.path.insert(0,sys.argv[1])
import autocode_process as processes
import autocode_writer_barrier as barrier
root,marker,result=map(pathlib.Path,sys.argv[2:5])
environment={'PATH':'/usr/bin:/bin','LC_CTYPE':'C','AUTOCODE_BARRIER_TEST_VALUE':'exact-original-environment'}
program=('import hashlib,json,os,pathlib,sys; '
         'pathlib.Path(sys.argv[1]).write_text(json.dumps({'
         '"value_sha256":hashlib.sha256(os.environ["AUTOCODE_BARRIER_TEST_VALUE"].encode()).hexdigest(),'
         '"arguments":sys.argv[2:]}))')
command=[sys.executable,'-I','-S','-B','-c',program,str(marker),'','literal argument']
for fd in (0,1,2):
    os.close(fd)
closed=[]
for fd in (0,1,2):
    try:
        os.fstat(fd)
    except OSError:
        closed.append(fd)
sealed=[]
receipts=[]
with barrier.WriterBarrier(command,environment,payload_directory=root) as gate:
    private=[gate._read_fd,gate._payload_fd,gate._write_fd]
    inheritable=[os.get_inheritable(fd) for fd in private]
    argv,options=gate.launch({'env':environment,'start_new_session':True,
        'stdin':subprocess.DEVNULL,'stdout':subprocess.DEVNULL,'stderr':subprocess.DEVNULL})
    child=subprocess.Popen(argv,**options)
    gate.spawned(child)
    def seal(actual,tree,facts):
        if marker.exists():
            raise AssertionError('Target executed before controlled root seal')
        sealed.append(tree.known[actual.pid]['pid'])
    code,expired=processes.wait_for_stage(child,3,lambda rows:receipts.extend(rows),
        on_root=lambda tree,release_token:gate.release_after_seal(child,tree,seal,release_token))
result.write_text(json.dumps({'closed':closed,'private':private,'inheritable':inheritable,
    'code':code,'expired':expired,'sealed':sealed,'captured':any(row['pid']==child.pid for row in receipts)}))
raise SystemExit(0 if code==0 and not expired else 1)
"""
        # The helper needs installed psutil; -I ignores unsafe environment/search
        # inputs but keeps the venv's site packages. The barrier bootstrap itself
        # still uses the unchanged -I/-S/-B argv and exact payload environment.
        child = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                helper,
                str(Path(barrier.__file__).resolve().parent),
                str(self.root),
                str(self.marker),
                str(result_path),
            ],
            env=self.environment,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Original supervisor owns the helper and any nested local roots if an
        # assertion fails. This is bounded local fixture composition, not a new
        # AutoCode controller, native provider, retry grant or production cap.
        code, expired = processes.wait_for_stage(child, 10, lambda _: None)
        self.assertEqual(0, code)
        self.assertFalse(expired)
        result = json.loads(result_path.read_text())
        self.assertEqual([0, 1, 2], result["closed"])
        self.assertTrue(all(fd >= 3 for fd in result["private"]))
        self.assertEqual([False, False, False], result["inheritable"])
        self.assertEqual(0, result["code"])
        self.assertFalse(result["expired"])
        self.assertEqual(1, len(result["sealed"]))
        self.assertTrue(result["captured"])
        target = json.loads(self.marker.read_text())
        self.assertEqual(
            hashlib.sha256(self.environment["AUTOCODE_BARRIER_TEST_VALUE"].encode()).hexdigest(), target["value_sha256"]
        )
        self.assertEqual(["", "literal argument"], target["arguments"])


if __name__ == "__main__":
    unittest.main()
