"""The offline editor provider uses the fixture's own qualified Python."""

import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.rustup_vscode_fixture import cargo_explicit_command, normalized_review, provider_path


class RustupEditorFixtureTests(unittest.TestCase):
    def test_explicit_cargo_command_preserves_a_path_with_spaces(self):
        program = "/owned/runtime with spaces/bin/cargo"
        command = cargo_explicit_command(program, "stable-toolchain")
        self.assertEqual(shlex.split(command), [program, "+stable-toolchain", "--version"])

    def test_normalized_review_handles_literal_and_markdown_escaped_paths(self):
        program = "/home/runner/.cargo/bin/cargo"
        review = r"Run /home/runner/\.cargo/bin/cargo and `/home/runner/.cargo/bin/cargo --version`. Keep other\.text."
        self.assertEqual(normalized_review(review, program), r"Run cargo and `cargo --version`. Keep other\.text.")

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
