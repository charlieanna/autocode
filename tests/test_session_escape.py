"""Offline providers exercise the real kernel/exec boundary, with event gates."""

import contextlib
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
from pathlib import Path
from unittest.mock import Mock, patch

import autocode_process as processes
import autocode_session_boundary as boundary
import autocode_supervision as supervision
import autocode_supervision_handoff as handoff
import autocode_supervision_keeper as keeper

TOOLS = Path(__file__).resolve().parents[1] / "tools"


class SessionReplyTests(unittest.TestCase):
    def test_shutdown_during_cookie_validation_cannot_continue_a_request(self):
        triggered, done = threading.Event(), threading.Event()

        def valid(*args):
            triggered.set()
            return True

        with (
            patch.object(boundary, "request_valid", side_effect=valid),
            patch.object(boundary, "continue_request") as continued,
            patch.object(boundary, "reject_request") as rejected,
        ):
            self.assertFalse(keeper._reply_session(9, 42, threading.Lock(), triggered, done))
            continued.assert_not_called()
            rejected.assert_called_once_with(9, 42)

    def test_unsupported_host_refuses_before_any_child_or_payload_exec(self):
        with (
            patch.object(boundary, "require_supported", side_effect=boundary.UnsupportedBoundary("unsupported host")),
            patch.object(supervision.subprocess, "Popen") as launched,
            tempfile.TemporaryDirectory() as root,
        ):
            with self.assertRaisesRegex(supervision.SupervisionError, "no provider launched"):
                with supervision.launch(
                    [sys.executable, "-c", "raise RuntimeError('payload ran')"],
                    receipt_path=Path(root) / "receipt.json",
                    start_new_session=True,
                ):
                    self.fail("Unsupported launch was released")
            launched.assert_not_called()
            self.assertFalse((Path(root) / "receipt.json").exists())

    def test_macos_launches_the_provider_without_claiming_the_kernel_boundary(self):
        # #1195: macOS has no seccomp, so the provider runs under runner-command supervision.
        portable = boundary.PortableHost("Provider session ownership requires Linux native x86_64 or aarch64")
        saved, notes = [], []
        with (
            patch.object(boundary, "require_supported", side_effect=portable),
            patch.object(supervision, "_portable_noted", False),
            patch.object(supervision, "print", create=True, side_effect=lambda *a, **k: notes.append(a[0])),
            tempfile.TemporaryDirectory() as root,
        ):
            for name in ("first", "second"):
                with supervision.launch(
                    [sys.executable, "-c", "print('payload ran')"],
                    receipt_path=Path(root) / f"{name}.json",
                    timeout=30,
                    checkpoint=saved.append,
                    start_new_session=True,
                    stdout=subprocess.PIPE,
                    text=True,
                ) as child:
                    self.assertEqual("payload ran\n", child.communicate(timeout=20)[0])
                receipt = supervision.receipt(saved[-1])
                self.assertEqual("stopped", receipt["phase"], receipt)
                self.assertNotIn("session_boundary", receipt)
            self.assertEqual(1, len(notes), notes)
            self.assertIn("unavailable on macOS", notes[0])

    def test_other_non_linux_hosts_refuse_before_child_checkpoint_or_receipt(self):
        for host, machine in (("win32", "x86_64"), ("freebsd14", "x86_64")):
            checkpoint = Mock()
            with (
                self.subTest(host=host, machine=machine),
                patch.object(boundary.sys, "platform", host),
                patch.object(boundary.platform, "machine", return_value=machine),
                patch.object(boundary, "_libc") as libc,
                patch.object(
                    supervision.subprocess,
                    "Popen",
                    side_effect=AssertionError("Unsupported host reached a subprocess launch"),
                ) as launched,
                tempfile.TemporaryDirectory() as root,
            ):
                with self.assertRaisesRegex(supervision.SupervisionError, "boundary unavailable; no provider launched"):
                    with supervision.launch(
                        [sys.executable, "-c", "raise RuntimeError('payload ran')"],
                        receipt_path=Path(root) / "receipt.json",
                        checkpoint=checkpoint,
                        start_new_session=True,
                    ):
                        self.fail("Unsupported launch was released")
                libc.assert_not_called()
                launched.assert_not_called()
                checkpoint.assert_not_called()
                self.assertFalse((Path(root) / "receipt.json").exists())


