"""A command's output is kept up to a byte limit and the rest is drained, not written (#1118)."""

import contextlib
import hashlib
import io
import json
import os
import shlex
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_capped_output as capped
import autocode_capture_command as capture
import autocode_verify as verify


def python(source):
    return shlex.join([sys.executable, "-c", source])


class WatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="watch-")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "out.log"
        self.file = self.path.open("xb")  # a command writes through a descriptor like this one, sharing its offset
        self.addCleanup(self.file.close)

    def test_a_file_past_the_limit_is_cut_back_and_marked(self):
        self.file.write(b"0123456789" * 10)
        self.file.flush()
        stats = capped.Watch(self.file.fileno(), limit=25).finish()
        self.assertEqual({"total": 100, "limit": 25, "truncated": True}, stats)
        self.assertEqual(
            b"0123456789012345678901234" + capped.notice(100, 25),
            self.path.read_bytes(),
            "the first bytes, then a notice",
        )

    def test_writes_after_a_cut_are_counted_and_the_file_never_keeps_a_gap(self):
        # The command's offset is not moved by the cut: its next write lands past the end, and the
        # size it leaves is the total written, so the dropped bytes are counted without being held.
        watch = capped.Watch(self.file.fileno(), limit=25)
        self.file.write(b"a" * 100)
        self.file.flush()
        watch.enforce()
        self.assertEqual(25, self.path.stat().st_size)
        self.file.write(b"b" * 50)
        self.file.flush()
        stats = watch.finish()
        self.assertEqual((150, True), (stats["total"], stats["truncated"]))
        self.assertEqual(b"a" * 25 + capped.notice(150, 25), self.path.read_bytes())

    def test_a_final_write_between_size_sample_and_cut_is_counted(self):
        self.file.write(b"a" * 100)
        self.file.flush()
        sampled = os.fstat(self.file.fileno())
        watch = capped.Watch(self.file.fileno(), limit=25)

        def final_write(descriptor):
            # The writer finishes after the size sample, before the watcher cuts.
            os.write(descriptor, b"b" * 50)
            return sampled

        with patch.object(capped.os, "fstat", side_effect=final_write):
            watch.enforce()
        stats = watch.finish()
        self.assertEqual({"total": 150, "limit": 25, "truncated": True}, stats)
        self.assertEqual(b"a" * 25 + capped.notice(150, 25), self.path.read_bytes())

    def test_output_within_the_limit_is_left_alone(self):
        self.file.write(b"small")
        self.file.flush()
        stats = capped.Watch(self.file.fileno(), limit=25).finish()
        self.assertEqual({"total": 5, "limit": 25, "truncated": False}, stats)
        self.assertEqual(b"small", self.path.read_bytes())

    def test_the_polling_thread_holds_a_runaway_writer_near_the_limit(self):
        # What is bounded is the space the file takes: the command's offset keeps counting, so the cut leaves
        # a hole, and the size the file reports is the total written until the last cut.
        watch = capped.Watch(self.file.fileno(), limit=10_000)
        with patch.object(capped, "INTERVAL", 0.02):
            watch.start()
            for _ in range(60):
                self.file.write(b"z" * 5_000)
                self.file.flush()
                time.sleep(0.01)
            allocated = self.path.stat().st_blocks * 512
            watch.stop()
        stats = watch.finish()
        self.assertEqual(300_000, stats["total"])
        self.assertLess(allocated, 10_000 + 20 * 5_000, "the space used did not grow with the output")
        self.assertEqual(10_000 + len(capped.notice(300_000, 10_000)), self.path.stat().st_size)


class RunCommandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="capped-output-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_a_flood_of_output_is_truncated_without_failing_the_command(self):
        with patch.object(capped, "LIMIT", 1_000_000):
            result = verify.run_command(
                python("import sys; sys.stdout.write('x' * 5_000_000)"), self.root, self.root / "flood.log"
            )
        output = Path(result["output"])
        self.assertEqual(0, result["exit_code"], "a verbose command that passes still passes")
        self.assertTrue(result["output_truncated"])
        self.assertEqual((5_000_000, 1_000_000), (result["output_bytes"], result["output_limit_bytes"]))
        self.assertLess(output.stat().st_size, 1_000_200, "the file holds the limit and one notice line")
        self.assertIn("output truncated at 1000000 bytes; 4000000 of 5000000 bytes were dropped", result["tail"])
        self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), result["output_sha256"])

    def test_output_under_the_limit_has_no_truncation_fields(self):
        result = verify.run_command(python("print('hello')"), self.root, self.root / "small.log")
        self.assertEqual(0, result["exit_code"])
        self.assertNotIn("output_truncated", result)
        self.assertEqual("hello\n", Path(result["output"]).read_text())
        self.assertEqual("hello\n", result["tail"])

    def test_a_background_process_that_keeps_the_pipe_does_not_hold_the_command_up(self):
        pid_file = self.root / "background.pid"
        command = f'sleep 60 & echo $! > "{pid_file}"; echo finished'
        started = time.monotonic()
        result = verify.run_command(command, self.root, self.root / "background.log")
        self.assertLess(time.monotonic() - started, 30)
        self.assertEqual(0, result["exit_code"])
        self.assertIn("finished", result["tail"])
        self.addCleanup(self.kill_background, pid_file)  # it outlives the command, on master too

    @staticmethod
    def kill_background(pid_file):
        with contextlib.suppress(OSError, ValueError):
            os.kill(int(pid_file.read_text()), signal.SIGKILL)


class CaptureCliTests(unittest.TestCase):
    def test_the_capture_receipt_flags_output_kept_to_the_limit(self):
        with tempfile.TemporaryDirectory(prefix="capture-cap-") as temporary:
            root = Path(temporary).resolve()
            (root / ".autocode").mkdir()
            previous = Path.cwd()
            os.chdir(root)
            self.addCleanup(os.chdir, previous)
            with patch.object(capped, "LIMIT", 100_000), contextlib.redirect_stdout(io.StringIO()):
                code = capture.cli(
                    [
                        "--output",
                        str(root / ".autocode/flood.json"),
                        "--",
                        sys.executable,
                        "-c",
                        "print('y' * 1_000_000)",
                    ]
                )
            receipt = json.loads((root / ".autocode/flood.json").read_text())
            logged = Path(receipt["full_output"]).stat().st_size
        self.assertEqual(0, code)
        self.assertEqual(
            (True, 1_000_001, 100_000),
            (receipt["output_truncated"], receipt["output_bytes"], receipt["output_limit_bytes"]),
        )
        self.assertLess(logged, 100_200)

    def test_output_under_the_limit_is_unchanged(self):
        with tempfile.TemporaryDirectory(prefix="capture-small-") as temporary:
            root = Path(temporary).resolve()
            (root / ".autocode").mkdir()
            previous = Path.cwd()
            os.chdir(root)
            self.addCleanup(os.chdir, previous)
            with contextlib.redirect_stdout(io.StringIO()):
                code = capture.cli(
                    ["--output", str(root / ".autocode/small.json"), "--", sys.executable, "-c", "print('hi')"]
                )
            receipt = json.loads((root / ".autocode/small.json").read_text())
            logged = Path(receipt["full_output"]).read_text()
        self.assertEqual(0, code)
        self.assertNotIn("output_truncated", receipt)
        self.assertEqual("hi\n", logged)


if __name__ == "__main__":
    unittest.main()
