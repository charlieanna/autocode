"""An independent checkout contender waits for worker cleanup after owner loss (#454).

Real dispatch, lifelines, keepers and kernel flocks; the worker is an offline
stand-in. Pipe events and release barriers establish ordering without sleeps.
"""

import contextlib
import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_checkout_lock as checkout_lock
import autocode_process as processes

TOOLS = Path(checkout_lock.__file__).resolve().parent

KEEPER = """\
import json, os, sys
sys.path.insert(0, os.environ['LEASE_TEST_TOOLS'])
import autocode_supervision_keeper as keeper

event_fd = int(os.environ['LEASE_TEST_EVENTS'])
release_fd = int(os.environ['LEASE_TEST_RELEASE'])
original = keeper.util.atomic_json
if os.environ.get('LEASE_TEST_UNCERTAIN') == '1':
    def inspection_failure(self, child):
        raise keeper.processes.ProcessError('Injected owned-worker inspection failure')
    keeper.KeeperTree.stop = inspection_failure
    keeper.KeeperTree.signal = lambda *args: None

def publication(path, value):
    if value.get('phase') in ('stopped', 'discharged', 'uncertain'):
        os.write(event_fd, (json.dumps({'event': 'cleanup_ready', 'receipt': value}) + '\\n').encode())
        if os.read(release_fd, 1) != b'G':
            raise RuntimeError('Test publication barrier closed')
    original(path, value)

keeper.util.atomic_json = publication
code = keeper.main(*(int(value) for value in sys.argv[1:]))
os.write(event_fd, (json.dumps({'event': 'keeper_retired', 'pid': os.getpid()}) + '\\n').encode())
raise SystemExit(code)
"""

WORKER = """\
import json, os, signal, subprocess, sys
sys.path.insert(0, os.environ['LEASE_TEST_TOOLS'])
import autocode_supervision as supervision
import autocode_supervision_cli as admission

event_fd = int(os.environ['LEASE_TEST_EVENTS'])
release_fd = int(os.environ['LEASE_TEST_RELEASE'])
popen = subprocess.Popen

def launch(argv, **options):
    argv = list(argv)
    argv[2] = os.environ['LEASE_TEST_KEEPER']
    options['pass_fds'] = (*options.get('pass_fds', ()), event_fd, release_fd)
    return popen(argv, **options)

supervision.subprocess.Popen = launch
if os.environ.get('LEASE_TEST_UNCERTAIN') == '1':
    declaration = admission.declaration
    def bounded(fd):
        packet, remaining = declaration(fd)
        packet['kill_after_seconds'] = 0.001
        return packet, remaining
    admission.declaration = bounded

def interrupted(signum, frame):
    raise SystemExit(2)

with admission.guard(sys.argv[1:]):
    signal.signal(signal.SIGTERM, signal.SIG_IGN if os.environ.get('LEASE_TEST_UNCERTAIN') == '1' else interrupted)
    signal.signal(signal.SIGUSR1, interrupted)
    target = os.stat(os.environ['LEASE_TEST_LOCK'])
    inherited = []
    for fd in range(3, 1024):
        try:
            row = os.fstat(fd)
        except OSError:
            continue
        if (row.st_dev, row.st_ino) == (target.st_dev, target.st_ino):
            inherited.append(fd)
    os.write(event_fd, (json.dumps({'event': 'worker_guarded', 'lease_copies': inherited}) + '\\n').encode())
    while True:
        signal.pause()
"""

CONTROLLER = """\
import json, os, subprocess, sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, os.environ['LEASE_TEST_TOOLS'])
import autocode_checkout_lock as checkout_lock
import autocode_dispatch as dispatch
import autocode_util as util

root = Path(sys.argv[1])
workspace = root / 'workspace'
state = {'status': 'RUNNING', 'workspace': str(workspace)}
batch = {'workers': [{'status': 'PENDING', 'run_dir': str(root / f'worker-{n}'), 'workspace': str(root / f'worker-{n}'), 'milestone_id': f'M{n}'} for n in range(1, int(os.environ['LEASE_TEST_WORKERS']) + 1)]}
popen = subprocess.Popen

def launch(argv, **options):
    argv = list(argv)
    argv[1] = os.environ['LEASE_TEST_WORKER']
    options['pass_fds'] = (*options.get('pass_fds', ()), int(os.environ['LEASE_TEST_EVENTS']), int(os.environ['LEASE_TEST_RELEASE']))
    return popen(argv, **options)

with checkout_lock.exclusive(workspace, root), patch.object(dispatch.subprocess, 'Popen', launch), patch.object(dispatch.s, 'assert_no_legacy_process'), patch.object(dispatch.autocode_status, 'persist', side_effect=util.atomic_json):
    dispatch.run_workers(state, root, batch)
"""

PROBE = """\
import fcntl, sys
with open(sys.argv[1], 'a+') as handle:
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(73)
"""


class CheckoutLeaseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="checkout-lease-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        for name in ("worker-1", "worker-2"):
            (self.root / name).mkdir()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.event_r, event_w = os.pipe()
        release_r, self.release_w = os.pipe()
        self.addCleanup(self.close_pipe, self.event_r)
        self.addCleanup(self.close_pipe, self.release_w)
        self.pending = bytearray()
        paths = {}
        for name, source in (("controller", CONTROLLER), ("worker", WORKER), ("keeper", KEEPER)):
            path = self.root / f"{name}.py"
            path.write_text(source)
            paths[name] = str(path)
        env = {
            **os.environ,
            "LEASE_TEST_TOOLS": str(TOOLS),
            "LEASE_TEST_EVENTS": str(event_w),
            "LEASE_TEST_RELEASE": str(release_r),
            "LEASE_TEST_WORKER": paths["worker"],
            "LEASE_TEST_KEEPER": paths["keeper"],
            "LEASE_TEST_LOCK": str(checkout_lock.lock_path(self.workspace)),
            "LEASE_TEST_UNCERTAIN": "1" if "uncertain_keeper" in self._testMethodName else "0",
            "LEASE_TEST_WORKERS": "1" if "uncertain_keeper" in self._testMethodName else "2",
        }
        self.controller = subprocess.Popen(
            [sys.executable, paths["controller"], str(self.root)],
            env=env,
            pass_fds=(event_w, release_r),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        self.addCleanup(self.cleanup_owned)
        os.close(event_w)
        os.close(release_r)

    @staticmethod
    def close_pipe(fd):
        with contextlib.suppress(OSError):
            os.close(fd)

    def event(self):
        while b"\n" not in self.pending:
            self.assertTrue(select.select([self.event_r], [], [], 15)[0], "Owned actor never reached its event barrier")
            value = os.read(self.event_r, 65536)
            self.assertTrue(value, "Owned actor exited before its event barrier")
            self.pending.extend(value)
        line, _, self.pending = self.pending.partition(b"\n")
        return json.loads(line)

    def probe(self):
        return subprocess.run(
            [sys.executable, "-c", PROBE, str(checkout_lock.lock_path(self.workspace))],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        ).returncode

    def cleanup_owned(self):
        # Only birth-identified actors in this test's own receipts may be signalled.
        if self.controller.poll() is None:
            self.controller.kill()
        self.controller.communicate(timeout=10)
        for path in self.root.glob("worker-*/lifeline/*-supervision.json"):
            value = json.loads(path.read_text())
            rows = [value["keeper"], value["provider"], *value.get("processes", [])]
            for row in processes.live_processes(rows):
                with contextlib.suppress(ProcessLookupError):
                    os.kill(row["pid"], signal.SIGKILL)

    def test_checkout_stays_locked_after_owner_death_until_worker_cleanup_is_published(self):
        for _ in range(2):
            guarded = self.event()
            self.assertEqual("worker_guarded", guarded["event"], guarded)
            self.assertEqual([], guarded["lease_copies"], "A worker would leak the parent lease to its own commands")
        self.assertEqual(73, self.probe(), "The checkout was never locked")
        self.controller.kill()
        self.controller.communicate(timeout=10)
        receipts = []
        for _ in range(2):
            terminal = self.event()
            self.assertEqual("cleanup_ready", terminal["event"], terminal)
            receipt = terminal["receipt"]
            receipts.append(receipt)
            self.assertEqual(
                ("stopped", "owner_lost", None), (receipt["phase"], receipt["cause"], receipt["cleanup_error"])
            )
            self.assertEqual([], processes.live_processes([receipt["provider"], *receipt["processes"]]))
            self.assertTrue(
                processes.live_processes([receipt["keeper"]]), "Cleanup keeper disappeared before publication"
            )
        self.assertEqual(73, self.probe(), "Orchestrator death unlocked the checkout before worker cleanup settled")
        os.write(self.release_w, b"G")
        retired = self.event()
        self.assertEqual("keeper_retired", retired["event"], retired)
        self.assertEqual(
            73, self.probe(), "The first completed worker unlocked a checkout whose other keeper is still settling"
        )
        os.write(self.release_w, b"G")
        self.assertEqual("keeper_retired", self.event()["event"])
        # Only keepers retain the event writer; EOF proves both retired.
        self.assertTrue(select.select([self.event_r], [], [], 15)[0], "Keeper did not retire after publication")
        self.assertEqual(b"", os.read(self.event_r, 1))
        self.assertEqual(0, self.probe(), "Verified worker cleanup did not release the checkout")
        for receipt in receipts:
            saved = json.loads(Path(receipt["receipt"]).read_text())
            self.assertEqual(("stopped", None), (saved["phase"], saved["cleanup_error"]))

    def test_uncertain_keeper_exit_keeps_autocode_admission_closed_over_a_live_worker(self):
        guarded = self.event()
        self.assertEqual("worker_guarded", guarded["event"], guarded)
        self.controller.kill()
        self.controller.communicate(timeout=10)
        terminal = self.event()
        self.assertEqual("cleanup_ready", terminal["event"], terminal)
        receipt = terminal["receipt"]
        self.assertEqual("uncertain", receipt["phase"])
        self.assertIn("Injected owned-worker inspection failure", receipt["cleanup_error"])
        self.assertTrue(processes.live_processes([receipt["provider"]]))
        self.assertEqual(73, self.probe(), "The keeper dropped its capability before publishing uncertain ownership")
        os.write(self.release_w, b"G")
        self.assertEqual("keeper_retired", self.event()["event"])
        self.assertEqual(0, self.probe(), "An uncertain keeper was kept around as an unbounded lease process")
        with self.assertRaises(checkout_lock.CheckoutBusy):
            with checkout_lock.exclusive(self.workspace, self.root / "second-run"):
                self.fail("A bounded keeper exit admitted work over its still-live owned worker")
        owned = processes.live_processes([receipt["provider"]])
        self.assertEqual(1, len(owned))
        os.kill(owned[0]["pid"], signal.SIGUSR1)
        self.assertTrue(select.select([self.event_r], [], [], 15)[0], "Owned worker did not exit after fixture release")
        self.assertEqual(b"", os.read(self.event_r, 1))
        with checkout_lock.exclusive(self.workspace, self.root / "second-run"):
            pass
        self.assertEqual({}, checkout_lock.holder(self.workspace))


if __name__ == "__main__":
    unittest.main()
