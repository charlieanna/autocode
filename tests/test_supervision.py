"""Offline owner-loss faults reach the real exec and pipe-lifeline boundaries.

Event pipes/barriers establish each fault phase; timeouts only bound broken
fixtures. No model, allowance, private run-state write or elapsed-time oracle.
"""

import contextlib
import errno
import io
import json
import os
import select
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_elapsed_timing as elapsed_timing
import autocode_process as processes
import autocode_supervision as supervision

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def keeper_timing(deadline):
    monotonic_origin = time.monotonic()
    return elapsed_timing.admission(
        time.time(),
        monotonic_origin,
        None if deadline is None else deadline - monotonic_origin,
    )


class SupervisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="supervision-test-")
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    @unittest.skipUnless(sys.platform == "linux", "requires the native provider session listener")
    def test_watchdog_closed_lifeline_preserves_supervision_failure(self):
        provider_code = (
            "import json, subprocess, sys\n"
            "print(json.dumps({'ready': True}), flush=True)\n"
            "sys.stdin.readline()\n"
            "subprocess.run([sys.executable, '-c', 'pass'], start_new_session=True)\n"
        )
        with patch.object(
            supervision,
            "session_handoff_reply",
            side_effect=OSError(errno.EBADF, "injected session handoff failure"),
        ) as handoff_fault:
            with self.assertRaisesRegex(supervision.SupervisionError, "Session ownership handoff failed"):
                with supervision.launch(
                    [sys.executable, "-c", provider_code],
                    receipt_path=self.root / "watchdog-close.json",
                    timeout=20,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                ) as provider:
                    self.assertEqual(self.read_event(provider), {"ready": True})
                    provider.communicate(b"spawn\n", timeout=20)
        handoff_fault.assert_called_once()
        self.assertIsNotNone(provider.returncode)
        self.assertNotEqual(provider.returncode, 0)
        final = supervision.receipt(provider.supervision)
        self.assertEqual(final["phase"], "stopped")
        self.assertIsNone(final["cleanup_error"])

    @unittest.skipUnless(sys.platform == "linux", "requires the native provider session listener")
    def test_completion_does_not_write_to_reused_lifeline_descriptor(self):
        provider_code = (
            "import json, subprocess, sys\n"
            "print(json.dumps({'ready': True}), flush=True)\n"
            "sys.stdin.readline()\n"
            "subprocess.run([sys.executable, '-c', 'pass'], start_new_session=True)\n"
        )
        original_send = supervision._send
        control = []

        def observe_send(fd, value):
            if "metadata" in value:
                control.append(fd)
            return original_send(fd, value)

        unrelated = self.root / "unrelated-output"
        foreign_fd = None
        try:
            with (
                patch.object(supervision, "_send", side_effect=observe_send),
                patch.object(
                    supervision,
                    "session_handoff_reply",
                    side_effect=OSError(errno.EBADF, "injected session handoff failure"),
                ),
                self.assertRaisesRegex(supervision.SupervisionError, "Session ownership handoff failed"),
            ):
                with supervision.launch(
                    [sys.executable, "-c", provider_code],
                    receipt_path=self.root / "reused-descriptor.json",
                    timeout=20,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                ) as provider:
                    self.assertEqual(self.read_event(provider), {"ready": True})
                    provider.communicate(b"spawn\n", timeout=20)
                    self.assertEqual(len(control), 1)
                    # EOF has stopped the provider: the watchdog closed its writer.
                    # Reuse that exact number as an unrelated caller-owned file.
                    foreign_fd = os.open(unrelated, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
                    if foreign_fd != control[0]:
                        os.dup2(foreign_fd, control[0])
                        os.close(foreign_fd)
                        foreign_fd = control[0]
            self.assertEqual(unrelated.read_bytes(), b"")
            self.assertEqual(os.fstat(foreign_fd).st_ino, unrelated.stat().st_ino)
        finally:
            if foreign_fd is not None:
                with contextlib.suppress(OSError):
                    os.close(foreign_fd)

    def receipt_until(self, metadata, phases=("stopped", "uncertain", "discharged")):
        limit = time.monotonic() + 20
        while time.monotonic() < limit:
            value = supervision.receipt(metadata)
            if value and value.get("phase") in phases:
                return value
            threading.Event().wait(0.02)
        self.fail("Keeper never retained its terminal cleanup receipt")

    def read_event(self, child):
        # TextIO.readline can prefetch another event while select sees an empty
        # kernel pipe. Keep all received bytes here and consume queued lines
        # before asking the kernel for more readiness.
        pending = getattr(child, "_event_bytes", None)
        if pending is None:
            pending = child._event_bytes = bytearray()
        deadline = time.monotonic() + 20
        while b"\n" not in pending:
            remaining = deadline - time.monotonic()
            self.assertGreater(remaining, 0, "Fixture did not reach its event barrier")
            self.assertTrue(
                select.select([child.stdout], [], [], remaining)[0], "Fixture did not reach its event barrier"
            )
            chunk = os.read(child.stdout.fileno(), 65536)
            self.assertTrue(chunk, "Fixture exited before its event barrier")
            pending.extend(chunk)
        end = pending.index(b"\n")
        line = bytes(pending[:end])
        del pending[: end + 1]
        value = json.loads(line)
        if "metadata" in value:
            metadata = value["metadata"]
            scope = {"metadata": metadata, "known": {metadata["provider"]["pid"]: metadata["provider"]}}
            child._cleanup_scope = scope
            self.addCleanup(self.cleanup_supervised_owner, child, scope)
        elif "worker" in value and hasattr(child, "_cleanup_scope"):
            scope = child._cleanup_scope
            pid = value["worker"]
            if type(pid) is int and pid > 0:
                provider = scope["metadata"]["provider"]
                table = processes.process_table({provider["pid"], pid})
                worker = table.get(pid)
                receipt = supervision.receipt(scope["metadata"])
                recorded = (receipt or {}).get("processes", [])
                parent_verified = (
                    processes.matches(provider, table.get(provider["pid"]))
                    and worker
                    and worker["parent"] == provider["pid"]
                )
                if worker and (parent_verified or any(processes.matches(row, worker) for row in recorded)):
                    # Capture each event immediately; a later failed barrier
                    # must not discard an already verified detached worker.
                    scope["known"][pid] = processes.identity(worker)
        return value

    def cleanup_supervised_owner(self, child, scope):
        metadata = scope["metadata"]
        tree = processes.ProcessTree(metadata["provider"]["pid"], lambda rows: None, excluded=(metadata["keeper"],))
        tree.known = dict(scope["known"])
        deadline = time.monotonic() + 20

        def retained_workers():
            receipt = supervision.receipt(metadata)
            for row in (receipt or {}).get("processes", []):
                if not processes.matches(metadata["keeper"], row):
                    tree.known[row["pid"]] = row
            return list(tree.known.values())

        errors = []

        def remember(error):
            if not errors:
                errors.append(error)

        try:
            try:
                # Discover detached descendants while the native provider
                # parent still exists, then stop/reap our actual owner first.
                retained_workers()
                tree.sample(notify=False)
            except Exception as error:
                remember(error)
            finally:
                try:
                    self.stop_owner(child)
                except Exception as error:
                    remember(error)
            # A discovery error must not end the keeper's EOF cleanup chance.
            # Receipt refresh failures retain the already captured identities.
            while time.monotonic() < deadline - 2:
                try:
                    retained_workers()
                except Exception as error:
                    remember(error)
                try:
                    if not processes.live_processes(list(tree.known.values()) + [metadata["keeper"]]):
                        break
                except Exception as error:
                    remember(error)
                threading.Event().wait(0.02)
        finally:
            try:
                # Even failed discovery/inspection reaches native fallback.
                # Fresh absence makes this a no-op after successful cleanup.
                tree.signal(list(tree.known.values()), signal.SIGKILL)
            except Exception as error:
                remember(error)
            finally:
                try:
                    self.cleanup_identity(metadata["keeper"])
                except Exception as error:
                    remember(error)
        live = None
        while time.monotonic() < deadline:
            try:
                live = processes.live_processes(list(tree.known.values()) + [metadata["keeper"]])
            except Exception as error:
                remember(error)
                break
            if not live:
                break
            threading.Event().wait(0.02)
        if errors:
            raise errors[0]
        self.assertEqual([], live, "Captured fixture processes survived bounded cleanup")

    def cleanup_identity(self, row):
        if row and processes.live_processes([row]):
            current = processes.process_table({row["pid"]}).get(row["pid"])
            if processes.matches(row, current):
                os.kill(row["pid"], signal.SIGKILL)

    def start_owner(self, provider, *, callback=None, timeout=30):
        command = [sys.executable, "-u", "-c", provider]
        callback_code = callback or "print(json.dumps({'metadata':meta}),flush=True)"
        script = f"""import sys,os,json,signal
sys.path.insert(0,{str(TOOLS)!r})
import autocode_supervision as guard

def checkpoint(meta):
    {callback_code}
with guard.launch({command!r},receipt_path={str(self.root / "receipt.json")!r},timeout={timeout!r},
                  checkpoint=checkpoint,start_new_session=True,stdout=sys.stdout,stderr=sys.stderr) as child:
    child.wait()
"""
        child = subprocess.Popen(
            [sys.executable, "-u", "-c", script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        self.addCleanup(self.stop_owner, child)
        return child

    def stop_owner(self, child):
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream:
                stream.close()

    def test_coalesced_events_are_read_while_the_pipe_writer_remains_open(self):
        read_fd, write_fd = os.pipe()
        output = os.fdopen(read_fd, "r")
        self.addCleanup(output.close)
        self.addCleanup(os.close, write_fd)
        os.write(write_fd, b'{"provider":101}\n{"worker":102}\n')
        child = SimpleNamespace(stdout=output)
        original_select = select.select
        # Both complete events already exist. An immediate kernel-readiness
        # probe makes the old TextIO-prefetch failure deterministic and fast.
        with patch.object(
            select,
            "select",
            side_effect=lambda readers, writers, errors, timeout: original_select(readers, writers, errors, 0),
        ):
            self.assertEqual({"provider": 101}, self.read_event(child))
            self.assertEqual({"worker": 102}, self.read_event(child))

    def test_failed_event_barrier_cleanup_keeps_guardian_until_owner_loss_cleanup(self):
        sentinel = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)
        self.addCleanup(self.stop_owner, sentinel)
        sentinel_identity = processes.identity(processes.process_table({sentinel.pid})[sentinel.pid])
        provider = """import subprocess,sys,signal
subprocess.Popen([sys.executable,'-u','-c',"import os,json,signal; signal.signal(signal.SIGTERM,signal.SIG_IGN); print(json.dumps({'worker':os.getpid()}),flush=True); signal.pause()"],start_new_session=True)
signal.pause()
"""
        owner = self.start_owner(provider)
        metadata = self.read_event(owner)["metadata"]
        event = self.read_event(owner)
        worker_identity = owner._cleanup_scope["known"][event["worker"]]
        with patch.object(select, "select", return_value=([], [], [])):
            with self.assertRaisesRegex(AssertionError, "event barrier"):
                self.read_event(owner)
        self.cleanup_supervised_owner(owner, owner._cleanup_scope)
        self.assertIsNotNone(owner.returncode)
        receipt = supervision.receipt(metadata)
        self.assertEqual("owner_lost", receipt["cause"])
        self.assertEqual("stopped", receipt["phase"], receipt)
        self.assertFalse(processes.live_processes([worker_identity]))
        self.assertTrue(processes.live_processes([sentinel_identity]))

    def test_discovery_error_still_allows_guardian_to_clean_captured_worker(self):
        provider = """import subprocess,sys,signal
subprocess.Popen([sys.executable,'-u','-c',"import os,json,signal; signal.signal(signal.SIGTERM,signal.SIG_IGN); print(json.dumps({'worker':os.getpid()}),flush=True); signal.pause()"],start_new_session=True)
signal.pause()
"""
        owner = self.start_owner(provider)
        metadata = self.read_event(owner)["metadata"]
        event = self.read_event(owner)
        worker_identity = owner._cleanup_scope["known"][event["worker"]]
        with patch.object(
            processes.ProcessTree, "sample", side_effect=processes.ProcessError("fixture discovery denied")
        ):
            with self.assertRaisesRegex(processes.ProcessError, "fixture discovery denied"):
                self.cleanup_supervised_owner(owner, owner._cleanup_scope)
        receipt = supervision.receipt(metadata)
        self.assertEqual("owner_lost", receipt["cause"])
        self.assertEqual("stopped", receipt["phase"], receipt)
        self.assertFalse(processes.live_processes([worker_identity, metadata["provider"], metadata["keeper"]]))

    def test_exec_preserves_actual_popen_pid_parent_session_stdio_and_exit(self):
        code = "import os,sys,json; print(json.dumps({'pid':os.getpid(),'parent':os.getppid(),'session':os.getsid(0),'input':sys.stdin.read()})); print('stderr-kept',file=sys.stderr); raise SystemExit(7)"
        saved = []
        with supervision.launch(
            [sys.executable, "-c", code],
            receipt_path=self.root / "receipt.json",
            timeout=30,
            checkpoint=saved.append,
            start_new_session=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as child:
            root = processes.ProcessTree(child.pid, lambda rows: None).capture_root()
            admitted = child.supervision["provider"]
            self.assertTrue(processes.matches(admitted, root))
            self.assertEqual(admitted["group"], root["group"])
            if sys.platform == "linux":
                self.assertEqual(child.pid, admitted["session"])
                self.assertNotIn("session", root)
            output, error = child.communicate("the prompt", timeout=20)
            row = json.loads(output)
            self.assertIsInstance(child, subprocess.Popen)
            self.assertEqual(
                {"pid": child.pid, "parent": os.getpid(), "session": child.pid, "input": "the prompt"}, row
            )
            self.assertEqual(7, child.returncode)
            self.assertEqual("stderr-kept\n", error)
        self.assertEqual(child.supervision, saved[0])
        self.assertEqual("stopped", supervision.receipt(saved[0])["phase"])

    def test_verified_abnormal_stop_is_distinct_from_unknown_or_uncertain_cleanup(self):
        read_receipt = supervision.receipt
        cases = [
            ("stage_deadline", "stopped", None, supervision.VerifiedStop),
            ("unknown_cause", "stopped", None, supervision.SupervisionError),
            ("stage_deadline", "uncertain", "not collected", supervision.SupervisionError),
        ]
        for index, (cause, phase, cleanup, expected) in enumerate(cases):

            def terminal(metadata):
                value = read_receipt(metadata)
                return (
                    {**value, "cause": cause, "phase": phase, "cleanup_error": cleanup}
                    if value and value.get("phase") == "stopped"
                    else value
                )

            with self.subTest(cause=cause, phase=phase), patch.object(supervision, "receipt", side_effect=terminal):
                with self.assertRaises(expected) as caught:
                    with supervision.launch(
                        [sys.executable, "-c", 'print("partial artifact")'],
                        receipt_path=self.root / f"receipt-{index}.json",
                        timeout=30,
                        start_new_session=True,
                        stdout=subprocess.PIPE,
                        text=True,
                    ) as child:
                        output, _ = child.communicate(timeout=20)
                        self.assertEqual("partial artifact\n", output)
                self.assertIs(type(caught.exception), expected)
                if expected is supervision.VerifiedStop:
                    self.assertEqual("stage_deadline", caught.exception.cause)
                self.assertFalse(processes.live_processes(read_receipt(child.supervision)["processes"]))

    def test_owner_sigkill_stops_provider_and_retains_cause_without_touching_sentinel(self):
        sentinel = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)
        self.addCleanup(self.stop_owner, sentinel)
        sentinel_identity = processes.identity(processes.process_table({sentinel.pid})[sentinel.pid])
        owner = self.start_owner(
            "import os,json,signal; print(json.dumps({'provider':os.getpid(),'parent':os.getppid()}),flush=True); signal.pause()"
        )
        metadata = self.read_event(owner)["metadata"]
        event = self.read_event(owner)
        self.assertEqual(metadata["provider"]["pid"], event["provider"])
        self.assertEqual(owner.pid, event["parent"])
        os.kill(owner.pid, signal.SIGKILL)
        owner.wait(timeout=10)
        receipt = self.receipt_until(metadata)
        self.assertEqual("owner_lost", receipt["cause"])
        self.assertEqual("stopped", receipt["phase"], receipt)
        self.assertFalse(processes.live_processes(receipt["processes"]))
        self.assertTrue(processes.live_processes([sentinel_identity]))
        self.assertFalse(supervision.observe(metadata)["owner"]["alive"])

    def test_owner_death_after_arming_before_release_never_executes_provider(self):
        marker = self.root / "executed"
        callback = "print(json.dumps({'metadata':meta}),flush=True); sys.stdin.read()"
        owner = self.start_owner(
            f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')", callback=callback
        )
        metadata = self.read_event(owner)["metadata"]
        self.assertEqual("armed", supervision.receipt(metadata)["phase"])
        owner.kill()
        owner.wait(timeout=10)
        self.assertEqual("stopped", self.receipt_until(metadata)["phase"])
        self.assertFalse(marker.exists())

    def test_failed_checkpoint_and_failed_initial_receipt_never_executes_provider(self):
        marker = self.root / "executed"
        command = [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')"]

        def failed(_):
            raise OSError("fixture checkpoint failed")

        with self.assertRaisesRegex(OSError, "checkpoint failed"):
            with supervision.launch(
                command, receipt_path=self.root / "receipt.json", timeout=30, checkpoint=failed, start_new_session=True
            ):
                self.fail("Failed checkpoint released provider")
        self.assertFalse(marker.exists())
        occupied = self.root / "occupied"
        occupied.mkdir()
        with self.assertRaises(supervision.SupervisionError):
            with supervision.launch(command, receipt_path=occupied, timeout=30, start_new_session=True):
                self.fail("Invalid receipt released provider")
        self.assertFalse(marker.exists())

    def test_keeper_death_causes_independent_scoped_cleanup(self):
        with self.assertRaises(supervision.SupervisionError):
            with supervision.launch(
                [sys.executable, "-c", "import signal; signal.pause()"],
                receipt_path=self.root / "receipt.json",
                timeout=30,
                start_new_session=True,
            ) as child:
                keeper = child.supervision["keeper"]
                os.kill(keeper["pid"], signal.SIGKILL)
                child.wait(timeout=15)
                self.assertIsNotNone(child.returncode)

    def test_keeper_death_before_release_never_executes_provider(self):
        marker = self.root / "executed"

        def lose_keeper(metadata):
            os.kill(metadata["keeper"]["pid"], signal.SIGKILL)
            limit = time.monotonic() + 10
            while processes.live_processes([metadata["keeper"]]) and time.monotonic() < limit:
                threading.Event().wait(0.02)

        with self.assertRaises(supervision.SupervisionError):
            with supervision.launch(
                [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
                receipt_path=self.root / "receipt.json",
                timeout=30,
                checkpoint=lose_keeper,
                start_new_session=True,
            ):
                self.fail("Dead keeper released provider")
        self.assertFalse(marker.exists())

    def test_blocked_checkpoint_does_not_disable_hard_deadline(self):
        marker = self.root / "executed"

        def blocked(metadata):
            self.receipt_until(metadata)

        with self.assertRaises(supervision.SupervisionError):
            with supervision.launch(
                [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
                receipt_path=self.root / "receipt.json",
                timeout=1,
                checkpoint=blocked,
                start_new_session=True,
            ):
                self.fail("Deadline-expired bootstrap was released")
        self.assertFalse(marker.exists())

    def test_keeper_refuses_missing_or_invalid_timing_before_admission(self):
        import autocode_supervision_keeper as keeper

        for timing in ({}, {"timing": None}, {"timing": {}}, {"timing": False}):
            with self.subTest(timing=timing):
                control_r, control_w = os.pipe()
                ack_r, ack_w = os.pipe()
                try:
                    packet = {"metadata": {}, "deadline": None, **timing}
                    os.write(control_w, (json.dumps(packet) + "\n").encode())
                    with patch.object(keeper, "KeeperTree") as trees:
                        self.assertEqual(2, keeper.main(control_r, ack_w))
                    trees.assert_not_called()
                    self.assertFalse(select.select([ack_r], [], [], 0)[0])
                finally:
                    for fd in (control_r, control_w, ack_r, ack_w):
                        os.close(fd)

    def test_keeper_admission_uses_native_birth_and_preserves_declared_identity(self):
        import autocode_supervision_keeper as keeper

        pid = os.getpid()
        raw_birth = 1760000000.9995
        declared = {
            "pid": pid,
            "group": pid,
            "birth_identity": raw_birth,
            "birth_time": raw_birth + 1.25,
            "started": "controller clock",
        }
        provider = {
            "pid": pid + 1,
            "group": pid + 1,
            "birth_identity": raw_birth,
            "birth_time": raw_birth,
            "started": "provider clock",
        }
        for changed_birth in (False, True):
            with self.subTest(changed_birth=changed_birth):
                observed = {
                    **declared,
                    "birth_time": raw_birth,
                    "started": "keeper clock",
                    "birth_identity": raw_birth + 1 if changed_birth else raw_birth,
                }
                metadata = {
                    "schema": 1,
                    "nonce": "a" * 32,
                    "owner": declared,
                    "keeper": declared,
                    "provider": provider,
                    "receipt": str(self.root / "admission.json"),
                    "started_at": "initial",
                }
                control_r, control_w = os.pipe()
                ack_r, ack_w = os.pipe()
                published = []
                try:
                    packet = {"metadata": metadata, "deadline": None, "timing": keeper_timing(None)}
                    os.write(control_w, (json.dumps(packet) + "\n").encode())
                    with (
                        patch.object(keeper.processes, "live_processes", return_value=[provider]),
                        patch.object(keeper.processes, "process_table", return_value={pid: observed}),
                        patch.object(keeper, "KeeperTree") as trees,
                        patch.object(
                            keeper.util,
                            "atomic_json",
                            side_effect=lambda path, value: published.append(json.loads(json.dumps(value))),
                        ),
                    ):
                        trees.return_value.sample.return_value = []
                        trees.return_value.inventory.return_value = [provider]
                        trees.return_value.admitted_sessions.return_value = []
                        code = keeper.main(control_r, ack_w)
                        if changed_birth:
                            self.assertEqual(2, code)
                            self.assertFalse(select.select([ack_r], [], [], 0)[0])
                            trees.assert_not_called()
                            self.assertEqual([], published)
                        else:
                            self.assertEqual(0, code)
                            self.assertTrue(select.select([ack_r], [], [], 0)[0])
                            self.assertEqual({"armed": metadata["nonce"]}, json.loads(os.read(ack_r, 4096)))
                            trees.assert_called_once()
                            trees.return_value.stop.assert_called_once()
                            self.assertEqual(["armed", "stopped"], [value["phase"] for value in published])
                            self.assertTrue(all(value["keeper"] == declared for value in published))
                            self.assertTrue(all(value["timing"] for value in published))
                            self.assertTrue(all("session_admissions" not in value for value in published))
                finally:
                    for fd in (control_r, control_w, ack_r, ack_w):
                        with contextlib.suppress(OSError):  # The admitted keeper closes its owned ends.
                            os.close(fd)

    def dead_provider_metadata(self):
        """Admission metadata whose provider already exited before arming.

        A readiness byte holds the child deterministically live (blocked on
        its stdin) while its identity is sampled, then the parent permits its
        exit and returns from waitid(WNOWAIT): the child is a confirmed
        zombie that is still unreaped, exactly like a bootstrap that died
        waiting for its launch to be admitted while its parent still owns the
        wait. Cleanup closes both pipe ends and reaps it even when sampling
        fails.
        """
        import autocode_supervision_keeper as keeper

        dead = subprocess.Popen(
            [sys.executable, "-c", "import os; os.write(1, b'R'); os.read(0, 1)"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            start_new_session=True,
        )
        self.addCleanup(dead.wait, timeout=10)
        self.addCleanup(dead.stdout.close)
        self.addCleanup(dead.stdin.close)  # a closed stdin releases a still-blocked child
        self.assertEqual(b"R", dead.stdout.read(1))
        provider = supervision._identity(dead.pid)
        dead.stdin.write(b"exit")
        dead.stdin.flush()
        os.waitid(os.P_PID, dead.pid, os.WEXITED | os.WNOWAIT)
        self.assertFalse(processes.live_processes([provider]))
        metadata = {
            "schema": 1,
            "nonce": "b" * 32,
            "owner": supervision._identity(os.getpid()),
            "keeper": supervision._identity(os.getpid()),
            "provider": provider,
            "receipt": str(self.root / "receipt.json"),
            "started_at": "initial",
        }
        return keeper, metadata

    @unittest.skipUnless(sys.platform == "linux", "requires the native provider session listener")
    def test_dead_provider_before_its_listener_transfer_names_the_failed_transfer(self):
        """A bootstrap that died without delivering its listener is refused by name.

        A nested provider listener attempt inside an already-bound provider dies
        in exactly this window: refused by the inherited filter, the bootstrap
        exits before the transfer, so the keeper must report the failed listener
        transfer itself rather than exiting before acknowledgement with no
        cause, whatever the race order between the two processes was.
        """
        keeper, metadata = self.dead_provider_metadata()
        import autocode_session_boundary as boundary

        channel, peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        peer.close()  # the dead bootstrap's end: no descriptor will ever arrive
        callback, callback_peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        control_r, control_w = os.pipe()
        ack_r, ack_w = os.pipe()
        try:
            packet = {
                "metadata": metadata,
                "deadline": None,
                "timing": keeper_timing(None),
                "cleanup_policy": "freeze_tree",
                "session_socket": channel.fileno(),
                "session_callback": callback.fileno(),
            }
            os.write(control_w, (json.dumps(packet) + "\n").encode())
            with patch.object(keeper.boundary, "guardian_harden"):
                with self.assertRaisesRegex(boundary.BoundaryError, "listener descriptor transfer"):
                    keeper.main(control_r, ack_w)
            self.assertFalse(select.select([ack_r], [], [], 0)[0])
            self.assertFalse((self.root / "receipt.json").exists())
        finally:
            for fd in (control_r, control_w, ack_r, ack_w):
                with contextlib.suppress(OSError):
                    os.close(fd)
            for sock in (channel, callback, callback_peer):
                with contextlib.suppress(OSError):  # the keeper closed the channel it received
                    sock.close()

    def test_dead_provider_refusal_names_its_cause_on_keeper_stderr(self):
        """A pre-admission provider death never leaves the keeper exit silent (#815)."""
        keeper, metadata = self.dead_provider_metadata()
        control_r, control_w = os.pipe()
        ack_r, ack_w = os.pipe()
        captured = io.StringIO()
        try:
            packet = {"metadata": metadata, "deadline": None, "timing": keeper_timing(None)}
            os.write(control_w, (json.dumps(packet) + "\n").encode())
            with contextlib.redirect_stderr(captured):
                self.assertEqual(2, keeper.main(control_r, ack_w))
            self.assertIn("Provider exited before keeper admission", captured.getvalue())
            self.assertFalse(select.select([ack_r], [], [], 0)[0])
            self.assertFalse((self.root / "receipt.json").exists())
        finally:
            for fd in (control_r, control_w, ack_r, ack_w):
                with contextlib.suppress(OSError):
                    os.close(fd)

    def test_nonce_or_identity_tampering_is_unknown(self):
        with supervision.launch(
            [sys.executable, "-c", "pass"], receipt_path=self.root / "receipt.json", timeout=30, start_new_session=True
        ) as child:
            child.wait(timeout=20)
        meta = child.supervision
        self.assertIsNone(supervision.receipt({**meta, "nonce": "0" * 32}))
        self.assertIsNone(supervision.receipt({**meta, "provider": {**meta["provider"], "birth_identity": -1}}))
        self.assertFalse(supervision.observe(meta)["provider"]["alive"])

    def test_no_fork_or_preexec_and_keeper_does_not_inherit_provider_lock(self):
        import fcntl

        lock = (self.root / "writer.lock").open("w")
        fcntl.flock(lock, fcntl.LOCK_EX)
        with patch.object(os, "fork", side_effect=AssertionError("threaded owner forked")):
            with supervision.launch(
                [sys.executable, "-c", "pass"],
                receipt_path=self.root / "receipt.json",
                timeout=30,
                start_new_session=True,
                pass_fds=(lock.fileno(),),
            ) as child:
                child.wait(timeout=20)
                lock.close()
                with (self.root / "writer.lock").open("w") as contender:
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.assertEqual(0, child.returncode)

    def test_term_resistant_detached_writer_is_stopped_on_owner_loss(self):
        provider = """import os,json,subprocess,sys,signal
worker = subprocess.Popen([sys.executable,'-u','-c',"import os,json,signal; signal.signal(signal.SIGTERM,signal.SIG_IGN); print(json.dumps({'worker':os.getpid()}),flush=True); signal.pause()"],start_new_session=True)
print(json.dumps({'provider':os.getpid()}),flush=True)
signal.pause()
"""
        owner = self.start_owner(provider)
        metadata = self.read_event(owner)["metadata"]
        events = [self.read_event(owner), self.read_event(owner)]
        worker_pid = next(row["worker"] for row in events if "worker" in row)
        worker_identity = owner._cleanup_scope["known"][worker_pid]
        # The worker's readiness event precedes the owner fault; the keeper
        # discovers its detached session independently at the fault boundary.
        owner.kill()
        owner.wait(timeout=10)
        value = self.receipt_until(metadata)
        self.assertEqual("stopped", value["phase"], value)
        self.assertIn(worker_pid, [row["pid"] for row in value["processes"]])
        self.assertFalse(processes.live_processes([worker_identity]))

    def test_owner_death_before_arm_never_executes_provider(self):
        marker = self.root / "executed"
        command = [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"]
        script = f"""import sys,json
sys.path.insert(0,{str(TOOLS)!r})
import autocode_supervision as guard
original = guard._send
def blocked(fd,value):
    print(json.dumps({{'metadata':value['metadata']}}),flush=True)
    sys.stdin.read()
    original(fd,value)
guard._send=blocked
with guard.launch({command!r},receipt_path={str(self.root / "receipt.json")!r},timeout=30,start_new_session=True):
    raise AssertionError('unarmed provider released')
"""
        owner = subprocess.Popen(
            [sys.executable, "-u", "-c", script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        self.addCleanup(self.stop_owner, owner)
        metadata = self.read_event(owner)["metadata"]
        owner.kill()
        owner.wait(timeout=10)
        # Bootstrap EOF is an observable process boundary, not a timing guess.
        until = time.monotonic() + 15
        while processes.live_processes([metadata["provider"]]) and time.monotonic() < until:
            threading.Event().wait(0.02)
        self.assertFalse(processes.live_processes([metadata["provider"]]))
        self.assertFalse(marker.exists())
        self.assertIsNone(supervision.receipt(metadata))

    def start_cli_guard(self, *, normal=False, exit_code=0, timeout=30):
        read_fd, write_fd = os.pipe()
        identity = processes.identity(processes.process_table({os.getpid()})[os.getpid()])
        nonce = "a" * 32
        after = f"raise SystemExit({exit_code})" if normal else "signal.pause()"
        script = f"""import sys,os,json,signal
from pathlib import Path
sys.path.insert(0,{str(TOOLS)!r})
import autocode_supervision as guard

def interrupted(*_):
    Path({str(self.root / "interrupted")!r}).write_text('retained')
    raise SystemExit(2)
signal.signal(signal.SIGTERM,interrupted)
with guard.protect_owner({read_fd},receipt_path={str(self.root / "cli-receipt.json")!r},timeout={timeout!r},
                         owner_identity={identity!r},nonce={nonce!r}) as meta:
    print(json.dumps({{'metadata':meta}}),flush=True)
    {after}
"""
        child = subprocess.Popen(
            [sys.executable, "-u", "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            pass_fds=(read_fd,),
        )
        os.close(read_fd)
        self.addCleanup(self.stop_owner, child)
        writer = os.fdopen(write_fd, "wb", buffering=0)
        self.addCleanup(writer.close)
        return child, writer

    def test_cli_guard_normal_discharge_preserves_success_and_failure_exit(self):
        for code in (0, 7):
            with self.subTest(code=code):
                path = self.root / "cli-receipt.json"
                if path.exists():
                    path.unlink()
                child, writer = self.start_cli_guard(normal=True, exit_code=code)
                metadata = self.read_event(child)["metadata"]
                child.wait(timeout=15)
                self.assertEqual(code, child.returncode, child.stderr.read())
                self.assertEqual("discharged", self.receipt_until(metadata)["phase"])
                self.assertFalse((self.root / "interrupted").exists())

    def test_cli_guard_discharge_receipt_retains_its_stop_phase_timing(self):
        # #1143: a slow discharge must be diagnosable from the keeper's own terminal receipt.
        child, writer = self.start_cli_guard(normal=True)
        metadata = self.read_event(child)["metadata"]
        child.wait(timeout=15)
        self.assertEqual(0, child.returncode, child.stderr.read())
        value = self.receipt_until(metadata)
        self.assertEqual(("discharged", "controller_finished"), (value["phase"], value["cause"]), value)
        timing = value["stop_timing"]
        final, last = timing["final_sample"], timing["last_sample"]
        # Marks are seconds before publication, in the order the discharge reaches them.
        marks = [timing["requested"], timing["loop_exit"], final["started"], final["finished"], 0]
        self.assertEqual(sorted(marks), marks, timing)
        if last is not None:  # None only when the finished message beat the loop's first sample
            self.assertLessEqual(last["started"], last["finished"], timing)
            self.assertLessEqual(last["finished"], timing["loop_exit"], timing)
        # The guarded CLI leads its own session, so the final sample scanned the host for its members.
        self.assertGreater(final["enumeration_seconds"], 0, timing)
        self.assertLessEqual(final["enumeration_seconds"], final["finished"] - final["started"] + 1e-5, timing)

    def test_cli_guard_eof_allows_interrupted_receipt_before_stopping(self):
        for timeout in (30, None):  # None: a Builder worker's lifeline has no deadline of its own (#454)
            with self.subTest(timeout=timeout):
                for path in (self.root / "cli-receipt.json", self.root / "interrupted"):
                    path.unlink(missing_ok=True)
                child, writer = self.start_cli_guard(timeout=timeout)
                metadata = self.read_event(child)["metadata"]
                writer.close()
                child.wait(timeout=15)
                self.assertEqual(2, child.returncode, child.stderr.read())
                self.assertEqual("retained", (self.root / "interrupted").read_text())
                value = self.receipt_until(metadata)
                self.assertEqual("owner_lost", value["cause"])
                self.assertEqual("stopped", value["phase"], value)

    def test_cli_guard_without_deadline_admits_nested_launch_then_discharges(self):
        read_fd, write_fd = os.pipe()
        identity = processes.identity(processes.process_table({os.getpid()})[os.getpid()])
        script = f"""import sys,json
sys.path.insert(0,{str(TOOLS)!r})
import autocode_supervision as guard
with guard.protect_owner({read_fd},receipt_path={str(self.root / "cli-receipt.json")!r},timeout=None,
                         owner_identity={identity!r},nonce={"a" * 32!r}) as meta:
    print(json.dumps({{'metadata':meta}}),flush=True)
    with guard.launch([sys.executable,'-c','pass'],receipt_path={str(self.root / "nested.json")!r},timeout=None,
                      start_new_session=True) as child:
        child.wait(timeout=10)
    print(json.dumps({{'nested':child.supervision}}),flush=True)
"""
        child = subprocess.Popen(
            [sys.executable, "-u", "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            pass_fds=(read_fd,),
        )
        os.close(read_fd)
        self.addCleanup(self.stop_owner, child)
        writer = os.fdopen(write_fd, "wb", buffering=0)
        self.addCleanup(writer.close)
        metadata = self.read_event(child)["metadata"]
        nested = self.read_event(child)["nested"]
        child.wait(timeout=15)
        self.assertEqual(0, child.returncode, child.stderr.read())
        value = self.receipt_until(metadata)
        self.assertEqual(
            ("discharged", "controller_finished", None), (value["phase"], value["cause"], value["cleanup_error"])
        )
        # The nested pair was admitted before its provider could exec, with no owner deadline to bound it.
        for role in ("keeper", "provider"):
            self.assertTrue(any(processes.matches(nested[role], row) for row in value["processes"]), role)
        with self.assertRaisesRegex(supervision.SupervisionError, "expired"):
            with supervision.protect_owner(
                0, receipt_path=self.root / "unused.json", timeout=0, owner_identity=metadata["owner"], nonce="b" * 32
            ):
                pass

    def test_pid_zero_never_establishes_ownership_or_signals_a_process_group(self):
        row = {"pid": 0, "group": 0, "birth_identity": 1.0, "started": "kernel"}
        with (
            patch.object(processes, "process_ids", return_value=[0, -1]),
            patch.object(processes.psutil, "Process", side_effect=AssertionError("Inspected nonpositive PID")),
        ):
            self.assertEqual({}, processes.process_table())
        tree = processes.ProcessTree(999, lambda rows: None)
        with (
            patch.object(processes, "process_table", return_value={0: row}) as inspect,
            patch.object(processes.os, "kill", side_effect=AssertionError("Signalled PID zero")),
        ):
            tree.signal([row], signal.SIGKILL)
            inspect.assert_called_once_with(set())

    def test_exact_keeper_identity_is_excluded_before_group_expansion(self):
        root = {"pid": 101, "parent": 99, "group": 101, "birth_identity": 1.0, "started": "root", "state": "sleeping"}
        keeper = {
            "pid": 102,
            "parent": 101,
            "group": 102,
            "birth_identity": 2.0,
            "started": "keeper",
            "state": "sleeping",
        }

        class Process:
            def __init__(self, pid):
                self.pid = pid
                self._ident = (pid, 1.0 if pid == 101 else 2.0)

            def create_time(self):
                return self._ident[1]

        tree = processes.ProcessTree(101, lambda rows: None, excluded=(processes.identity(keeper),))
        groups = []

        def table(pids):
            return {pid: dict(row) for pid, row in ((101, root), (102, keeper)) if pid in pids}

        def group(pid):
            groups.append(pid)
            return 102

        with (
            patch.object(processes, "process_table", side_effect=table),
            patch.object(processes.psutil, "Process", Process),
            patch.object(processes.process_children, "descendants", return_value=[Process(102)]),
            patch.object(processes, "process_ids", return_value=[101, 102, 0]),
            patch.object(processes.os, "getpgid", side_effect=group),
        ):
            rows = tree.sample()
        self.assertEqual([101], [row["pid"] for row in rows])
        self.assertNotIn(102, tree.known)
        self.assertNotIn(0, groups)
        # Exclusion is bound to birth identity; it cannot hide a reused PID.
        reused = {**keeper, "birth_identity": 3.0}
        self.assertTrue(tree._included(reused))

    def test_retained_group_owns_a_child_reparented_before_any_sample(self):
        # A guarded CLI leads its own session. Killed and reaped before its keeper
        # samples a new child, it leaves that child reparented; only the child's
        # group still ties it to the recorded leader.
        from autocode_supervision_keeper import Observer

        worker = "import signal; print(flush=True); signal.pause()"
        script = (
            f"import subprocess,sys,signal; c=subprocess.Popen([sys.executable,'-c',{worker!r}],"
            "stdout=subprocess.PIPE); c.stdout.readline(); print(c.pid,flush=True); signal.pause()"
        )
        leader = subprocess.Popen(
            [sys.executable, "-c", script], stdout=subprocess.PIPE, text=True, start_new_session=True
        )

        def stop_leader():
            if leader.poll() is None:
                leader.kill()
            leader.wait(timeout=10)
            leader.stdout.close()

        self.addCleanup(stop_leader)
        root = processes.identity(processes.process_table({leader.pid})[leader.pid])
        orphan_pid = int(leader.stdout.readline())
        orphan = processes.identity(processes.process_table({orphan_pid})[orphan_pid])
        self.addCleanup(self.cleanup_identity, orphan)
        leader.kill()
        leader.wait(timeout=10)
        self.assertNotEqual(leader.pid, processes.process_table({orphan_pid})[orphan_pid]["parent"])
        self.assertEqual(leader.pid, orphan["group"])
        unretained = processes.ProcessTree(leader.pid, lambda rows: None)
        unretained.known = {leader.pid: root}
        self.assertEqual([], unretained.sample(notify=False))
        tree = processes.ProcessTree(leader.pid, lambda rows: None, groups=(root,))
        tree.known = {leader.pid: root}
        self.assertEqual([orphan_pid], [row["pid"] for row in tree.sample(notify=False)])
        tree.stop(Observer(root))
        self.assertFalse(processes.live_processes([orphan]))

    def provider_reparented_before_keeper_sample(self, *, lose_keeper, caller_error=False, change_group=False):
        # Hold the keeper's first ancestry sample until a provider's child has
        # been witnessed natively, its parent has exited and the caller has
        # reaped that parent. No observer can discover the child by ancestry.
        gate_r, gate_w = os.pipe()
        entered_r, entered_w = os.pipe()

        def close_barriers():
            for fd in (gate_r, gate_w, entered_r, entered_w):
                with contextlib.suppress(OSError):
                    os.close(fd)

        self.addCleanup(close_barriers)
        wrapper = self.root / "first-sample-keeper.py"
        wrapper.write_text(f"""import os, select, sys
sys.path.insert(0, {str(TOOLS)!r})
import autocode_supervision_keeper as keeper
original = keeper.KeeperTree.sample
def sample(self, *args, **kwargs):
    keeper.KeeperTree.sample = original
    os.write({entered_w}, b'R')
    os.close({entered_w})
    if not select.select([{gate_r}], [], [], 10)[0] or os.read({gate_r}, 1) != b'G':
        raise RuntimeError('first-sample barrier was not released')
    os.close({gate_r})
    return original(self, *args, **kwargs)
keeper.KeeperTree.sample = sample
raise SystemExit(keeper.main(int(sys.argv[1]), int(sys.argv[2])))
""")
        provider = r"""import json, subprocess, sys
worker = subprocess.Popen([sys.executable, '-u', '-c',
    "import os, signal; os.write(1,b'worker-ready\\n'); signal.pause()"], stdout=subprocess.PIPE)
if worker.stdout.readline() != b'worker-ready\n':
    raise RuntimeError('child did not reach its ready barrier')
print(json.dumps({'worker': worker.pid}), flush=True)
if sys.stdin.buffer.read(1) != b'X':
    raise RuntimeError('provider exit barrier was not released')
"""
        if change_group:
            provider = provider.replace("import os, signal;", "import os, signal; os.setpgid(0,0);")
        real_popen = subprocess.Popen

        def launch(command, **options):
            if len(command) > 2 and Path(command[2]).name == "autocode_supervision_keeper.py":
                command = [command[0], "-E", str(wrapper), *command[3:]]
                options["pass_fds"] += (gate_r, entered_w)
            return real_popen(command, **options)

        sentinel = real_popen([sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True)
        self.addCleanup(self.stop_owner, sentinel)
        sentinel_row = processes.identity(processes.process_table({sentinel.pid})[sentinel.pid])

        def reach_fault(child):
            self.assertTrue(select.select([entered_r], [], [], 10)[0], "Keeper did not reach first sample")
            self.assertEqual(b"R", os.read(entered_r, 1))
            worker_pid = self.read_event(child)["worker"]
            table = processes.process_table({worker_pid, child.pid})
            self.assertTrue(processes.matches(child.supervision["provider"], table.get(child.pid)))
            self.assertEqual(child.pid, table[worker_pid]["parent"])
            worker = processes.identity(table[worker_pid])
            self.addCleanup(self.cleanup_identity, worker)
            child.stdin.write(b"X")
            child.stdin.flush()
            self.assertEqual(0, child.wait(timeout=10))
            reparented = processes.process_table({worker_pid})[worker_pid]
            self.assertNotEqual(child.pid, reparented["parent"])
            self.assertEqual(worker_pid if change_group else child.pid, reparented["group"])
            self.assertEqual(child.pid, os.getsid(worker_pid))
            if lose_keeper:
                # The controller's independent keeper-loss fallback must keep
                # the same group ownership even without a descendant receipt.
                self.cleanup_identity(child.supervision["keeper"])
            else:
                os.write(gate_w, b"G")
            return worker

        with patch.object(supervision.subprocess, "Popen", side_effect=launch):
            if caller_error:
                expected = self.assertRaisesRegex(RuntimeError, "caller stopped after the fault")
            else:
                expected = self.assertRaises(supervision.SupervisionError) if lose_keeper else contextlib.nullcontext()
            with expected:
                with supervision.launch(
                    [sys.executable, "-u", "-c", provider],
                    receipt_path=self.root / "group-receipt.json",
                    timeout=20,
                    start_new_session=True,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                ) as child:
                    self.addCleanup(self.stop_owner, child)
                    worker = reach_fault(child)
                    if caller_error:
                        raise RuntimeError("caller stopped after the fault")
            self.assertFalse(processes.live_processes([worker]), "Reparented owned child survived cleanup")
            self.assertTrue(processes.live_processes([sentinel_row]), "Cleanup touched an unrelated sentinel")
            self.assertEqual(0, child.returncode, "A keeper must not manufacture the provider's exit")
            if not lose_keeper:
                final = child.supervision_result
                self.assertEqual("stopped", final["phase"])
                self.assertIsNone(final["cleanup_error"])
                self.assertTrue(any(processes.matches(worker, row) for row in final["processes"]))

    def test_provider_group_keeps_child_reparented_before_first_keeper_sample(self):
        self.provider_reparented_before_keeper_sample(lose_keeper=False)

    def test_keeper_loss_fallback_keeps_reparented_provider_group(self):
        self.provider_reparented_before_keeper_sample(lose_keeper=True)

    def test_keeper_loss_during_abnormal_context_exit_stops_reparented_group(self):
        self.provider_reparented_before_keeper_sample(lose_keeper=True, caller_error=True)

    def test_provider_session_keeps_unseen_reparented_child_in_a_new_group(self):
        self.provider_reparented_before_keeper_sample(lose_keeper=False, change_group=True)

    def test_keeper_loss_fallback_keeps_unseen_reparented_session_member(self):
        self.provider_reparented_before_keeper_sample(lose_keeper=True, change_group=True)

    def test_a_reused_or_emptied_leader_group_ends_retained_ownership(self):
        leader = {"pid": 4242, "group": 4242, "birth_identity": 1.0, "birth_time": 1.0, "started": "leader"}
        member = {
            "pid": 4243,
            "parent": 1,
            "group": 4242,
            "birth_identity": 3.0,
            "birth_time": 3.0,
            "started": "member",
            "state": "sleeping",
        }
        reused = {**leader, "parent": 1, "birth_identity": 2.0, "birth_time": 2.0, "state": "sleeping"}
        stranger = {**member, "pid": 4250, "birth_identity": 9.0, "birth_time": 9.0, "started": "stranger"}

        def sample(tree, *rows):
            current = {row["pid"]: row for row in rows}

            def killpg(group, sig):
                self.assertEqual(0, sig)
                if not any(row["group"] == group for row in rows):
                    raise ProcessLookupError(group)

            with (
                patch.object(
                    processes,
                    "process_table",
                    side_effect=lambda pids: {pid: dict(current[pid]) for pid in pids if pid in current},
                ),
                patch.object(processes, "process_ids", return_value=list(current)),
                patch.object(processes.os, "getpgid", side_effect=lambda pid: current[pid]["group"]),
                patch.object(processes.os, "killpg", side_effect=killpg),
            ):
                return [row["pid"] for row in tree.sample(notify=False)]

        def tree():
            retained = processes.ProcessTree(4242, lambda rows: None, groups=(leader,))
            retained.known = {4242: leader}
            return retained

        self.assertEqual([4243], sample(tree(), member))
        # A different process at the leader's PID proves the old group emptied
        # first. Its new group is never claimed, even after that process exits.
        reused_tree = tree()
        self.assertEqual([], sample(reused_tree, reused, member))
        self.assertEqual([], sample(reused_tree, member))
        # A sample that finds the group empty ends ownership too: from then on the
        # kernel may give the PID to a new group leader, which can start a child
        # and exit before the next sample. That child is not claimed.
        emptied = tree()
        self.assertEqual([], sample(emptied))
        self.assertEqual([], sample(emptied, stranger))

    def session_tree_case(self, *, rows=(), snapshots=None, denied=False, excluded=()):
        leader = {"pid": 4242, "session": 4242, "group": 4242, "birth_identity": 1.0, "started": "leader"}
        tree = processes.ProcessTree(4242, lambda rows: None, sessions=(leader,), excluded=excluded)
        tree.known = {4242: leader}
        current = {row["pid"]: row for row in rows}
        native_sessions = {pid: row["session"] for pid, row in current.items()}
        if denied:
            sid = lambda pid: (_ for _ in ()).throw(PermissionError("session inspection denied"))
        else:
            sid = lambda pid: native_sessions[pid]
        contexts = contextlib.ExitStack()
        self.addCleanup(contexts.close)
        contexts.enter_context(
            patch.object(
                processes,
                "process_table",
                side_effect=lambda pids: {pid: current[pid] for pid in pids if pid in current},
            )
        )
        contexts.enter_context(patch.object(processes, "process_ids", side_effect=snapshots or (lambda: list(current))))
        contexts.enter_context(patch.object(processes.os, "getsid", side_effect=sid))
        contexts.enter_context(patch.object(processes.os, "getpgid", side_effect=lambda pid: current[pid]["group"]))
        return tree, current, native_sessions

    def test_reused_or_stably_empty_session_ends_ownership(self):
        member = {
            "pid": 4243,
            "parent": 1,
            "session": 4242,
            "group": 4243,
            "birth_identity": 3.0,
            "state": "sleeping",
            "started": "member",
        }
        tree, current, _ = self.session_tree_case(rows=(member,))
        self.assertEqual([4243], [row["pid"] for row in tree.sample(notify=False)])
        # An exited session and a different leader birth cannot authorize its
        # later members, even if the same numeric SID is reused.
        self.assertEqual({4242}, set(tree.sessions))
        current.clear()
        self.assertEqual([], tree.sample(notify=False))
        self.assertEqual({}, tree.sessions)
        reused = {**member, "pid": 4242, "group": 4242, "birth_identity": 8.0}
        reused_tree, _, _ = self.session_tree_case(rows=(reused, member))
        self.assertEqual([], reused_tree.sample(notify=False))
        self.assertEqual({}, reused_tree.sessions)

    def test_changing_pid_snapshots_do_not_retire_an_empty_session(self):
        tree, _, _ = self.session_tree_case(snapshots=[[], [11], [], [12], [], [13]])
        with self.assertRaisesRegex(processes.ProcessError, "Process set changed"):
            tree.sample(notify=False)
        self.assertEqual({4242}, set(tree.sessions), "Uncertain enumeration discarded admitted session ownership")

    def test_disappearing_session_candidate_requires_a_stable_fresh_empty_scan(self):
        member = {
            "pid": 4243,
            "parent": 1,
            "session": 4242,
            "group": 4243,
            "birth_identity": 3.0,
            "state": "sleeping",
            "started": "member",
        }
        tree, _, _ = self.session_tree_case(rows=(member,), snapshots=[[4243], [4244]] * 3)
        # getsid saw the old member, then it forked a new member and exited
        # before native birth metadata could verify it. The fresh empty table
        # must not skip the before/after membership guard and report clean.
        with patch.object(processes, "process_table", return_value={}):
            with self.assertRaisesRegex(processes.ProcessError, "Process set changed"):
                tree.sample(notify=False)
        self.assertEqual({4242}, set(tree.sessions))

    def test_denied_session_inspection_does_not_publish_empty_ownership(self):
        member = {
            "pid": 4243,
            "parent": 1,
            "session": 4242,
            "group": 4243,
            "birth_identity": 3.0,
            "state": "sleeping",
            "started": "member",
        }
        tree, _, _ = self.session_tree_case(rows=(member,), denied=True)
        with self.assertRaisesRegex(processes.ProcessError, "cleanup remains unknown"):
            tree.sample(notify=False)
        self.assertEqual({4242}, set(tree.sessions))

    def test_session_candidates_require_fresh_membership_and_exclude_exact_keeper(self):
        keeper = {
            "pid": 4243,
            "parent": 1,
            "session": 4242,
            "group": 4243,
            "birth_identity": 3.0,
            "state": "sleeping",
            "started": "keeper",
        }
        moved = {**keeper, "pid": 4244, "group": 4244, "session": 4999, "started": "foreign-session"}
        tree, _, native_sessions = self.session_tree_case(rows=(keeper, moved), excluded=(keeper,))
        native_sessions[4244] = 4242  # candidate lookup preceded its new-session transition
        self.assertEqual([], tree.sample(notify=False))
        self.assertNotIn(4243, tree.known)
        self.assertNotIn(4244, tree.known, "A stale getsid candidate authorized changed native membership")

    def test_historical_identity_without_sid_cannot_enable_session_ownership(self):
        leader = {"pid": 4242, "group": 4242, "birth_identity": 1.0, "started": "leader"}
        tree = processes.ProcessTree(4242, lambda rows: None, sessions=(leader,))
        self.assertEqual({}, tree.sessions)
        for invalid in (True, 4242.0, 0):
            tree = processes.ProcessTree(4242, lambda rows: None, sessions=({**leader, "session": invalid},))
            self.assertEqual({}, tree.sessions)

    def keeper_publication_case(self, *, deadline_fault):
        # A blocked sampling write and an independent trigger compete for the
        # same receipt. Event barriers drive the ordering; the fake timer fires
        # the hard escalation while that write remains blocked.
        from copy import deepcopy

        import autocode_supervision_keeper as keeper_module

        blocked = threading.Event()
        release = threading.Event()
        killed = threading.Event()
        published = []
        writes = [0]
        alive = [True]
        clock = [0.0]
        wall_clock = [2_000_000_000.0]
        outcome = []
        errors = []
        provider = {"pid": 901, "group": 901, "birth_identity": 1.0, "birth_time": 1.0, "started": "provider"}
        guardian = {
            "pid": os.getpid(),
            "group": os.getpid(),
            "birth_identity": 2.0,
            "birth_time": 2.0,
            "started": "keeper",
        }
        worker = {"pid": 902, "group": 902, "birth_identity": 3.0, "birth_time": 3.0, "started": "worker"}
        metadata = {
            "schema": 1,
            "nonce": "c" * 32,
            "owner": guardian,
            "keeper": guardian,
            "provider": provider,
            "receipt": str(self.root / "publication.json"),
            "started_at": "fixture",
        }

        class Tree:
            def __init__(self, *args, **kwargs):
                self.known = {}

            def admitted_sessions(self):
                return []

            def inventory(self):
                return list(self.known.values())

            def sample(self, **kwargs):
                self.known[worker["pid"]] = worker
                return list(self.known.values()) if alive[0] else []

            def signal(self, rows, sig):
                if sig == signal.SIGKILL:
                    alive[0] = False
                    killed.set()

            def stop(self, observer):
                alive[0] = False

        class Timer:
            def __init__(self, interval, callback, args=()):
                self.callback, self.args = callback, args
                self.daemon = False

            def start(self):
                self.callback(*self.args)

            def cancel(self):
                pass

        def atomic(path, value):
            snapshot = deepcopy(value)
            writes[0] += 1
            if writes[0] == 2:
                blocked.set()
                if not release.wait(5):
                    raise AssertionError("Sampling receipt was not released")
            published.append(snapshot)

        control_r, control_w = os.pipe()
        ack_r, ack_w = os.pipe()

        def run():
            try:
                outcome.append(keeper_module.main(control_r, ack_w))
            except BaseException as error:
                errors.append(error)

        try:
            with (
                patch.object(keeper_module, "KeeperTree", Tree),
                patch.object(keeper_module.processes, "process_table", return_value={os.getpid(): guardian}),
                patch.object(
                    keeper_module.processes,
                    "live_processes",
                    side_effect=lambda rows: [row for row in rows if row == guardian or row == provider and alive[0]],
                ),
                patch.object(keeper_module.util, "atomic_json", side_effect=atomic),
                patch.object(keeper_module.threading, "Timer", Timer),
                patch.object(keeper_module.time, "monotonic", side_effect=lambda: clock[0]),
                patch.object(keeper_module.time, "time", side_effect=lambda: wall_clock[0]),
            ):
                thread = threading.Thread(target=run)
                thread.start()
                deadline = 5 if deadline_fault else None
                packet = {
                    "metadata": metadata,
                    "deadline": deadline,
                    "timing": keeper_timing(deadline),
                    "cleanup_policy": "freeze_tree",
                }
                os.write(control_w, (json.dumps(packet) + "\n").encode())
                self.assertTrue(blocked.wait(5), "Sampling receipt did not reach its barrier")
                if deadline_fault:
                    # A host sleep advances wall time only; the existing monotonic deadline still triggers.
                    wall_clock[0] += 1000.0
                    clock[0] = 10.0
                else:
                    os.close(control_w)
                    control_w = None
                self.assertTrue(killed.wait(5), "A blocked receipt write prevented hard cleanup")
                self.assertFalse(alive[0])
                self.assertFalse(release.is_set())
                # The trigger cannot publish past the older locked write.
                self.assertEqual(["armed"], [row["phase"] for row in published])
                release.set()
                thread.join(5)
                self.assertFalse(thread.is_alive())
                self.assertEqual([], errors)
                self.assertEqual([0], outcome)
                phases = [row["phase"] for row in published]
                self.assertIn("stopping", phases)
                self.assertNotIn("armed", phases[phases.index("stopping") :])
                self.assertEqual("stopped", published[-1]["phase"])
                self.assertEqual("stage_deadline" if deadline_fault else "owner_lost", published[-1]["cause"])
                self.assertIs(published[-1]["timing"]["likely_host_or_vm_suspension"], deadline_fault)
        finally:
            release.set()
            if control_w is not None:
                os.close(control_w)
            os.close(ack_r)

    def test_receipt_publications_cannot_revert_stopping_to_armed(self):
        self.keeper_publication_case(deadline_fault=False)

    def test_blocked_receipt_writer_cannot_block_independent_deadline_escalation(self):
        self.keeper_publication_case(deadline_fault=True)

    def keeper_with_a_blocked_watcher(self, name, woken):
        """Run the keeper to a clean provider stop while its lifeline watcher cannot be scheduled.

        The watcher is held inside its select until ``release`` is set; ``woken`` then decides what that
        select does. Returns once the keeper has returned and published, with the watcher still held.
        """
        import autocode_supervision_keeper as keeper_module

        blocked = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        alive = [True]
        outcome = []
        errors = []
        watchers = []
        run_thread = []
        provider = {"pid": 903, "group": 903, "birth_identity": 1.0, "birth_time": 1.0, "started": "provider"}
        guardian = {"pid": os.getpid(), "group": os.getpid(), "birth_identity": 2.0, "birth_time": 2.0, "started": "k"}
        receipt = self.root / f"{name}.json"
        metadata = {
            "schema": 1,
            "nonce": "e" * 32,
            "owner": guardian,
            "keeper": guardian,
            "provider": provider,
            "receipt": str(receipt),
            "started_at": "fixture",
        }

        class Tree:
            def __init__(self, *args, **kwargs):
                self.known = {provider["pid"]: provider}

            def admitted_sessions(self):
                return []

            def inventory(self):
                return list(self.known.values())

            def sample(self, **kwargs):
                # The provider is found gone only once the watcher is held inside its select.
                if alive[0] and not blocked.wait(5):
                    raise AssertionError("The lifeline watcher never reached its select")
                alive[0] = False
                return []

            def signal(self, rows, sig):
                raise AssertionError(f"A clean stop signalled {sig}")

            def stop(self, observer):
                alive[0] = False

        real_select = select.select

        def held_select(rlist, wlist, xlist, timeout=None):
            if threading.current_thread() is run_thread[0]:
                return real_select(rlist, wlist, xlist, timeout)
            watchers.append(threading.current_thread())
            blocked.set()
            release.wait(10)  # well past the keeper's 1 s join bound
            return woken()

        control_r, control_w = os.pipe()
        ack_r, ack_w = os.pipe()

        def run():
            run_thread.append(threading.current_thread())
            try:
                outcome.append(keeper_module.main(control_r, ack_w))
            except BaseException as error:
                errors.append(error)

        try:
            with (
                patch.object(keeper_module, "KeeperTree", Tree),
                patch.object(keeper_module.processes, "process_table", return_value={os.getpid(): guardian}),
                patch.object(
                    keeper_module.processes,
                    "live_processes",
                    side_effect=lambda rows: [row for row in rows if row == guardian or row == provider and alive[0]],
                ),
                patch.object(keeper_module.select, "select", held_select),
            ):
                thread = threading.Thread(target=run)
                thread.start()
                packet = {
                    "metadata": metadata,
                    "deadline": None,
                    "timing": keeper_timing(None),
                    "cleanup_policy": "freeze_tree",
                }
                os.write(control_w, (json.dumps(packet) + "\n").encode())
                thread.join(15)
                self.assertFalse(thread.is_alive(), "The keeper waited for its unscheduled watcher")
                self.assertEqual([], errors)
                return SimpleNamespace(
                    final=json.loads(receipt.read_text()), outcome=outcome, watchers=watchers, release=release
                )
        finally:
            os.close(control_w)
            os.close(ack_r)

    def test_unscheduled_watcher_does_not_make_a_clean_provider_stop_uncertain(self):
        # The provider exits 0 while the lifeline watcher thread cannot be scheduled for longer than
        # the keeper's join bound (a loaded host). The published phase depends on the cleanup, which
        # was verified, not on thread liveness: no trigger is in flight and none can start (#454).
        run = self.keeper_with_a_blocked_watcher("clean-stop", lambda: ([], [], []))
        self.assertEqual(
            ("stopped", "provider_stopped", None),
            (run.final["phase"], run.final["cause"], run.final["cleanup_error"]),
            run.final,
        )
        self.assertEqual([0], run.outcome)

    def test_a_watcher_reaching_its_trigger_after_publication_ends_without_changing_it(self):
        # The published keeper must not keep a late trigger blocked on its lock for good (#454).
        def fails():
            raise OSError("lifeline select failed")

        run = self.keeper_with_a_blocked_watcher("late-trigger", fails)
        run.release.set()
        (watcher,) = run.watchers
        watcher.join(5)
        self.assertFalse(watcher.is_alive(), "A late trigger stayed blocked on the published keeper's lock")
        self.assertEqual(run.final, json.loads(Path(run.final["receipt"]).read_text()))

    def test_publication_reports_what_the_trigger_it_waited_for_failed_to_do(self):
        # Owner loss arrives as the provider is found gone, and the trigger's signal batch is still running
        # when the keeper reaches its publication: it waits for the trigger, so what that trigger recorded
        # in the meantime reaches the receipt. A cleanup that partly failed is never published clean (#454).
        import autocode_supervision_keeper as keeper_module

        provider = {"pid": 903, "group": 903, "birth_identity": 1.0, "birth_time": 1.0, "started": "provider"}
        guardian = {"pid": os.getpid(), "group": os.getpid(), "birth_identity": 2.0, "birth_time": 2.0, "started": "k"}
        for policy in ("freeze_tree", "interrupt_root"):
            with self.subTest(policy=policy):
                receipt = self.root / f"{policy}-failed-signal.json"
                metadata = {
                    "schema": 1,
                    "nonce": "e" * 32,
                    "owner": guardian,
                    "keeper": guardian,
                    "provider": provider,
                    "receipt": str(receipt),
                    "started_at": "fixture",
                }
                signalling = threading.Event()
                alive = [True]
                outcome = []
                errors = []

                class Tree:
                    def __init__(self, *args, **kwargs):
                        self.known = {provider["pid"]: provider}

                    def admitted_sessions(self):
                        return []

                    def inventory(self):
                        return list(self.known.values())

                    def sample(self, **kwargs):
                        # The provider is found gone once the owner-loss trigger is signalling it.
                        if alive[0] and not signalling.wait(5):
                            raise AssertionError("The owner-loss trigger never signalled")
                        alive[0] = False
                        return []

                    def signal(self, rows, sig):
                        signalling.set()
                        time.sleep(0.5)  # the batch is still running when the keeper reaches publication
                        raise processes.ProcessError("Cannot signal owned process 903: permission denied")

                    def stop(self, observer):
                        alive[0] = False

                control_r, control_w = os.pipe()
                ack_r, ack_w = os.pipe()

                def run():
                    try:
                        outcome.append(keeper_module.main(control_r, ack_w))
                    except BaseException as error:
                        errors.append(error)

                try:
                    with (
                        patch.object(keeper_module, "KeeperTree", Tree),
                        patch.object(keeper_module.processes, "process_table", return_value={os.getpid(): guardian}),
                        patch.object(
                            keeper_module.processes,
                            "live_processes",
                            side_effect=lambda rows: [
                                row for row in rows if row == guardian or row == provider and alive[0]
                            ],
                        ),
                    ):
                        thread = threading.Thread(target=run)
                        thread.start()
                        packet = {
                            "metadata": metadata,
                            "deadline": None,
                            "timing": keeper_timing(None),
                            "cleanup_policy": policy,
                        }
                        os.write(control_w, (json.dumps(packet) + "\n").encode())
                        self.assertTrue(select.select([ack_r], [], [], 15)[0], "The keeper never armed")
                        os.close(control_w)  # owner EOF
                        control_w = None
                        thread.join(15)
                        self.assertFalse(thread.is_alive(), "The keeper never published")
                    self.assertEqual([], errors)
                    final = json.loads(receipt.read_text())
                    self.assertEqual("owner_lost", final["cause"], final)
                    self.assertIn("ProcessError", final["cleanup_error"] or "", final)
                    self.assertNotEqual("discharged", final["phase"], final)
                    self.assertEqual([4], outcome)
                finally:
                    if control_w is not None:
                        os.close(control_w)
                    os.close(ack_r)

    def test_protect_owner_accepts_the_declared_null_pair_and_refuses_half_of_it(self):
        # timeout and deadline both None is the one unbounded guard (a Builder worker's lifeline, #454);
        # every other input keeps the finite validation of #622, and so does the kill window.
        identity = processes.identity(processes.process_table({os.getpid()})[os.getpid()])
        for kwargs, message in (
            ({"timeout": None, "deadline": 5.0}, "deadline is invalid"),
            ({"timeout": 5, "deadline": float("nan")}, "deadline is invalid"),
            ({"timeout": 0, "deadline": None}, "deadline has expired"),
            ({"timeout": 5, "deadline": None, "kill_after": 0}, "kill window is invalid"),
            ({"timeout": None, "deadline": None, "kill_after": True}, "kill window is invalid"),
            ({"timeout": None, "deadline": None, "kill_after": float("inf")}, "kill window is invalid"),
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(supervision.SupervisionError, message):
                    with supervision.protect_owner(
                        0, receipt_path=self.root / "unused.json", owner_identity=identity, nonce="b" * 32, **kwargs
                    ):
                        pass
        self.assertFalse((self.root / "unused.json").exists())


class HeldKeeperAdmissionDiagnosticTests(unittest.TestCase):
    """Pure held-handle faults; no real launch, receipt I/O or native authority."""

    def fixture(self, *, metadata_changed=False, keeper_pid_changed=False, keeper_exit=None, errors=()):
        trace = []
        metadata = {
            "schema": 1,
            "nonce": "controlled-original-admission",
            "keeper": {"pid": 200, "birth_identity": 2.0},
            "provider": {"pid": 300, "birth_identity": 3.0},
        }

        class Child(subprocess.Popen):
            def __init__(self):
                # This controlled object never invokes Popen or owns a child.
                self._child_created = False
                self.args = ["controlled-outer-bootstrap"]
                self.supervision = json.loads(json.dumps(metadata))
                if metadata_changed:
                    self.supervision["nonce"] = "different-admission"

            @property
            def supervision_errors(self):
                trace.append("supervision_errors")
                return list(errors)

        class Keeper:
            @property
            def pid(self):
                trace.append("keeper_pid")
                return 201 if keeper_pid_changed else 200

            def poll(self):
                trace.append("keeper_poll")
                return keeper_exit

        child = Child()
        envelope = object.__new__(supervision._LaunchEnvelope)
        envelope._child, envelope._keeper, envelope._pid = child, Keeper(), 100
        envelope._active = envelope._released = True
        envelope._command = ("controlled-target",)
        envelope._bootstrap = tuple(child.args)
        envelope._metadata = json.dumps(metadata, sort_keys=True)
        envelope._options = json.dumps({"environment_sha256": "e" * 64}, sort_keys=True)
        envelope._pins = {"controlled-bootstrap-source": "a" * 64}
        envelope._release = {"fd": 3, "device": 1, "inode": 2}
        child._autocode_launch_envelope = envelope
        return envelope, child, metadata, trace

    def test_first_refusal_cause_stops_before_later_guards_without_extra_poll(self):
        prefix = "Held launch envelope: original keeper admission changed: "
        cases = (
            (
                {"metadata_changed": True, "keeper_pid_changed": True, "keeper_exit": 0, "errors": ["fault"]},
                "supervision metadata differs",
                [],
            ),
            (
                {"keeper_pid_changed": True, "keeper_exit": 0, "errors": ["fault"]},
                "keeper PID differs",
                ["keeper_pid"],
            ),
            (
                {"keeper_exit": 0, "errors": ["fault"]},
                "keeper process stopped",
                ["keeper_pid", "keeper_poll"],
            ),
            (
                {"errors": ["fault"]},
                "supervision errors reported",
                ["keeper_pid", "keeper_poll", "supervision_errors"],
            ),
        )
        for faults, reason, expected_trace in cases:
            with self.subTest(reason=reason):
                envelope, child, metadata, trace = self.fixture(**faults)
                with (
                    patch.object(supervision.os, "getpid", return_value=100),
                    patch.object(
                        supervision.util, "file_hash", side_effect=AssertionError("later source guard reached")
                    ) as source,
                    patch.object(
                        supervision, "receipt", side_effect=AssertionError("later receipt guard reached")
                    ) as read,
                    patch.object(
                        supervision.processes, "live_processes", side_effect=AssertionError("later birth guard reached")
                    ) as live,
                ):
                    with self.assertRaises(supervision.SupervisionError) as caught:
                        envelope.authenticate(child, command=["controlled-target"], environment_sha256="e" * 64)
                    self.assertEqual(prefix + reason, str(caught.exception))
                    self.assertEqual(expected_trace, trace)
                    self.assertLessEqual(trace.count("keeper_poll"), 1)
                    source.assert_not_called()
                    read.assert_not_called()
                    live.assert_not_called()

    def test_alive_keeper_still_reaches_the_original_bootstrap_pin_guard_once(self):
        envelope, child, metadata, trace = self.fixture()
        with (
            patch.object(supervision.os, "getpid", return_value=100),
            patch.object(supervision.util, "file_hash", return_value="b" * 64) as source,
            patch.object(supervision, "receipt", side_effect=AssertionError("receipt guard reached too early")) as read,
            patch.object(
                supervision.processes, "live_processes", side_effect=AssertionError("birth guard reached too early")
            ) as live,
        ):
            with self.assertRaisesRegex(supervision.SupervisionError, "outer bootstrap source changed"):
                envelope.authenticate(child, command=["controlled-target"], environment_sha256="e" * 64)
            self.assertEqual(["keeper_pid", "keeper_poll", "supervision_errors"], trace)
            source.assert_called_once_with(Path("controlled-bootstrap-source"))
            read.assert_not_called()
            live.assert_not_called()

    def test_a_terminal_receipt_still_refuses_after_one_original_keeper_poll(self):
        envelope, child, metadata, trace = self.fixture()
        with (
            patch.object(supervision.os, "getpid", return_value=100),
            patch.object(supervision.util, "file_hash", return_value="a" * 64) as source,
            patch.object(supervision, "receipt", return_value={"phase": "stopped", "cause": "stage_deadline"}) as read,
            patch.object(
                supervision.processes,
                "live_processes",
                side_effect=AssertionError("terminal receipt reached birth guard"),
            ) as live,
        ):
            with self.assertRaisesRegex(supervision.SupervisionError, "keeper or root birth is no longer live"):
                envelope.authenticate(child, command=["controlled-target"], environment_sha256="e" * 64)
            self.assertEqual(["keeper_pid", "keeper_poll", "supervision_errors"], trace)
            source.assert_called_once_with(Path("controlled-bootstrap-source"))
            read.assert_called_once_with(metadata)
            live.assert_not_called()


class OwnerDeadlineTests(unittest.TestCase):
    """Setup consumes the inherited launch budget; no real keeper or sleep."""

    def exercise_setup(
        self, *, timeout=20, deadline=110, pass_deadline=True, owner_delay=2, launch_delay=4, identity_delay=2
    ):
        from contextlib import ExitStack
        from unittest.mock import Mock

        clock = [100.0]
        owner = {"pid": 4001, "birth_identity": 1.0}
        keeper = Mock(spec=["pid", "returncode", "poll", "wait"])
        keeper.pid, keeper.returncode = 4002, 0
        keeper.poll.return_value = None
        keeper.wait.return_value = 0
        watcher = Mock(spec=["start", "join"])
        nonce = "c" * 32

        def live(rows):
            clock[0] += owner_delay
            return rows

        def launch(*args, **kwargs):
            clock[0] += launch_delay
            return keeper

        def identity(pid):
            clock[0] += identity_delay / 2
            return {"pid": pid, "birth_identity": 2.0}

        with tempfile.TemporaryDirectory(prefix="owner-deadline-") as temp, ExitStack() as stack:
            read_fd, write_fd = os.pipe()
            stack.callback(os.close, read_fd)
            stack.callback(os.close, write_fd)
            stack.enter_context(patch.object(supervision.time, "monotonic", side_effect=lambda: clock[0]))
            stack.enter_context(patch.object(supervision.processes, "live_processes", side_effect=live))
            stack.enter_context(patch.object(supervision, "_identity", side_effect=identity))
            popen = stack.enter_context(patch.object(supervision.subprocess, "Popen", side_effect=launch))
            send = stack.enter_context(patch.object(supervision, "_send"))
            read = stack.enter_context(patch.object(supervision, "_read_line", return_value={"armed": nonce}))
            stack.enter_context(
                patch.object(
                    supervision,
                    "receipt",
                    side_effect=[
                        {"phase": "armed", "cause": None},
                        {"phase": "armed", "cause": None},
                        {"phase": "discharged", "cause": "controller_finished"},
                    ],
                )
            )
            stack.enter_context(patch.object(supervision.select, "select", return_value=([], [], [])))
            stack.enter_context(patch.object(supervision.threading, "Thread", return_value=watcher))
            kwargs = {"deadline": deadline} if pass_deadline else {}
            error = None
            try:
                with supervision.protect_owner(
                    read_fd,
                    receipt_path=Path(temp) / "receipt.json",
                    timeout=timeout,
                    owner_identity=owner,
                    nonce=nonce,
                    **kwargs,
                ):
                    pass
            except Exception as caught:
                error = caught
            return {"error": error, "popen": popen, "send": send, "read": read, "keeper": keeper, "clock": clock[0]}

    def test_authenticated_absolute_deadline_does_not_shift_during_setup(self):
        result = self.exercise_setup()
        self.assertIsNone(result["error"])
        self.assertEqual(108, result["clock"])
        self.assertEqual(110, result["send"].call_args_list[0].args[1]["deadline"])
        self.assertEqual(110, result["read"].call_args.args[1])
        result["keeper"].wait.assert_called_once_with(timeout=8)

    def test_relative_caller_also_consumes_setup_time_and_admission_budget(self):
        result = self.exercise_setup(pass_deadline=False)
        self.assertIsNone(result["error"])
        self.assertEqual(120, result["send"].call_args_list[0].args[1]["deadline"])
        self.assertEqual(115, result["read"].call_args.args[1])

    def test_relative_bound_is_not_extended_by_a_later_inherited_deadline(self):
        result = self.exercise_setup(timeout=10, deadline=200)
        self.assertIsNone(result["error"])
        self.assertEqual(110, result["send"].call_args_list[0].args[1]["deadline"])
        self.assertEqual(110, result["read"].call_args.args[1])

    def test_owner_inspection_exhausting_the_budget_refuses_before_launch(self):
        result = self.exercise_setup(owner_delay=11)
        self.assertIsInstance(result["error"], supervision.SupervisionError)
        self.assertIn("expired", str(result["error"]))
        result["popen"].assert_not_called()
        result["send"].assert_not_called()

    def test_invalid_or_expired_bounds_refuse_before_process_inspection(self):
        for change in (
            {"deadline": 100},
            {"deadline": 99},
            {"deadline": True},
            {"deadline": "110"},
            {"deadline": float("nan")},
            {"deadline": float("inf")},
            {"timeout": 0},
            {"timeout": True},
            {"timeout": float("nan")},
            {"timeout": float("inf")},
        ):
            with self.subTest(change=change):
                result = self.exercise_setup(**change)
                self.assertIsInstance(result["error"], supervision.SupervisionError)
                self.assertEqual(100, result["clock"])
                result["popen"].assert_not_called()


class KeeperDischargeTimeoutTests(unittest.TestCase):
    """Fault the admitted keeper's exact wait, without a live keeper or sleep."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="keeper-discharge-fault-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def guard_fixture(
        self, *, timeout=False, interrupted=False, before_phase="armed", terminal_phase="discharged", returncode=0
    ):
        from contextlib import ExitStack, contextmanager
        from unittest.mock import Mock

        @contextmanager
        def controlled():
            read_fd, write_fd = os.pipe()
            self.addCleanup(os.close, read_fd)
            self.addCleanup(os.close, write_fd)
            nonce = "b" * 32
            owner = {"pid": 4001, "birth_identity": 1.0}
            native_keeper = {"pid": 4002, "birth_identity": 2.0}
            provider = {"pid": 4003, "birth_identity": 3.0}
            keeper = Mock(spec=["pid", "returncode", "poll", "wait", "kill", "terminate"])
            keeper.pid = native_keeper["pid"]
            keeper.returncode = None if timeout else returncode
            keeper.poll.return_value = None
            raw_timeout = subprocess.TimeoutExpired(["controlled-keeper"], 8)
            keeper.wait.side_effect = raw_timeout if timeout else None
            keeper.wait.return_value = returncode
            watcher = Mock(spec=["start", "join"])
            armed = {"phase": "armed", "cause": None}
            before = {"phase": before_phase, "cause": None}
            terminal = None if terminal_phase is None else {"phase": terminal_phase, "cause": "controller_finished"}
            path = self.root / "receipt.json"
            with ExitStack() as stack:
                stack.enter_context(patch.object(supervision.processes, "live_processes", return_value=[owner]))
                stack.enter_context(patch.object(supervision, "_identity", side_effect=[native_keeper, provider]))
                popen = stack.enter_context(patch.object(supervision.subprocess, "Popen", return_value=keeper))
                send = stack.enter_context(patch.object(supervision, "_send"))
                stack.enter_context(patch.object(supervision, "_read_line", return_value={"armed": nonce}))
                observe = stack.enter_context(
                    patch.object(supervision, "receipt", side_effect=[armed, before, terminal])
                )
                stack.enter_context(
                    patch.object(
                        supervision.select,
                        "select",
                        side_effect=[([], [], []), ([read_fd] if interrupted else [], [], [])],
                    )
                )
                stack.enter_context(patch.object(supervision.threading, "Thread", return_value=watcher))
                stop = stack.enter_context(patch.object(supervision, "_stop_direct"))
                kill = stack.enter_context(patch.object(supervision.os, "kill"))
                publish = stack.enter_context(patch.object(supervision.util, "atomic_json"))
                yield {
                    "context": supervision.protect_owner(
                        read_fd, receipt_path=path, timeout=30, owner_identity=owner, nonce=nonce
                    ),
                    "keeper": keeper,
                    "raw_timeout": raw_timeout,
                    "popen": popen,
                    "send": send,
                    "observe": observe,
                    "stop": stop,
                    "kill": kill,
                    "publish": publish,
                    "watcher": watcher,
                    "receipt_path": path,
                    "nonce": nonce,
                }

        return controlled()

    def assert_no_keeper_replacement_or_signal(self, fixture):
        fixture["popen"].assert_called_once()
        fixture["stop"].assert_not_called()
        fixture["kill"].assert_not_called()
        fixture["keeper"].kill.assert_not_called()
        fixture["keeper"].terminate.assert_not_called()
        fixture["publish"].assert_not_called()
        fixture["watcher"].join.assert_called_once_with(timeout=1)

    def test_discharge_timeout_is_typed_and_leaves_admitted_keeper_responsible(self):
        retained = b'{"fixture":"armed receipt retained unchanged"}\n'
        with self.guard_fixture(timeout=True) as fixture:
            with self.assertRaisesRegex(supervision.SupervisionError, "discharge did not finish") as caught:
                with fixture["context"]:
                    fixture["receipt_path"].write_bytes(retained)
            self.assertIs(fixture["raw_timeout"], caught.exception.__cause__)
            self.assertIsInstance(caught.exception, processes.ProcessError)
            fixture["keeper"].wait.assert_called_once_with(timeout=8)
            self.assertEqual(retained, fixture["receipt_path"].read_bytes())
            self.assertEqual({"finished": fixture["nonce"]}, fixture["send"].call_args_list[-1].args[1])
            self.assert_no_keeper_replacement_or_signal(fixture)

    def test_discharge_receipt_cannot_substitute_for_timed_out_actual_wait(self):
        with self.guard_fixture(timeout=True, before_phase="discharged") as fixture:
            with self.assertRaises(supervision.SupervisionError) as caught:
                with fixture["context"]:
                    pass
            self.assertIs(fixture["raw_timeout"], caught.exception.__cause__)
            fixture["keeper"].wait.assert_called_once_with(timeout=8)
            self.assertEqual(2, fixture["observe"].call_count)
            self.assert_no_keeper_replacement_or_signal(fixture)

    def test_owner_loss_does_not_wait_for_or_discharge_its_keeper(self):
        with self.guard_fixture(interrupted=True) as fixture:
            with fixture["context"] as metadata:
                self.assertEqual(fixture["nonce"], metadata["nonce"])
            fixture["keeper"].wait.assert_not_called()
            self.assertEqual(1, fixture["send"].call_count)
            self.assert_no_keeper_replacement_or_signal(fixture)

    def test_verified_discharge_preserves_normal_and_nonzero_body_exit(self):
        for code in (0, 7):
            with self.subTest(code=code), self.guard_fixture() as fixture:
                if code:
                    with self.assertRaises(SystemExit) as caught:
                        with fixture["context"]:
                            raise SystemExit(code)
                    self.assertEqual(code, caught.exception.code)
                else:
                    with fixture["context"]:
                        pass
                fixture["keeper"].wait.assert_called_once_with(timeout=8)
                self.assertEqual(3, fixture["observe"].call_count)
                self.assert_no_keeper_replacement_or_signal(fixture)

    def test_wait_success_still_requires_verified_discharge_receipt_and_exit(self):
        for terminal, code in ((None, 0), ("stopped", 0), ("discharged", 4)):
            with (
                self.subTest(terminal=terminal, code=code),
                self.guard_fixture(terminal_phase=terminal, returncode=code) as fixture,
            ):
                with self.assertRaisesRegex(supervision.SupervisionError, "cleanup was uncertain"):
                    with fixture["context"]:
                        pass
                fixture["keeper"].wait.assert_called_once_with(timeout=8)
                self.assert_no_keeper_replacement_or_signal(fixture)

    def test_public_cli_reports_typed_discharge_timeout_without_traceback(self):
        import io

        import autocode as runner

        stdout, stderr = io.StringIO(), io.StringIO()
        with self.guard_fixture(timeout=True) as fixture:

            def guarded_main():
                with fixture["context"]:
                    return 0

            with (
                patch.object(runner, "main", side_effect=guarded_main),
                patch.object(runner.sys, "stdout", stdout),
                patch.object(runner.sys, "stderr", stderr),
            ):
                self.assertEqual(2, runner.cli())
            self.assertIn("autocode: Harness keeper discharge did not finish within its bound", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())
            fixture["keeper"].wait.assert_called_once_with(timeout=8)
            self.assert_no_keeper_replacement_or_signal(fixture)


class KeeperStderrCaptureTests(unittest.TestCase):
    """#815: the keeper's death cause must reach the SupervisionError message."""

    def setUp(self):
        import shutil
        from unittest import mock

        import autocode_supervision as supervision_module

        self.mock = mock
        self.supervision = supervision_module
        self.shutil = shutil
        self.directory = tempfile.TemporaryDirectory(prefix="keeper-stderr-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_keeper_exit_before_identity_sampling_names_its_stderr_cause_without_release(self):
        real_popen = subprocess.Popen
        provider_children = []
        marker = self.root / "provider-released"

        def exited_keeper(argv=None, **kwargs):
            if argv and argv[1:2] == ["-E"]:
                child = real_popen(
                    [
                        sys.executable,
                        "-c",
                        "import sys; sys.stderr.write(\"ModuleNotFoundError: No module named 'psutil'\\n\"); sys.exit(1)",
                    ],
                    **kwargs,
                )
                child.wait(timeout=5)  # Force the early-exit ordering, without a sleep.
                return child
            child = real_popen(argv, **kwargs)
            provider_children.append(child)
            return child

        with self.mock.patch.object(self.supervision.subprocess, "Popen", exited_keeper):
            with self.assertRaisesRegex(self.supervision.SupervisionError, "acknowledging ownership.*psutil"):
                with self.supervision.launch(
                    [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('released')"],
                    receipt_path=self.root / "receipt.json",
                    timeout=30,
                    start_new_session=True,
                ):
                    self.fail("An exited keeper must not release the provider")
        self.assertEqual(1, len(provider_children))
        self.assertIsNotNone(provider_children[0].poll())
        self.assertFalse(marker.exists())
        self.assertFalse((self.root / "receipt.json").exists())

    def test_alive_keeper_identity_inspection_failure_keeps_original_error_without_release(self):
        real_popen = subprocess.Popen
        real_identity = self.supervision._identity
        keeper = None
        provider_children = []
        marker = self.root / "provider-released"
        failure = self.supervision.processes.ProcessError("Cannot inspect owned keeper: access denied")

        def blocked_keeper(argv=None, **kwargs):
            nonlocal keeper
            if argv and argv[1:2] == ["-E"]:
                kwargs["stderr"].write(b"keeper diagnostic must not replace an inspection error\n")
                kwargs["stderr"].flush()
                keeper = real_popen(
                    [sys.executable, "-c", "import os,sys; os.read(int(sys.argv[1]), 1)", argv[-2]],
                    **kwargs,
                )
                return keeper
            child = real_popen(argv, **kwargs)
            provider_children.append(child)
            return child

        def denied_identity(pid):
            if keeper is not None and pid == keeper.pid:
                raise failure
            return real_identity(pid)

        with (
            self.mock.patch.object(self.supervision.subprocess, "Popen", blocked_keeper),
            self.mock.patch.object(self.supervision, "_identity", side_effect=denied_identity),
            self.assertRaises(self.supervision.processes.ProcessError) as caught,
        ):
            with self.supervision.launch(
                [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('released')"],
                receipt_path=self.root / "receipt.json",
                timeout=30,
                start_new_session=True,
            ):
                self.fail("An unverified keeper must not release the provider")
        self.assertIs(failure, caught.exception)
        self.assertIsNotNone(keeper.poll())
        self.assertEqual(1, len(provider_children))
        self.assertIsNotNone(provider_children[0].poll())
        self.assertFalse(marker.exists())
        self.assertFalse((self.root / "receipt.json").exists())

    def assert_pre_ack_failure_keeps_cleanup_and_error(self, site, keeper_state):
        real_popen = subprocess.Popen
        real_identity = self.supervision._identity
        real_send = self.supervision._send
        keeper = None
        provider_children = []
        trigger_r, trigger_w = os.pipe()
        marker = self.root / "provider-released"
        denied_polls = []
        failure = (
            self.supervision.SupervisionError("Cannot capture supervision process birth identity")
            if site == "provider_identity"
            else BrokenPipeError("Keeper control pipe closed before acknowledgement")
        )

        def controlled_keeper(argv=None, **kwargs):
            nonlocal keeper
            if argv and argv[1:2] == ["-E"]:
                kwargs["pass_fds"] = (*kwargs["pass_fds"], trigger_r)
                argv = [
                    sys.executable,
                    "-c",
                    "import os,select,sys; control,trigger=map(int,sys.argv[1:]); "
                    "ready=select.select([control,trigger],[],[])[0]; "
                    "fault=trigger in ready and bool(os.read(trigger,1)); "
                    "sys.stderr.write(\"ModuleNotFoundError: No module named 'psutil'\\n\") if fault else None; "
                    "sys.exit(1 if fault else 0)",
                    argv[-2],
                    str(trigger_r),
                ]
                keeper = real_popen(argv, **kwargs)
                return keeper
            child = real_popen(argv, **kwargs)
            provider_children.append(child)
            return child

        def fail_at_boundary():
            if keeper_state == "exited":
                os.write(trigger_w, b"X")
                keeper.wait(timeout=5)
            elif keeper_state == "unknown":
                real_poll = keeper.poll

                def denied_once():
                    if not denied_polls:
                        denied_polls.append(True)
                        raise PermissionError("Keeper exit inspection denied")
                    return real_poll()

                keeper.poll = denied_once
            raise failure

        def identity(pid):
            if site == "provider_identity" and any(child.pid == pid for child in provider_children):
                fail_at_boundary()
            return real_identity(pid)

        def send(fd, value):
            if site == "send":
                fail_at_boundary()
            return real_send(fd, value)

        try:
            with (
                self.mock.patch.object(self.supervision.subprocess, "Popen", controlled_keeper),
                self.mock.patch.object(self.supervision, "_identity", side_effect=identity),
                self.mock.patch.object(self.supervision, "_send", side_effect=send),
                self.assertRaises((self.supervision.processes.ProcessError, OSError)) as caught,
            ):
                with self.supervision.launch(
                    [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('released')"],
                    receipt_path=self.root / "receipt.json",
                    timeout=30,
                    start_new_session=True,
                ):
                    self.fail("A failed pre-ack operation must not release the provider")
            if keeper_state == "exited":
                self.assertRegex(str(caught.exception), "acknowledging ownership.*psutil")
                self.assertIs(failure, caught.exception.__cause__)
            else:
                self.assertIs(failure, caught.exception)
            if keeper_state == "unknown":
                self.assertEqual([True], denied_polls)
            self.assertIsNotNone(keeper.poll())
            self.assertEqual(1, len(provider_children))
            self.assertIsNotNone(provider_children[0].poll())
            self.assertFalse(marker.exists())
            self.assertFalse((self.root / "receipt.json").exists())
        finally:
            os.close(trigger_r)
            os.close(trigger_w)

    def test_provider_identity_failure_after_keeper_exit_names_its_stderr_without_release(self):
        self.assert_pre_ack_failure_keeps_cleanup_and_error("provider_identity", "exited")

    def test_provider_identity_failure_with_alive_keeper_keeps_original_error_without_release(self):
        self.assert_pre_ack_failure_keeps_cleanup_and_error("provider_identity", "alive")

    def test_provider_identity_failure_with_unknown_keeper_keeps_original_error_without_release(self):
        self.assert_pre_ack_failure_keeps_cleanup_and_error("provider_identity", "unknown")

    def test_send_failure_after_keeper_exit_names_its_stderr_without_release(self):
        self.assert_pre_ack_failure_keeps_cleanup_and_error("send", "exited")

    def test_send_failure_with_alive_keeper_keeps_original_error_without_release(self):
        self.assert_pre_ack_failure_keeps_cleanup_and_error("send", "alive")

    def test_send_failure_with_unknown_keeper_keeps_original_error_without_release(self):
        self.assert_pre_ack_failure_keeps_cleanup_and_error("send", "unknown")

    def test_keeper_death_before_ack_names_its_stderr_cause(self):
        real_popen = subprocess.Popen

        def failing_keeper(argv=None, **kwargs):
            if argv and argv[1:2] == ["-E"]:
                # Wait for the ownership frame, sent after both birth identities are captured.
                argv = [
                    sys.executable,
                    "-c",
                    "import os, sys; os.read(int(sys.argv[1]), 1); "
                    "sys.stderr.write(\"ModuleNotFoundError: No module named 'psutil'\\n\"); sys.exit(1)",
                    argv[3],
                ]
            return real_popen(argv, **kwargs)

        with self.mock.patch.object(self.supervision.subprocess, "Popen", failing_keeper):
            with self.assertRaisesRegex(self.supervision.SupervisionError, "acknowledging ownership.*psutil"):
                with self.supervision.launch(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    receipt_path=self.root / "receipt.json",
                    timeout=30,
                    start_new_session=True,
                ):
                    self.fail("A dead keeper must not release the provider")

    def test_successful_launch_leaves_no_keeper_stderr_file(self):
        with self.supervision.launch(
            [sys.executable, "-c", "print('done')"],
            receipt_path=self.root / "receipt.json",
            timeout=30,
            start_new_session=True,
        ) as child:
            child.wait(timeout=15)
        leftovers = list(self.root.glob("keeper-stderr-*.log"))
        self.assertEqual([], leftovers, leftovers)

    def test_failed_launch_retains_the_stderr_file_for_inspection(self):
        real_popen = subprocess.Popen

        def failing_keeper(argv=None, **kwargs):
            if argv and argv[1:2] == ["-E"]:
                argv = [
                    sys.executable,
                    "-c",
                    'import sys; sys.stderr.write("boom: keeper unimportable\\n"); sys.exit(1)',
                ]
            return real_popen(argv, **kwargs)

        with self.mock.patch.object(self.supervision.subprocess, "Popen", failing_keeper):
            with self.assertRaises(self.supervision.SupervisionError):
                with self.supervision.launch(
                    [sys.executable, "-c", "pass"],
                    receipt_path=self.root / "receipt.json",
                    timeout=30,
                    start_new_session=True,
                ):
                    self.fail("A dead keeper must not release the provider")
        retained = list(self.root.glob("keeper-stderr-*.log"))
        self.assertEqual(1, len(retained), retained)
        self.assertIn("keeper unimportable", retained[0].read_text())


if __name__ == "__main__":
    unittest.main()
