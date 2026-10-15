"""A keeper that never arms says where its launch limit went (#989).

REV-10 failed once with only "Keeper did not arm within the launch limit": nothing
showed whether the parent's own launch steps, the keeper's startup or its admission
work used the 15 seconds. These tests drive the real launch path on a fake clock and
real pipes, with fake processes, so the limit expires without waiting in real time.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_supervision as supervision


class FakeProcess:
    def __init__(self, pid):
        self.pid, self.returncode = pid, None

    def poll(self):
        return self.returncode

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class LaunchLimitDiagnosticTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def launch(self, *, timeout, spawn_cost=0.0, keeper_reads=False, stall=0.0, state="sleeping", table=None):
        """Launch a runner command whose keeper never acknowledges; the error and whether it was released.

        ``spawn_cost`` is fake time the parent spends starting the keeper. With
        ``keeper_reads`` the keeper consumes its whole ownership frame; ``stall`` is
        fake time that passes while the parent waits for the acknowledgement.
        ``table`` replaces the process-table probe that reports the keeper's ``state``.
        """
        clock = [1000.0]
        held = {}
        real_send, real_read_line = supervision._send, supervision._read_line

        def popen(argv, **kwargs):
            if argv[1:2] == ["-E"]:
                held["control"] = os.dup(int(argv[3]))
                clock[0] += spawn_cost
                return FakeProcess(4242)
            held["release"] = os.dup(int(argv[3]))
            return FakeProcess(4343)

        def send(fd, value):
            real_send(fd, value)
            if keeper_reads:
                frame = b""
                while not frame.endswith(b"\n"):
                    frame += os.read(held["control"], 4096)

        def read_line(fd, deadline):
            clock[0] += stall
            return real_read_line(fd, deadline)

        try:
            with (
                patch.object(supervision.time, "monotonic", lambda: clock[0]),
                patch.object(supervision.subprocess, "Popen", popen),
                patch.object(supervision, "_identity", lambda pid: {"pid": pid, "birth_identity": 1.0}),
                patch.object(supervision, "_send", send),
                patch.object(supervision, "_read_line", read_line),
                patch.object(
                    supervision.processes,
                    "process_table",
                    table or (lambda pids: {pid: {"state": state} for pid in pids}),
                ),
                self.assertRaises(supervision.SupervisionError) as caught,
                supervision.launch_command(
                    [sys.executable, "-c", "pass"],
                    receipt_path=self.root / "receipt.json",
                    timeout=timeout,
                    start_new_session=True,
                ),
            ):
                self.fail("A keeper that never armed must not release the provider")
            # The parent closed its release writer without the release byte.
            released = os.read(held["release"], 1)
        finally:
            for fd in held.values():
                os.close(fd)
        return str(caught.exception), released

    def test_slow_parent_launch_is_named_and_the_frame_left_unread(self):
        message, released = self.launch(timeout=30, spawn_cost=16)
        self.assertEqual(b"", released)
        self.assertTrue(message.startswith("Keeper did not arm within the launch limit (limit 15s; "), message)
        self.assertIn("keeper spawned +16.00s; provider spawned +16.00s; ownership sent +16.00s", message)
        self.assertIn("keeper alive (sleeping)", message)
        if sys.platform.startswith("linux"):
            self.assertRegex(message, r"ownership frame unread \(\d+ bytes\)")
        self.assertTrue(message.endswith("; no receipt)"), message)

    def test_keeper_stalled_after_reading_its_frame_is_named(self):
        message, released = self.launch(timeout=30, keeper_reads=True, stall=16, state="disk-sleep")
        self.assertEqual(b"", released)
        self.assertIn("(limit 15s; keeper spawned +0.00s; provider spawned +0.00s; ownership sent +0.00s;", message)
        self.assertIn("keeper alive (disk-sleep)", message)
        if sys.platform.startswith("linux"):
            self.assertIn("ownership frame read;", message)
        self.assertTrue(message.endswith("; no receipt)"), message)

    def test_a_shorter_stage_timeout_still_bounds_the_launch_limit(self):
        message, released = self.launch(timeout=10, spawn_cost=11)
        self.assertEqual(b"", released)
        self.assertIn("(limit 10s; keeper spawned +11.00s;", message)

    def test_failed_probes_report_unknown_and_keep_the_launch_limit_error(self):
        def denied(pids):
            raise supervision.processes.ProcessError("Cannot inspect owned keeper: access denied")

        with patch.object(supervision, "_unread_bytes", return_value=None):
            message, released = self.launch(timeout=30, spawn_cost=16, table=denied)
        self.assertEqual(b"", released)
        self.assertTrue(message.startswith("Keeper did not arm within the launch limit (limit 15s; "), message)
        self.assertIn("; keeper unknown; ownership frame unknown; no receipt)", message)

    def test_unread_bytes_counts_the_pending_frame_from_the_write_end(self):
        if not sys.platform.startswith("linux"):
            self.assertIsNone(supervision._unread_bytes(1))
            return
        read, write = os.pipe()
        self.addCleanup(os.close, read)
        self.addCleanup(os.close, write)
        os.write(write, b"frame\n")
        self.assertEqual(6, supervision._unread_bytes(write))
        os.read(read, 6)
        self.assertEqual(0, supervision._unread_bytes(write))


if __name__ == "__main__":
    unittest.main()
