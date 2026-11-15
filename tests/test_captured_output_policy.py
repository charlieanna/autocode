"""Owned capture freezes its output policy and carries input only through private stdin."""

import contextlib
import os
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import autocode_captured_process as captured
import autocode_output_decoding as decoding


class CapturedOutputPolicyTests(unittest.TestCase):
    def setUp(self):
        self.preflight = self.enterContext(patch.object(captured, "preflight"))
        self.launch = self.enterContext(patch("subprocess.Popen", side_effect=AssertionError("No target may launch")))
        self.enterContext(patch.object(captured, "_linux", self.execute))
        self.enterContext(patch.object(captured, "_sampled", self.execute))
        self.calls = []
        self.deadlines = []
        self.answer = lambda output, errors, stdin: (0, False)

    def execute(self, command, timeout, env, cwd, output, errors, stdin):
        self.calls.append(stdin)
        self.deadlines.append(timeout)
        return self.answer(output, errors, stdin)

    def capture(self, **options):
        try:
            return captured.run(["never-executed"], **options)
        except TypeError as error:
            if "unexpected keyword argument" in str(error):
                self.fail(f"Owned capture must accept its supported input contract: {error}")
            raise

    def test_default_codec_is_frozen_before_execution_for_both_streams_and_timeout(self):
        with (
            patch.object(decoding, "sys", SimpleNamespace(flags=SimpleNamespace(utf8_mode=0))),
            patch.object(decoding.locale, "getencoding", return_value="latin-1") as default,
        ):
            for expired in (False, True):
                with self.subTest(expired=expired):
                    default.return_value = "latin-1"
                    default.reset_mock()

                    def answer(output, errors, stdin):
                        os.write(output.fileno(), b"\xff\r\n")
                        os.write(errors.fileno(), b"\xff\r")
                        default.return_value = "utf-8"
                        return 0, expired

                    self.answer = answer
                    try:
                        if expired:
                            with self.assertRaises(subprocess.TimeoutExpired) as caught:
                                self.capture(timeout=5)
                            result = caught.exception
                        else:
                            result = self.capture(timeout=5)
                    except UnicodeDecodeError:
                        self.fail(
                            "The parent locale changed after execution began; this read must keep its original codec"
                        )
                    self.assertEqual(("ÿ\n", "ÿ\n"), (result.stdout, result.stderr))
                    default.assert_called_once()
        self.launch.assert_not_called()

    def test_small_and_large_text_and_bytes_round_trip_through_private_stdin(self):
        for text, payload, encoding in (
            (False, b"\xff\r\n", None),
            (False, bytes(range(256)) * 1024, None),
            (True, "é\r\n", "latin-1"),
            (True, "é\r\n" * 65536, "utf-8"),
        ):
            with self.subTest(text=text, size=len(payload), encoding=encoding):
                raw = payload.encode(encoding) if text else payload

                def echo(output, errors, stdin):
                    self.assertGreater(stdin.fileno(), 2)
                    self.assertEqual(raw, stdin.read())
                    os.write(output.fileno(), raw)
                    os.write(errors.fileno(), raw)
                    return 0, False

                self.answer = echo
                result = self.capture(input=payload, text=text, encoding=encoding, timeout=5)
                expected = payload.replace("\r\n", "\n").replace("\r", "\n") if text else payload
                self.assertEqual((expected, expected), (result.stdout, result.stderr))
                self.assertTrue(self.calls[-1].closed, "Only the supplied-input private file belongs to capture")
        self.launch.assert_not_called()

    def test_omitted_and_none_input_leave_existing_stdin_untouched(self):
        with tempfile.TemporaryFile("w+b") as stream:
            for stdin in (None, subprocess.DEVNULL, stream):
                for options in ({}, {"input": None}):
                    with self.subTest(stdin=stdin, options=options):
                        self.capture(stdin=stdin, **options)
                        self.assertIs(stdin, self.calls[-1])
                        self.assertFalse(stream.closed)
        self.launch.assert_not_called()

    def test_input_and_stdin_conflict_before_preflight_or_execution(self):
        with self.assertRaisesRegex(ValueError, "stdin and input arguments may not both be used"):
            self.capture(input=b"payload", stdin=subprocess.DEVNULL, text=False)
        self.preflight.assert_not_called()
        self.assertEqual([], self.calls)
        self.launch.assert_not_called()

    def test_metadata_timeout_keeps_requested_bound_and_reduces_only_execution(self):
        scope = SimpleNamespace(remaining_timeout=lambda: 2.5)

        def expired(output, errors, stdin):
            os.write(output.fileno(), b"part\xc3")
            os.write(errors.fileno(), b"diagnostic\r\n")
            return 0, True

        self.answer = expired
        with (
            patch.object(captured.custody, "admission", return_value=contextlib.nullcontext(scope)),
            self.assertRaises(subprocess.TimeoutExpired) as caught,
        ):
            captured.run_metadata(["never-executed"], timeout=10, encoding="utf-8")
        self.assertEqual([2.5], self.deadlines)
        self.assertEqual(
            (10, "part�", "diagnostic\n"), (caught.exception.timeout, caught.exception.stdout, caught.exception.stderr)
        )
        self.assertEqual(2.5, getattr(caught.exception, "effective_timeout", None))
        self.launch.assert_not_called()

    def test_invalid_input_types_and_text_encoding_fail_before_target(self):
        for options, error in (
            ({"input": b"bytes", "text": True}, AttributeError),
            ({"input": "text", "text": False}, TypeError),
            ({"input": "€", "encoding": "ascii"}, UnicodeEncodeError),
            ({"input": "€", "encoding": "ascii", "errors": "strict"}, UnicodeEncodeError),
        ):
            with self.subTest(options=options), self.assertRaises(error):
                self.capture(**options)
        self.assertEqual([], self.calls)
        self.launch.assert_not_called()

    def test_unsupported_redirection_does_not_open_a_raw_subprocess_escape(self):
        for options, error in (
            ({"capture_output": False}, ValueError),
            ({"stdout": subprocess.DEVNULL}, TypeError),
            ({"stderr": subprocess.DEVNULL}, TypeError),
            ({"pass_fds": ()}, TypeError),
        ):
            with self.subTest(options=options), self.assertRaises(error):
                captured.run(["never-executed"], **options)
        self.preflight.assert_not_called()
        self.assertEqual([], self.calls)
        self.launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
