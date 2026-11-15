"""Captured commands finish only after their invocation's children are collected."""

import errno
import importlib.machinery
import json
import os
import platform
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from pathlib import Path
from unittest.mock import Mock, patch

import autocode_captured_process as captured

try:
    import autocode_capture_reaper as reaper
except ImportError:  # Public behavioral proof also runs unchanged on the base.
    reaper = None


class CapturedContractTests(unittest.TestCase):
    def test_missing_family_loader_refuses_before_module_execution(self):
        source = Path(reaper.__file__).read_bytes()
        for specification in (None, importlib.machinery.ModuleSpec("autocode_capture_family", None)):
            with self.subTest(specification=specification):
                with (
                    patch.object(reaper.importlib.util, "spec_from_file_location", return_value=specification),
                    patch.object(reaper.importlib.util, "module_from_spec") as load,
                    patch.object(captured.subprocess, "Popen") as launch,
                    self.assertRaisesRegex(ImportError, "Capture family module has no source loader"),
                ):
                    exec(compile(source, reaper.__file__, "exec"), {"__file__": reaper.__file__, "__name__": "fixture"})
                load.assert_not_called()
                launch.assert_not_called()

    def test_second_pipe_relocation_failure_closes_original_and_duplicate_descriptors(self):
        reader, writer = os.pipe()
        descriptors = [reader, writer]
        failure = OSError(errno.EMFILE, "forced second descriptor relocation failure")

        def relocate(descriptor):
            if descriptor == writer:
                raise failure
            duplicate = os.dup(descriptor)
            descriptors.append(duplicate)
            os.close(descriptor)
            return duplicate

        with tempfile.TemporaryFile("w+t") as output, tempfile.TemporaryFile("w+t") as errors:
            with (
                patch.object(captured.os, "pipe", return_value=(reader, writer)),
                patch.object(captured, "_private_fd", side_effect=relocate),
                patch.object(captured.subprocess, "Popen") as launch,
                self.assertRaises(OSError) as caught,
            ):
                captured._linux(["owned"], 1, None, None, output, errors, None)
        self.assertIs(failure, caught.exception)
        launch.assert_not_called()
        for descriptor in descriptors:
            with self.subTest(descriptor=descriptor), self.assertRaises(OSError) as closed:
                os.fstat(descriptor)
            self.assertEqual(errno.EBADF, closed.exception.errno)

    def test_repeated_interrupts_wait_for_cleanup_and_keep_the_original_interrupt(self):
        first, second = KeyboardInterrupt("first"), KeyboardInterrupt("second")
        helper = Mock(returncode=0)
        helper.wait.side_effect = [first, second, 0]

        def launch(words, **options):
            self.assertNotIn("PRIVATE account", words)
            request = json.loads(os.pread(options["pass_fds"][0], reaper.MAX_PACKET, 0))
            os.write(request["ready"], json.dumps({"schema": 1, "nonce": request["nonce"], "ready": True}).encode())
            packet = {
                "schema": 1,
                "nonce": request["nonce"],
                "clean": True,
                "started": True,
                "returncode": -signal.SIGTERM,
                "expired": False,
            }
            os.write(options["pass_fds"][1], json.dumps(packet).encode())
            return helper

        previous = signal.getsignal(signal.SIGTERM)
        with patch.object(captured, "preflight"), patch.object(captured.subprocess, "Popen", side_effect=launch):
            with patch.object(captured.sys, "platform", "linux"), self.assertRaises(KeyboardInterrupt) as caught:
                captured.run(["PRIVATE account"], timeout=1)
        self.assertIs(first, caught.exception)
        self.assertEqual(3, helper.wait.call_count)
        self.assertIs(previous, signal.getsignal(signal.SIGTERM))
        self.assertLessEqual(
            helper.wait.call_args_list[2].kwargs["timeout"], helper.wait.call_args_list[1].kwargs["timeout"]
        )

    @unittest.skipUnless(os.name == "posix", "private POSIX descriptors")
    def test_closed_standard_descriptors_cannot_overwrite_the_protocol(self):
        program = f"""
import json,os,sys
from unittest.mock import Mock,patch
sys.path.insert(0,{str(Path(__file__).resolve().parents[1] / "tools")!r})
import autocode_captured_process as captured
for fd in (0,1,2): os.close(fd)
def launch(words, **options):
    assert all(fd > 2 for fd in options['pass_fds'])
    request=json.loads(os.pread(options['pass_fds'][0],65536,0))
    os.write(request['ready'],json.dumps(dict(schema=1,nonce=request['nonce'],ready=True)).encode())
    os.write(options['pass_fds'][1],json.dumps(dict(schema=1,nonce=request['nonce'],clean=True,
        started=True,returncode=0,expired=False)).encode())
    return Mock(returncode=0)
with captured._temporary('w+t') as output, captured._temporary('w+t') as errors:
    assert output.fileno()>2 and errors.fileno()>2
    with patch.object(captured.subprocess,'Popen',side_effect=launch):
        assert captured._linux(['owned'],1,None,None,output,errors,None)==(0,False)
"""
        result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_text_capture_and_checked_failure_keep_separate_output(self):
        def execute(command, timeout, env, cwd, output, errors, stdin):
            self.assertEqual(
                (["owned"], 12, {"PATH": "/owned"}, "/workspace", subprocess.DEVNULL),
                (command, timeout, env, cwd, stdin),
            )
            os.write(output.fileno(), b"result\n")
            os.write(errors.fileno(), b"diagnostic\n")
            return 7, False

        with (
            patch.object(captured, "preflight"),
            patch.object(captured, "_linux", execute),
            patch.object(captured, "_sampled", execute),
        ):
            for check in (False, True):
                with self.subTest(check=check):
                    if check:
                        with self.assertRaises(subprocess.CalledProcessError) as caught:
                            captured.run(
                                ["owned"],
                                timeout=12,
                                env={"PATH": "/owned"},
                                cwd="/workspace",
                                stdin=subprocess.DEVNULL,
                                check=True,
                            )
                        result = caught.exception
                    else:
                        result = captured.run(
                            ["owned"], timeout=12, env={"PATH": "/owned"}, cwd="/workspace", stdin=subprocess.DEVNULL
                        )
                    self.assertEqual((7, "result\n", "diagnostic\n"), (result.returncode, result.stdout, result.stderr))

    def test_unsupported_capture_shape_never_launches(self):
        with patch.object(captured.subprocess, "Popen") as launch, self.assertRaises(ValueError):
            captured.run(["owned"], capture_output=False)
        launch.assert_not_called()

    @unittest.skipUnless(reaper is not None, "private reaper implementation")
    def test_missing_or_uncertain_terminal_packet_never_becomes_a_timeout(self):
        for response in (
            None,
            {"clean": False, "error": "FileNotFoundError"},
            {"clean": True, "started": True, "returncode": None, "expired": False},
            {"schema": True, "clean": True, "started": True, "returncode": 0, "expired": False},
            {"clean": True, "started": False, "returncode": None, "expired": True, "start_errno": errno.ENOENT},
            {"clean": True, "started": True, "returncode": 0, "expired": False, "start_errno": errno.ENOENT},
            {"clean": True, "started": False, "returncode": None, "expired": False, "start_errno": 0},
        ):
            with self.subTest(response=response):

                def launch(words, **options):
                    request = json.loads(os.pread(options["pass_fds"][0], reaper.MAX_PACKET, 0))
                    os.write(
                        request["ready"], json.dumps({"schema": 1, "nonce": request["nonce"], "ready": True}).encode()
                    )
                    if response is not None:
                        packet = {"schema": 1, "nonce": request["nonce"], **response}
                        os.write(options["pass_fds"][1], json.dumps(packet).encode())
                    return Mock(returncode=0)

                with tempfile.TemporaryFile("w+t") as output, tempfile.TemporaryFile("w+t") as errors:
                    with (
                        patch.object(captured.subprocess, "Popen", side_effect=launch),
                        self.assertRaises(captured.ProcessError),
                    ):
                        captured._linux(["owned"], 1, None, None, output, errors, None)

    @unittest.skipUnless(reaper is not None, "private reaper implementation")
    def test_cleanup_inventory_error_is_not_a_prelaunch_exec_error(self):
        with patch.object(reaper, "children", side_effect=FileNotFoundError(errno.ENOENT, "inventory unavailable")):
            with patch.object(reaper, "drain", return_value=(0, False)), self.assertRaises(FileNotFoundError):
                reaper.cleanup(123, 0)

    @unittest.skipUnless(reaper is not None, "private reaper implementation")
    def test_drain_includes_clone_children_and_collects_the_exact_root_status(self):
        with patch.object(reaper.os, "waitpid", side_effect=[(8, 3 << 8), (7, 9 << 8), ChildProcessError]) as wait:
            self.assertEqual((9, True), reaper.drain(7, None))
        self.assertEqual([((-1, os.WNOHANG | reaper.WAIT_ALL),)] * 3, [(call.args,) for call in wait.call_args_list])

    @unittest.skipUnless(reaper is not None, "private reaper implementation")
    def test_child_churn_yields_control_without_claiming_absence(self):
        with patch.object(reaper.os, "waitpid", return_value=(8, 0)) as wait:
            self.assertEqual((None, False), reaper.drain(7, None))
        self.assertEqual(reaper.DRAIN_BATCH, wait.call_count)

    @unittest.skipUnless(reaper is not None, "private reaper implementation")
    def test_a_later_reused_root_pid_cannot_replace_its_collected_status(self):
        with patch.object(reaper.os, "waitpid", side_effect=[(7, 9 << 8), ChildProcessError]):
            self.assertEqual((3, True), reaper.drain(7, 3))


