"""A reused preflight answer obeys the same output policy as a fresh execution."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from providers import env_prep


class PreflightOutputPolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        binary = self.root / "bin"
        binary.mkdir()
        self.command = ["fixture-tool.exe" if os.name == "nt" else "fixture-tool", "models"]
        self.tool = binary / self.command[0]
        self.tool.write_text("This synthetic file is never executed.\n")
        self.tool.chmod(0o700)
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        for name in ("stdout", "stderr", "encoding", "errors", "binary", "input", "complete"):
            (self.run_dir / name).mkdir()
        self.env = {"PATH": str(binary), "HOME": str(self.root / "home")}
        self.stdout, self.stderr = b"", b""
        self.calls = []
        self.outcome = "success"
        self.after_write = None
        self.stdlib_run, self.stdlib_popen = subprocess.run, subprocess.Popen
        backend = self.execute
        self.enterContext(patch("subprocess.Popen", side_effect=AssertionError("No process in output-policy tests")))
        self.enterContext(patch.object(env_prep, "_STDLIB_RUN", backend))
        self.enterContext(patch.object(subprocess, "run", backend))

    def execute(self, command, **kwargs):
        self.calls.append(kwargs)
        os.write(kwargs["stdout"].fileno(), self.stdout)
        os.write(kwargs["stderr"].fileno(), self.stderr)
        if self.after_write:
            self.after_write()
        if self.outcome == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs.get("timeout"))
        if self.outcome == "failure" and kwargs.get("check"):
            raise subprocess.CalledProcessError(3, command)
        return subprocess.CompletedProcess(command, 3 if self.outcome == "failure" else 0)

    def read(self, **options):
        return env_prep.preflight_run(
            self.command,
            self.env,
            cwd=self.root,
            capture_output=True,
            **{"text": True, "timeout": 5, **options},
        )

    def defaults(self, *, codec="utf-8", utf8_mode=0):
        # Both base and candidate expose their real default-selection dependency;
        # no backend or decoder itself is replaced.
        owner = getattr(env_prep, "output_decoding", env_prep)
        self.enterContext(patch.object(owner, "sys", SimpleNamespace(flags=SimpleNamespace(utf8_mode=utf8_mode))))
        return self.enterContext(patch.object(owner.locale, "getencoding", return_value=codec))

    def test_encoding_changes_cannot_reuse_other_decoded_bytes_in_either_stream(self):
        for stream in ("stdout", "stderr"):
            with self.subTest(stream=stream), env_prep.reuse_checks(self.run_dir / stream):
                self.stdout = b"\xff\r\n" if stream == "stdout" else b""
                self.stderr = b"\xff\r\n" if stream == "stderr" else b""
                self.read(encoding="latin-1", errors="strict")
                with self.assertRaises(UnicodeDecodeError):
                    self.read(encoding="utf-8", errors="strict")
                self.assertEqual(2, len(self.calls))
            self.calls.clear()

    def test_error_policy_changes_cannot_turn_strict_failure_into_cached_success(self):
        for stream in ("stdout", "stderr"):
            with self.subTest(stream=stream), env_prep.reuse_checks(self.run_dir / stream):
                self.stdout = b"\xff" if stream == "stdout" else b""
                self.stderr = b"\xff" if stream == "stderr" else b""
                self.read(encoding="utf-8", errors="replace")
                with self.assertRaises(UnicodeDecodeError):
                    self.read(encoding="utf-8", errors="strict")
                self.assertEqual(2, len(self.calls))
            self.calls.clear()

    def test_valid_encoding_changes_and_non_strict_errors_keep_their_own_text(self):
        for name, raw, first, second, expected in (
            ("encoding", b"\xc3\xa9\r\n", {"encoding": "utf-8"}, {"encoding": "latin-1"}, "Ã©\n"),
            (
                "errors",
                b"\xff\r\n",
                {"encoding": "utf-8", "errors": "ignore"},
                {"encoding": "utf-8", "errors": "replace"},
                "�\n",
            ),
        ):
            with self.subTest(policy=name), env_prep.reuse_checks(self.run_dir / name):
                self.stdout = self.stderr = raw
                self.read(**first)
                result = self.read(**second)
                self.assertEqual((expected, expected), (result.stdout, result.stderr))
                self.assertEqual(2, len(self.calls))
            self.calls.clear()

    def test_identical_policy_reuses_the_passed_check_across_controller_contexts(self):
        self.stdout, self.stderr = b"\xff\r\n", b"diagnostic\r"
        with env_prep.reuse_checks(self.run_dir):
            first = self.read(encoding="latin-1", errors="strict")
        with env_prep.reuse_checks(self.run_dir):
            second = self.read(encoding="latin-1", errors="strict")
        self.assertIsInstance(second, env_prep.KeptCheck)
        self.assertEqual((first.stdout, first.stderr), (second.stdout, second.stderr))
        self.assertEqual(1, len(self.calls))

    def test_changed_parent_default_locale_invalidates_the_decoded_answer(self):
        default = self.defaults(codec="latin-1")
        self.stdout = self.stderr = b"\xff\r\n"
        with env_prep.reuse_checks(self.run_dir):
            self.read()
            default.return_value = "utf-8"
            with self.assertRaises(UnicodeDecodeError):
                self.read()
        self.assertEqual(2, len(self.calls))

    def test_utf8_mode_overrides_locale_and_explicit_codec_ignores_parent_changes(self):
        default = self.defaults(codec="ascii", utf8_mode=1)
        self.stdout = self.stderr = b"\xc3\xa9\r\n"
        with env_prep.reuse_checks(self.run_dir):
            first = self.read()
            default.return_value = "latin-1"
            second = self.read(encoding="utf-8")
        self.assertEqual(("é\n", "é\n"), (first.stdout, first.stderr))
        self.assertEqual((first.stdout, first.stderr), (second.stdout, second.stderr))
        self.assertIsInstance(second, env_prep.KeptCheck)
        self.assertEqual(1, len(self.calls))
        default.assert_not_called()

    def test_one_parent_policy_snapshot_binds_key_and_both_actual_decoders(self):
        default = self.defaults(codec="latin-1")
        self.stdout = self.stderr = b"\xff\r\n"
        self.after_write = lambda: setattr(default, "return_value", "utf-8")
        with env_prep.reuse_checks(self.run_dir):
            try:
                result = self.read()
            except UnicodeDecodeError:
                self.fail("A parent locale change after admission must not change this execution's decoder")
            self.assertEqual(("ÿ\n", "ÿ\n"), (result.stdout, result.stderr))
            self.assertEqual(1, default.call_count, "Resolve once for the cache identity and both output streams")
            with self.assertRaises(UnicodeDecodeError):
                self.read()
        self.assertEqual(2, len(self.calls))

    def test_an_otherwise_valid_legacy_key_is_ignored_and_new_policy_entry_is_reused(self):
        info = self.tool.stat()
        old_identity = [
            self.command,
            str(self.root),
            sorted(self.env.items()),
            str(self.tool.resolve()),
            info.st_mtime_ns,
            info.st_size,
            [],
        ]
        old_key = hashlib.sha256(json.dumps(old_identity, default=str).encode()).hexdigest()
        folder = env_prep.reuse_folder(self.run_dir)
        folder.mkdir()
        (folder / (old_key + ".json")).write_text(
            json.dumps({"at": time.time(), "stdout": "unbound legacy decoded answer", "stderr": ""})
        )
        self.stdout, self.stderr = b"fresh\r\n", b"diagnostic\r\n"
        with env_prep.reuse_checks(self.run_dir):
            first = self.read(encoding="utf-8")
            second = self.read(encoding="utf-8")
        self.assertEqual(("fresh\n", "diagnostic\n"), (first.stdout, first.stderr))
        self.assertEqual(first.stdout, second.stdout)
        self.assertIsInstance(second, env_prep.KeptCheck)
        self.assertEqual(1, len(self.calls))

    def test_listing_freezes_one_parent_default_across_both_attempts(self):
        default = self.defaults(codec="latin-1")
        self.stdout = self.stderr = b"\xff\r\n"

        def change_locale_and_outcome():
            default.return_value = "utf-8"
            self.outcome = "timeout" if len(self.calls) == 1 else "success"

        self.after_write = change_locale_and_outcome
        with env_prep.reuse_checks(self.run_dir):
            try:
                result = env_prep.preflight_listing(
                    self.command, self.env, cwd=self.root, capture_output=True, text=True, timeout=5
                )
            except UnicodeDecodeError:
                self.fail("The listing retry must keep the parent policy selected before its first attempt")
        self.assertEqual(("ÿ\n", "ÿ\n"), (result.stdout, result.stderr))
        self.assertEqual([5, 10], [call["timeout"] for call in self.calls])
        default.assert_called_once()

    def test_warm_timeout_and_listing_use_one_policy_identity(self):
        default = self.defaults(codec="latin-1")
        self.stdout = self.stderr = b"\xff\r\n"
        self.outcome = "timeout"
        with env_prep.reuse_checks(self.run_dir):
            with self.assertRaises(subprocess.TimeoutExpired):
                self.read(warming=True)
            self.outcome = "success"
            self.after_write = lambda: setattr(default, "return_value", "utf-8")
            default.reset_mock()
            try:
                result = env_prep.preflight_listing(
                    self.command, self.env, cwd=self.root, capture_output=True, text=True, timeout=5
                )
            except UnicodeDecodeError:
                self.fail("A warmed listing must decode with the same policy its timeout key identifies")
        self.assertEqual(("ÿ\n", "ÿ\n"), (result.stdout, result.stderr))
        self.assertEqual([5, 10], [call["timeout"] for call in self.calls])
        default.assert_called_once()

    def test_warm_timeout_counts_across_later_codec_error_and_parent_default_changes(self):
        for axis, first, later in (
            ("codec", {"encoding": "latin-1"}, {"encoding": "utf-8"}),
            ("errors", {"encoding": "utf-8", "errors": "replace"}, {"encoding": "utf-8", "errors": "strict"}),
            ("parent-default", {}, {}),
        ):
            directory = self.run_dir / axis
            directory.mkdir(exist_ok=True)
            self.stdout = self.stderr = b"settled timeout output\r\n"
            self.outcome = "timeout"
            default = self.defaults(codec="latin-1") if axis == "parent-default" else None
            with self.subTest(axis=axis), env_prep.reuse_checks(directory):
                with self.assertRaises(subprocess.TimeoutExpired):
                    self.read(warming=True, **first)
                if default is not None:
                    default.return_value = "utf-8"
                with self.assertRaises(subprocess.TimeoutExpired) as expired:
                    env_prep.preflight_listing(
                        self.command, self.env, cwd=self.root, capture_output=True, text=True, timeout=5, **later
                    )
                self.assertEqual(10, expired.exception.timeout)
                self.assertEqual([5, 10], [call["timeout"] for call in self.calls])
            self.calls.clear()

    def test_binary_input_fresh_and_complete_keep_existing_reuse_contracts(self):
        default = self.defaults()
        self.stdout, self.stderr = b"\xff\r\n", b"err\r"
        with env_prep.reuse_checks(self.run_dir / "binary"):
            for _ in range(2):
                result = self.read(text=False)
                self.assertEqual((self.stdout, self.stderr), (result.stdout, result.stderr))
        self.assertEqual(2, len(self.calls))
        default.assert_not_called()
        self.stdout = b"ok\n"
        self.stderr = b""
        with env_prep.reuse_checks(self.run_dir / "input"):
            self.read(input="metadata stdin")
            self.read(input="metadata stdin")
        self.assertEqual(4, len(self.calls))
        with env_prep.reuse_checks(self.run_dir / "complete"):
            self.read(complete=lambda text: False)
            self.read()
            self.read(complete=lambda text: False)
            self.read(fresh=True)
            self.read()
        self.assertEqual(8, len(self.calls))

    def test_encoding_and_universal_aliases_without_text_remain_uncached(self):
        self.stdout, self.stderr = b"out\r\n", b"err\r"
        for name, options in (
            ("encoding-only", {"text": None, "encoding": "utf-8"}),
            ("universal-only", {"text": None, "universal_newlines": True}),
        ):
            directory = self.run_dir / name
            directory.mkdir()
            with self.subTest(options=options), env_prep.reuse_checks(directory):
                for _ in range(2):
                    result = self.read(**options)
                    self.assertEqual(("out\n", "err\n"), (result.stdout, result.stderr))
            self.assertFalse(env_prep.reuse_folder(directory).exists())
        self.assertEqual(4, len(self.calls))

    def test_cached_and_fresh_conflicting_aliases_match_stdlib_without_launch(self):
        self.stdout = b"valid\r\n"
        with env_prep.reuse_checks(self.run_dir):
            first = self.read(universal_newlines=True)
            # The real constructor checks these options before _execute_child.
            # Keep that actual stdlib comparator while forbidding every launch.
            with (
                patch.object(env_prep, "_STDLIB_RUN", self.stdlib_run),
                patch.object(subprocess, "run", self.stdlib_run),
                patch.object(subprocess, "Popen", self.stdlib_popen),
                patch.object(
                    self.stdlib_popen, "_execute_child", side_effect=AssertionError("No process may execute")
                ) as execute,
            ):
                for fresh in (False, True):
                    with (
                        self.subTest(fresh=fresh),
                        self.assertRaisesRegex(subprocess.SubprocessError, "Cannot disambiguate"),
                    ):
                        self.read(universal_newlines=False, fresh=fresh)
                execute.assert_not_called()
            second = self.read()
        self.assertEqual(first.stdout, second.stdout)
        self.assertIsInstance(second, env_prep.KeptCheck)
        self.assertEqual(1, len(self.calls))

    def test_failure_and_timeout_never_cache_and_partial_byte_policy_is_preserved(self):
        self.stdout, self.stderr = b"part\r\n\xc3", b"err\r\n\xc3"
        self.outcome = "timeout"
        with env_prep.reuse_checks(self.run_dir):
            with self.assertRaises(subprocess.TimeoutExpired) as expired:
                self.read(encoding="utf-8")
            self.assertEqual(("part\n�", "err\n�"), (expired.exception.stdout, expired.exception.stderr))
            with self.assertRaises(UnicodeDecodeError):
                self.read(encoding="utf-8", errors="strict")
            self.assertEqual(2, len(self.calls))
            self.stdout, self.stderr = b"out\r\n", b"err\r\n"
            self.outcome = "failure"
            with self.assertRaises(subprocess.CalledProcessError) as failed:
                self.read(encoding="utf-8", check=True)
            self.assertEqual(("out\n", "err\n"), (failed.exception.output, failed.exception.stderr))
            self.read(encoding="utf-8")
            self.read(encoding="utf-8")
        self.assertEqual(5, len(self.calls))
        self.assertFalse(env_prep.reuse_folder(self.run_dir).exists())


if __name__ == "__main__":
    unittest.main()
