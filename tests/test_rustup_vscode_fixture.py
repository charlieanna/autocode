"""The offline editor provider uses the fixture's own qualified Python."""

import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.rustup_vscode_fixture import cargo_explicit_command, provider_path


class RustupEditorFixtureTests(unittest.TestCase):
    def test_explicit_cargo_command_preserves_a_path_with_spaces(self):
        program = "/owned/runtime with spaces/bin/cargo"
        command = cargo_explicit_command(program, "stable-toolchain")
        self.assertEqual(shlex.split(command), [program, "+stable-toolchain", "--version"])

    @unittest.skipIf(os.name == "nt", "POSIX env shebang and executable-script fixture")
    def test_env_shebang_finds_fixture_python_and_its_process_dependency(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bin").mkdir()
            cargo_bin = root / "cargo-bin"
            cargo_bin.mkdir()
            script = root / "bin/provider"
            script.write_text("#!/usr/bin/env python3\nimport psutil, sys\nprint(sys.executable)\n")
            script.chmod(0o755)
            env = {**os.environ, "PATH": provider_path(root, cargo_bin, "/usr/bin:/bin")}
            result = subprocess.run([str(script)], env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue(os.path.samefile(result.stdout.strip(), sys.executable))


if __name__ == "__main__":
    unittest.main()