@unittest.skipUnless(sys.platform == "linux", "Linux invocation-local subreaper")
class CapturedLinuxTests(unittest.TestCase):
    def test_small_and_large_binary_and_text_input_reaches_the_owned_target(self):
        from providers import env_prep

        command = [
            sys.executable,
            "-c",
            "import sys; data=sys.stdin.buffer.read(); sys.stdout.buffer.write(data); sys.stderr.buffer.write(data)",
        ]
        for text, payload, encoding in (
            (False, b"\xff\r\n", None),
            (False, bytes(range(256)) * 1024, None),
            (True, "é\r\n", "latin-1"),
            (True, "é\r\n" * 65536, "utf-8"),
        ):
            with self.subTest(text=text, size=len(payload), encoding=encoding):
                with patch.dict(
                    os.environ, {"HOME": str(self.root / "home"), "XDG_STATE_HOME": str(self.root / "metadata-state")}
                ):
                    result = env_prep.preflight_run(
                        command,
                        dict(os.environ),
                        input=payload,
                        text=text,
                        encoding=encoding,
                        capture_output=True,
                        timeout=5,
                    )
                expected = payload.replace("\r\n", "\n").replace("\r", "\n") if text else payload
                self.assertEqual(0, result.returncode)
                self.assertEqual((expected, expected), (result.stdout, result.stderr))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workers = []
        self.addCleanup(self.clean_workers)

    @staticmethod
    def identity(pid):
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()
        except FileNotFoundError:
            return None
        return fields[19]

    def clean_workers(self):
        for path in self.workers:
            if path.exists():
                row = json.loads(path.read_text())
                try:
                    descriptor = os.pidfd_open(row["pid"])
                except ProcessLookupError:
                    continue
                try:
                    if self.identity(row["pid"]) == row["start"]:
                        with suppress(ProcessLookupError):
                            signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                        self.assertTrue(select.select([descriptor], [], [], 5)[0], "fixture worker did not stop")
                finally:
                    os.close(descriptor)

    def command(self, name="worker", outcome="0", together=None, ignore_term=False):
        record = self.root / f"{name}.json"
        self.workers.append(record)
        body = (
            "import json, os, pathlib, signal, time\n"
            "first = os.fork()\n"
            "if first == 0:\n"
            " os.setsid()\n"
            " if os.fork(): os._exit(0)\n"
            + (" signal.signal(signal.SIGTERM, signal.SIG_IGN)\n" if ignore_term else "")
            + " pid = os.getpid()\n"
            " start = pathlib.Path('/proc/self/stat').read_text().rpartition(')')[2].split()[19]\n"
            f" pathlib.Path({str(record)!r}).write_text(json.dumps({{'pid': pid, 'start': start}}))\n"
            " for fd in (0, 1, 2): os.close(fd)\n"
            " read, write = os.pipe(); os.read(read, 1)\n"
            "os.waitpid(first, 0)\n"
            f"while not pathlib.Path({str(record)!r}).exists(): time.sleep(.001)\n"
            "print('private stdout', flush=True)\n"
            "os.write(2, b'private stderr\\n')\n"
        )
        if together is not None:
            body += f"while not pathlib.Path({str(together)!r}).exists(): time.sleep(.001)\n"
        body += (
            "read, write = os.pipe(); os.read(read, 1)\n"
            if outcome == "timeout"
            else "os.kill(os.getpid(), signal.SIGTERM)\n"
            if outcome == "signal"
            else f"os._exit({outcome})\n"
        )
        return [sys.executable, "-c", body], record

    def assert_collected(self, record):
        row = json.loads(record.read_text())
        self.assertNotEqual(row["start"], self.identity(row["pid"]), "captured invocation left its orphan alive")

    def test_rapid_detach_and_root_exit_collect_every_child_before_returning(self):
        original_wait = captured.supervisor.wait

        def late_first_sample(child, timeout):
            # Negative control: base's sampled implementation must face the
            # already-exited root, rather than occasionally winning a fork race.
            os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)
            return original_wait(child, timeout)

        for outcome, expected in (("0", 0), ("7", 7), ("signal", -signal.SIGTERM)):
            with self.subTest(outcome=outcome):
                command, record = self.command(name="worker-" + outcome, outcome=outcome)
                with patch.object(captured.supervisor, "wait", late_first_sample):
                    result = captured.run(command, timeout=5)
                self.assertEqual(
                    (expected, "private stdout\n", "private stderr\n"),
                    (result.returncode, result.stdout, result.stderr),
                )
                self.assert_collected(record)
                record.unlink()

    def test_timeout_collects_orphans_before_returning_partial_output(self):
        command, record = self.command(outcome="timeout")
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            captured.run(command, timeout=1.5)
        self.assertEqual(("private stdout\n", "private stderr\n"), (caught.exception.stdout, caught.exception.stderr))
        self.assert_collected(record)

    def test_term_resistant_orphan_is_killed_and_collected_before_success(self):
        command, record = self.command(ignore_term=True)
        result = captured.run(command, timeout=5)
        self.assertEqual(0, result.returncode)
        self.assert_collected(record)

    def test_clone_without_exit_signal_is_owned_and_collected(self):
        number = {"x86_64": 56, "aarch64": 220}.get(platform.machine())
        if number is None:
            self.skipTest("qualified Linux clone syscall ABI")
        record = self.root / "clone.json"
        self.workers.append(record)
        body = (
            "import ctypes,json,os,pathlib\n"
            "libc=ctypes.CDLL(None,use_errno=True)\n"
            # CLONE_PARENT gives this clone the helper as its real parent;
            # exit_signal=0 requires __WALL even before any adoption occurs.
            f"pid=libc.syscall(ctypes.c_long({number}),ctypes.c_ulong(0x8000),"
            "ctypes.c_void_p(),ctypes.c_void_p(),ctypes.c_void_p(),ctypes.c_ulong())\n"
            "if pid < 0: raise OSError(ctypes.get_errno(), 'clone failed')\n"
            "if pid == 0:\n"
            " r,w=os.pipe(); os.read(r,1)\n"
            "start=pathlib.Path(f'/proc/{pid}/stat').read_text().rpartition(')')[2].split()[19]\n"
            f"pathlib.Path({str(record)!r}).write_text(json.dumps({{'pid':pid,'start':start}}))\n"
            "os._exit(0)\n"
        )
        self.assertEqual(0, captured.run([sys.executable, "-c", body], timeout=5).returncode)
        self.assert_collected(record)

    def test_threaded_invocations_keep_their_families_and_foreign_process_separate(self):
        sentinel = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)
        self.addCleanup(sentinel.wait, timeout=5)
        self.addCleanup(sentinel.stdin.close)
        first, first_record = self.command("first", together=self.root / "second.json")
        second, second_record = self.command("second", together=first_record)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(captured.run, words, timeout=5) for words in (first, second)]
            self.assertEqual([0, 0], [future.result(timeout=10).returncode for future in futures])
        self.assert_collected(first_record)
        self.assert_collected(second_record)
        self.assertIsNone(sentinel.poll())

    def test_ignored_sigchld_does_not_discard_the_root_exit_status(self):
        previous = signal.signal(signal.SIGCHLD, signal.SIG_IGN)
        try:
            result = captured.run([sys.executable, "-c", "raise SystemExit(7)"], timeout=5)
        finally:
            signal.signal(signal.SIGCHLD, previous)
        self.assertEqual(7, result.returncode)

    def test_cancellation_collects_the_family_and_restores_the_handler(self):
        command, record = self.command(outcome="timeout")
        previous = signal.getsignal(signal.SIGTERM)
        stop = threading.Event()

        def interrupt():
            until = time.monotonic() + 5
            while not stop.wait(0.01) and time.monotonic() < until:
                if record.exists():
                    os.kill(os.getpid(), signal.SIGTERM)
                    return

        watcher = threading.Thread(target=interrupt)
        watcher.start()
        try:
            with self.assertRaises(KeyboardInterrupt):
                captured.run(command, timeout=10)
        finally:
            stop.set()
            watcher.join(timeout=5)
        self.assert_collected(record)
        self.assertIs(previous, signal.getsignal(signal.SIGTERM))

    def test_missing_exec_remains_an_os_error_with_no_started_family(self):
        with self.assertRaises(FileNotFoundError):
            captured.run([str(self.root / "missing")], timeout=5)