@unittest.skipUnless(sys.platform == "linux", "Native seccomp qualification runs on Linux")
class SessionEscapeTests(unittest.TestCase):
    def setUp(self):
        boundary.require_supported()
        self.temp = tempfile.TemporaryDirectory(prefix="session-escape-")
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def stop(self, child):
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream:
                stream.close()

    def cleanup_identity(self, row):
        current = processes.process_table({row["pid"]}).get(row["pid"])
        if processes.matches(row, current) and not current["state"].startswith("Z"):
            os.kill(row["pid"], signal.SIGKILL)

    def wait_file(self, path):
        # The fixture publishes an event by atomic rename. Time only bounds a
        # broken fixture; no elapsed duration establishes behavior.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if path.exists():
                return json.loads(path.read_text())
            threading.Event().wait(0.01)
        self.fail(f"Fixture did not publish {path.name}")

    def escaped_child(self, *, lose_keeper=False, thread=False):
        entered_r, entered_w = os.pipe()
        gate_r, gate_w = os.pipe()
        for fd in (entered_r, entered_w, gate_r, gate_w):
            self.addCleanup(lambda fd=fd: self.close_fd(fd))
        worker_path = self.root / "worker.json"
        wrapper = self.root / "keeper.py"
        wrapper.write_text(f"""import os,select,sys
sys.path.insert(0,{str(TOOLS)!r})
import autocode_supervision_keeper as keeper
original_sample = keeper.KeeperTree.sample
original_stop = keeper.KeeperTree.stop
def sample(self,*args,**kwargs):
    # Kernel notifications continue to run, but no ancestry/group/SID sample
    # can discover a descendant before the provider has exited and been reaped.
    return keeper.processes.live_processes([self.known[self.pid]])
def stop(self,*args,**kwargs):
    keeper.KeeperTree.sample = original_sample
    os.write({entered_w},b'R')
    if not select.select([{gate_r}],[],[],10)[0] or os.read({gate_r},1)!=b'G':
        raise RuntimeError('cleanup barrier was not released')
    return original_stop(self,*args,**kwargs)
keeper.KeeperTree.sample=sample
keeper.KeeperTree.stop=stop
raise SystemExit(keeper.main(int(sys.argv[1]),int(sys.argv[2])))
""")
        transition = "threading.Thread(target=os.setsid); t.start(); t.join()" if thread else "None; os.setsid()"
        provider = f"""import os,sys,signal,json,threading
sys.path.insert(0,{str(TOOLS)!r})
import autocode_process as processes
r,w=os.pipe()
a=os.fork()
if a==0:
    os.close(w)
    t={transition}
    b=os.fork()
    if b: os._exit(0)
    # The admitted creator is gone, ordinary descriptors are closed, and
    # this last child is reparented before the keeper can sample it.
    os.read(r,1)
    os.closerange(3,1024)
    row=processes.process_table({{os.getpid()}})[os.getpid()]
    value={{'identity':processes.identity(row),'session':row['session'],'parent':row['parent']}}
    tmp={str(worker_path)!r}+'.tmp'
    with open(tmp,'w') as output: json.dump(value,output)
    os.rename(tmp,{str(worker_path)!r})
    signal.signal(signal.SIGTERM,signal.SIG_IGN)
    signal.pause()
else:
    os.close(r)
    os.waitpid(a,0)
    os._exit(0)
"""
        real_popen = subprocess.Popen

        def popen(command, **options):
            if len(command) > 2 and Path(command[2]).name == "autocode_supervision_keeper.py":
                command = [command[0], "-E", str(wrapper), *command[3:]]
                options["pass_fds"] += (entered_w, gate_r)
            return real_popen(command, **options)

        sentinel = real_popen([sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True)
        self.addCleanup(self.stop, sentinel)
        sentinel_row = processes.identity(processes.process_table({sentinel.pid})[sentinel.pid])
        expected = self.assertRaises(supervision.SupervisionError) if lose_keeper else contextlib.nullcontext()
        with patch.object(supervision.subprocess, "Popen", side_effect=popen), expected:
            with supervision.launch(
                [sys.executable, "-u", "-c", provider],
                receipt_path=self.root / "receipt.json",
                timeout=25,
                start_new_session=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ) as child:
                self.addCleanup(self.stop, child)
                self.assertEqual(0, child.wait(timeout=10))
                worker = self.wait_file(worker_path)
                self.addCleanup(self.cleanup_identity, worker["identity"])
                self.assertEqual(1, worker["parent"])
                self.assertNotEqual(child.pid, worker["session"])
                self.assertTrue(select.select([entered_r], [], [], 10)[0])
                self.assertEqual(b"R", os.read(entered_r, 1))
                before = supervision.receipt(child.supervision)
                self.assertFalse(any(processes.matches(worker["identity"], row) for row in before["processes"]))
                if lose_keeper:
                    self.cleanup_identity(child.supervision["keeper"])
                else:
                    os.write(gate_w, b"G")
        self.assertFalse(processes.live_processes([worker["identity"]]), "Unseen new-session child survived")
        self.assertTrue(processes.live_processes([sentinel_row]), "Unrelated sentinel was signalled")
        self.assertEqual(0, child.returncode)
        if not lose_keeper:
            self.assertEqual("stopped", child.supervision_result["phase"])
            self.assertIsNone(child.supervision_result["cleanup_error"])

    @staticmethod
    def close_fd(fd):
        with contextlib.suppress(OSError):
            os.close(fd)

    def test_unseen_setsid_double_fork_and_fd_closure_cannot_escape(self):
        self.escaped_child()

    def test_keeper_loss_restores_new_session_authority_from_receipt(self):
        self.escaped_child(lose_keeper=True)

    def test_nonleader_thread_setsid_is_owned_by_its_process_identity(self):
        self.escaped_child(thread=True)

    def test_cancelled_kernel_request_preserves_normal_provider_result(self):
        calls = []

        def cancel(metadata, record, deadline):
            # The syscall is blocked in the kernel and its durable notice has
            # reached the controller. Kill only that exact fixture birth.
            calls.append(record)
            self.cleanup_identity(record["creator"])
            # SIGKILL delivery is asynchronous. A cancellation acknowledgement
            # requires this exact birth to be gone, not merely a queued signal.
            # Observe without reaping the provider's child or extending the
            # enclosing handoff's deadline.
            keeper.Observer(record["creator"]).wait(timeout=max(0, deadline - time.monotonic()))
            return False

        provider = """import os,sys,signal
p=os.fork()
if p==0:
    os.setsid()
    raise AssertionError('cancelled syscall was continued')
else:
    _,status=os.waitpid(p,0)
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status)==signal.SIGKILL
    sys.exit(7)
"""
        with patch.object(handoff, "admit_session", side_effect=cancel):
            with supervision.launch(
                [sys.executable, "-c", provider],
                receipt_path=self.root / "cancelled.json",
                timeout=15,
                start_new_session=True,
            ) as child:
                self.assertEqual(7, child.wait(timeout=10))
        self.assertEqual(1, len(calls))
        self.assertEqual("stopped", child.supervision_result["phase"])
        self.assertIsNone(child.supervision_result["cleanup_error"])

    def test_unavailable_kernel_listener_never_executes_payload(self):
        wrapper = self.root / "unavailable-bootstrap.py"
        marker = self.root / "payload-ran"
        wrapper.write_text(f"""import sys
sys.path.insert(0,{str(TOOLS)!r})
import autocode_supervision_bootstrap as bootstrap
def unavailable():
    raise bootstrap.boundary.UnsupportedBoundary('kernel listener unavailable')
bootstrap.boundary.install=unavailable
raise SystemExit(bootstrap.main())
""")
        real_popen = subprocess.Popen

        def popen(command, **options):
            if len(command) > 2 and Path(command[2]).name == "autocode_supervision_bootstrap.py":
                command = [command[0], "-I", str(wrapper), *command[3:]]
            return real_popen(command, **options)

        with patch.object(supervision.subprocess, "Popen", side_effect=popen):
            with self.assertRaises(supervision.SupervisionError):
                with supervision.launch(
                    [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
                    receipt_path=self.root / "unavailable.json",
                    timeout=5,
                    start_new_session=True,
                    stderr=subprocess.DEVNULL,
                ):
                    self.fail("Kernel failure released provider exec")
        self.assertFalse(marker.exists())

    def test_nested_detached_tool_and_repeated_setsid_preserve_native_exit(self):
        owner_row = processes.identity(processes.process_table({os.getpid()})[os.getpid()])
        read_fd, write_fd = os.pipe()
        self.addCleanup(self.close_fd, read_fd)
        self.addCleanup(self.close_fd, write_fd)
        tool = 'import os,errno; os.setsid();\ntry: os.setsid()\nexcept OSError as e: assert e.errno==errno.EPERM\nelse: raise AssertionError("setsid should fail for leader")'
        provider = f"""import os,subprocess,sys
code={tool!r}
p=subprocess.Popen([sys.executable,'-c',code])
assert p.wait()==0
sys.exit(7)
"""
        # A guarded CLI runs in its own process/session. An assertion failure
        # must never put the unittest runner under that guard's cleanup policy.
        script = f"""import sys
sys.path.insert(0,{str(TOOLS)!r})
import autocode_supervision as guard
with guard.protect_owner({read_fd},receipt_path={str(self.root / "outer.json")!r},timeout=25,
                         owner_identity={owner_row!r},nonce={"a" * 32!r}):
    with guard.launch({[sys.executable, "-c", provider]!r},receipt_path={str(self.root / "nested.json")!r},
                      timeout=20,start_new_session=True) as child:
        assert child.wait(timeout=10)==7
    assert child.supervision_result['phase']=='stopped'
"""
        owner = subprocess.Popen(
            [sys.executable, "-c", script],
            pass_fds=(read_fd,),
            start_new_session=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.addCleanup(self.stop, owner)
        out, err = owner.communicate(timeout=20)
        self.assertEqual(0, owner.returncode, (out, err))


if __name__ == "__main__":
    unittest.main()
