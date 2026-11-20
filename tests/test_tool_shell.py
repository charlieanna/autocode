"""Bash selection stays fixed and temporary input stays inside the tool boundary."""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_tool_shell as shell


@unittest.skipUnless(os.name == "posix", "Native Bash tool boundaries require a supported POSIX host")
class ToolShellTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.prefix = self.root / "brew"
        self.binary = self.prefix / "Cellar/bash/9/bin/bash"
        self.binary.parent.mkdir(parents=True)
        self.binary.write_bytes(b"qualified fixture")
        self.entry = self.prefix / "opt/bash"
        self.entry.parent.mkdir(parents=True)
        self.entry.symlink_to(self.binary.parent.parent, target_is_directory=True)
        self.enterContext(patch.object(shell, "HOMEBREW_PREFIXES", (self.prefix,)))

    def test_mac_selects_the_fixed_homebrew_installation_and_ignores_path_shims(self):
        with patch.object(shell.sys, "platform", "darwin"), patch.dict(os.environ, {"PATH": str(self.root)}):
            self.assertEqual(self.binary, shell.executable())
            with patch.object(shell.macho, "dependencies", return_value={self.binary}) as dependencies:
                runtime = shell.runtime()
        dependencies.assert_called_once_with(self.binary)
        self.assertEqual(str(self.binary), runtime["path"])
        self.assertEqual([str(self.binary)], runtime["read_roots"])
        self.assertEqual(hashlib.sha256(self.binary.read_bytes()).hexdigest(), runtime["sha256"])
        self.assertTrue(shell.unchanged(runtime))
        self.binary.write_bytes(b"replaced")
        self.assertFalse(shell.unchanged(runtime))
        for malformed in ({}, {"path": 1, "sha256": "bad"}, None):
            self.assertFalse(shell.unchanged(malformed))

    def test_a_homebrew_alias_outside_its_bash_cellar_does_not_select_a_foreign_executable(self):
        self.entry.unlink()
        foreign = self.root / "foreign"
        (foreign / "bin").mkdir(parents=True)
        (foreign / "bin/bash").write_bytes(b"foreign")
        self.entry.symlink_to(foreign, target_is_directory=True)
        with patch.object(shell.sys, "platform", "darwin"):
            self.assertEqual(Path("/bin/bash").resolve(), shell.executable())

    def test_linux_keeps_the_native_system_bash_and_its_observed_loader_closure(self):
        with (
            patch.object(shell.sys, "platform", "linux"),
            patch.object(shell.elf, "dependencies", return_value={Path("/bin/bash").resolve()}) as dependencies,
        ):
            runtime = shell.runtime()
        dependencies.assert_called_once_with(Path("/bin/bash").resolve())
        self.assertEqual(str(Path("/bin/bash").resolve()), runtime["path"])

    def test_environment_keeps_scratch_and_selected_runtime_without_ambient_credentials(self):
        scratch = self.root / "scratch"
        context = {"attempt": "attempt", "nonce": "nonce", "source_revision": "revision"}
        original = {
            "PATH": "/bin:/usr/bin",
            "OPENAI_API_KEY": "never-copy",
            "AUTOCODE_CAPTURE_CONTEXT": json.dumps(context),
        }
        runtime = {"path": str(self.binary)}
        verification = {"manifest": "copy.json", "sha256": "copy-hash"}
        safe = shell.environment(runtime, scratch, "control", original, verification)
        for name in ("HOME", "TMPDIR", "TMP", "TEMP"):
            self.assertEqual(str(scratch), safe[name])
        self.assertEqual(str(self.binary), safe["SHELL"])
        self.assertEqual(str(self.binary.parent) + os.pathsep + original["PATH"], safe["PATH"])
        self.assertEqual("copy.json", safe["AUTOCODE_VERIFICATION_COPY"])
        self.assertEqual("copy-hash", safe["AUTOCODE_VERIFICATION_COPY_SHA256"])
        self.assertEqual(context, json.loads(safe["AUTOCODE_CAPTURE_CONTEXT"]))
        self.assertNotIn("OPENAI_API_KEY", safe)
        plain = shell.environment(runtime, scratch, "control", {}, None)
        self.assertNotIn("AUTOCODE_VERIFICATION_COPY", plain)
        self.assertNotIn("AUTOCODE_CAPTURE_CONTEXT", plain)
        for malformed in ("[]", "{}", '{"attempt":1}'):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                shell.environment(runtime, scratch, "control", {"AUTOCODE_CAPTURE_CONTEXT": malformed}, None)


if __name__ == "__main__":
    unittest.main()
