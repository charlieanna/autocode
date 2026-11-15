"""Login metadata must settle its owned descendants before returning a result."""

import json
import os
import select
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_captured_process as captured
import autocode_connected_engine as connected
import autocode_connections as connections
import autocode_process as processes


@unittest.skipUnless(sys.platform == "linux", "owned metadata descendants require supported Linux")
class ConnectionProcessTests(unittest.TestCase):
    def test_timeout_and_success_settle_descendants_before_connection_results(self):
        sentinel = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)
        try:
            for caller in ("connection", "default_engine"):
                for outcome in ("timeout", "success"):
                    with self.subTest(caller=caller, outcome=outcome), tempfile.TemporaryDirectory() as directory:
                        self.check_owned_result(Path(directory), caller, outcome)
                        self.assertIsNone(sentinel.poll(), "an unrelated process must remain alive")
        finally:
            sentinel.stdin.close()
            sentinel.wait(timeout=5)

    def check_owned_result(self, root, caller, outcome):
        ready = root / "ready"
        os.mkfifo(ready)
        descriptor = os.open(ready, os.O_RDWR)
        self.addCleanup(os.close, descriptor)
        worker_file = root / "worker.json"
        script = root / "auth_status.py"
        script.write_text(
            "import json, os, pathlib, subprocess, sys\n"
            "worker = subprocess.Popen([sys.executable, '-c', "
            "'import os; r,w=os.pipe(); os.read(r,1)'], "
            f"start_new_session={outcome == 'timeout'!r}, "
            "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
            f"pathlib.Path({str(worker_file)!r}).write_text(json.dumps(worker.pid))\n"
            "print('SECRET backend account', flush=True)\n"
            f"with open({str(ready)!r}, 'w') as ready: ready.write('R')\n"
            + ("r,w=os.pipe(); os.read(r,1)\n" if outcome == "timeout" else "")
        )
        command = [sys.executable, str(script)]
        cleanup = None
        child_process = None
        worker = None
        supervisor_wait = captured.supervisor.wait
        communicate = subprocess.Popen.communicate
        allowances = []

        def observed(child, timeout):
            nonlocal cleanup, child_process, worker
            allowances.append(timeout)
            child_process = child
            cleanup = processes.ProcessTree(child.pid, lambda rows: None)
            cleanup.capture_root()
            cleanup.sample()
            self.assertTrue(select.select([descriptor], [], [], 5)[0], "auth fixture did not become ready")
            self.assertEqual(b"R", os.read(descriptor, 1))
            worker_pid = json.loads(worker_file.read_text())
            worker = processes.identity(processes.process_table({worker_pid})[worker_pid])
            cleanup.known[worker_pid] = worker
            cleanup.sample()

        def owned_wait(child, timeout):
            observed(child, timeout)
            # The fixture is definitely live; advance only this existing wait's
            # allowance instead of sleeping through its 45-second deadline.
            return supervisor_wait(child, 0 if outcome == "timeout" else timeout)

        def raw_communicate(child, input=None, timeout=None):
            observed(child, timeout)
            return communicate(child, input, timeout=0 if outcome == "timeout" else 5)

        try:
            with (
                patch.object(captured.supervisor, "wait", owned_wait),
                patch.object(subprocess.Popen, "communicate", raw_communicate),
                patch.object(connections, "_definition", return_value=("Fixture", sys.executable, None, command, None)),
            ):
                if caller == "connection":
                    result = connections.connection_view("fixture", check=True)
                    self.assertEqual(
                        "check_failed" if outcome == "timeout" else "credentials_present",
                        result["connection_status"],
                    )
                    self.assertNotIn("SECRET", json.dumps(result))
                elif outcome == "timeout":
                    with self.assertRaises(subprocess.TimeoutExpired):
                        connected._run(command, cwd=root)
                else:
                    self.assertEqual(0, connected._run(command, cwd=root).returncode)
            self.assertEqual([], processes.live_processes([worker]), "connection check left a live owned descendant")
            self.assertEqual([45], allowances)
        finally:
            if cleanup is not None:
                cleanup.stop(child_process)
