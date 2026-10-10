"""The CLI's closed-terminal output wrapper (autocode_detached_output)."""

import errno
import io
import unittest

import autocode_detached_output as detached_output


class Gone(io.StringIO):
    """A stream whose reader went away with ``number``."""

    def __init__(self, number):
        super().__init__()
        self.number = number
        self.attempts = 0

    def write(self, value):
        self.attempts += 1
        raise OSError(self.number, "reader gone")

    def flush(self):
        raise OSError(self.number, "reader gone")


class DetachedOutputTest(unittest.TestCase):
    def test_a_closed_pipe_or_hung_up_terminal_discards_later_output(self):
        # A closed pipe fails with EPIPE; a closed controlling terminal fails with EIO (#454).
        for number in (errno.EPIPE, errno.EIO):
            with self.subTest(errno=errno.errorcode[number]):
                gone = Gone(number)
                output = detached_output.DetachedOutput(gone)
                self.assertEqual(len("stage: started\n"), output.write("stage: started\n"))
                output.flush()
                self.assertEqual(5, output.write("later"))
                self.assertEqual(1, gone.attempts, "nothing more is written to the gone stream")

    def test_a_failing_flush_also_discards(self):
        output = detached_output.DetachedOutput(Gone(errno.EIO))
        output.flush()
        self.assertEqual(3, output.write("one"))

    def test_any_other_error_still_raises(self):
        output = detached_output.DetachedOutput(Gone(errno.ENOSPC))
        with self.assertRaises(OSError):
            output.write("x")
        with self.assertRaises(OSError):
            output.flush()

    def test_a_live_stream_is_written_through(self):
        stream = io.StringIO()
        output = detached_output.DetachedOutput(stream)
        output.write("progress\n")
        output.flush()
        self.assertEqual("progress\n", output.getvalue(), "other attributes are the stream's own")


if __name__ == "__main__":
    unittest.main()
