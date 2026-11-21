"""The offline editor provider uses the fixture's own qualified Python."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.rustup_vscode_fixture import provider_path


class RustupEditorFixtureTests(unittest.TestCase):
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
