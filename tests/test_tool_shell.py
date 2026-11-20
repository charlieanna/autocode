"""Bash selection stays fixed and temporary input stays inside the tool boundary."""

import hashlib
import json
import os
import shutil
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
        runtime = {"path": str(self.binary), "alias": str(self.root / "shell-bin/bash")}
        verification = {"manifest": "copy.json", "sha256": "copy-hash"}
        safe = shell.environment(runtime, scratch, "control", original, verification)
        for name in ("HOME", "TMPDIR", "TMP", "TEMP"):
            self.assertEqual(str(scratch), safe[name])
        self.assertEqual(str(self.binary), safe["SHELL"])
        self.assertEqual(str(self.root / "shell-bin") + os.pathsep + original["PATH"], safe["PATH"])
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

    def test_selecting_bash_preserves_every_other_tools_path_precedence(self):
        selected = self.root / "selected/bin"
        selected.mkdir(parents=True)
        alias = self.root / "control/shell-bin/bash"
        alias.parent.mkdir(parents=True)
        self.binary.chmod(0o700)
        alias.symlink_to(self.binary)
        for name in ("python3", "git", "node", "custom-tool"):
            for directory in (self.binary.parent, selected):
                tool = directory / name
                tool.write_text("controlled executable")
                tool.chmod(0o700)
        runtime = {"path": str(self.binary), "alias": str(alias)}
        original = {"PATH": str(selected) + os.pathsep + str(self.binary.parent)}
        safe = shell.environment(runtime, alias.parent.parent / "scratch", "control", original, None)
        for name in ("python3", "git", "node", "custom-tool"):
            with self.subTest(tool=name):
                self.assertEqual(str(selected / name), shutil.which(name, path=safe["PATH"]))
        self.assertEqual(str(alias), shutil.which("bash", path=safe["PATH"]))

    def test_private_alias_identity_refuses_retargeting_and_extra_path_commands(self):
        control = self.root / "control"
        control.mkdir()
        runtime = shell.install_alias(
            {"path": str(self.binary), "sha256": hashlib.sha256(self.binary.read_bytes()).hexdigest()}, control
        )
        alias = Path(runtime["alias"])
        self.assertTrue(shell.unchanged(runtime, control=control))
        self.assertFalse(shell.unchanged(runtime, control=self.root / "foreign"))
        self.assertFalse(shell.unchanged({**runtime, "alias": None}))
        extra = alias.parent / "python3"
        extra.write_text("not an authorized PATH entry")
        self.assertFalse(shell.unchanged(runtime, control=control))
        extra.unlink()
        moved = self.root / "moved-bin"
        alias.parent.rename(moved)
        alias.parent.symlink_to(moved, target_is_directory=True)
        self.assertFalse(shell.unchanged(runtime, control=control))
        alias.parent.unlink()
        moved.rename(alias.parent)
        alias.unlink()
        alias.symlink_to(self.root / "foreign-bash")
        self.assertFalse(shell.unchanged(runtime, control=control))
        alias.unlink()
        alias.parent.rmdir()
        self.assertFalse(shell.unchanged(runtime, control=control))


if __name__ == "__main__":
    unittest.main()
