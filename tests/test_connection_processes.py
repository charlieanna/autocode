"""Login metadata must settle its owned descendants before returning a result."""

import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import unittest
from contextlib import suppress
from pathlib import Path
from unittest.mock import patch

import autocode_captured_process as captured
import autocode_connected_engine as connected
import autocode_connections as connections


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
        record = root / "worker.json"
        script = root / "auth_status.py"
        script.write_text(
            "import json, os, pathlib, subprocess, sys\n"
            "worker = subprocess.Popen([sys.executable, '-c', 'import os; r,w=os.pipe(); os.read(r,1)'], "
            "start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
            "start = pathlib.Path(f'/proc/{worker.pid}/stat').read_text().rpartition(')')[2].split()[19]\n"
            f"pathlib.Path({str(record)!r}).write_text(json.dumps({{'pid':worker.pid,'start':start}}))\n"
            "print('SECRET backend account', flush=True)\n"
            + ("r,w=os.pipe(); os.read(r,1)\n" if outcome == "timeout" else "")
        )
        command = [sys.executable, str(script)]
        allowances = []
        run = captured.run

        def bounded(words, **options):
            allowances.append(options["timeout"])
            return run(words, **{**options, "timeout": 1 if outcome == "timeout" else 5})

        try:
            with (
                patch.object(captured, "run", side_effect=bounded),
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
            row = json.loads(record.read_text())
            path = Path(f"/proc/{row['pid']}/stat")
            self.assertTrue(
                not path.exists() or path.read_text().rpartition(")")[2].split()[19] != row["start"],
                "connection check left an owned child",
            )
            self.assertEqual([45], allowances)
        finally:
            if record.exists():
                row = json.loads(record.read_text())
                with suppress(ProcessLookupError):
                    descriptor = os.pidfd_open(row["pid"])
                    try:
                        path = Path(f"/proc/{row['pid']}/stat")
                        if path.exists() and path.read_text().rpartition(")")[2].split()[19] == row["start"]:
                            signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                            self.assertTrue(select.select([descriptor], [], [], 5)[0], "fixture worker did not stop")
                    finally:
                        os.close(descriptor)
