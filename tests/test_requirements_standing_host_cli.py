"""The offline Requirements editor never selects an ambient provider."""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from . import requirements_standing_host_cli as host


class RequirementsStandingHostProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.bindir = self.root / "bin"
        self.bindir.mkdir()
        self.provider = self.bindir / "codex"
        script = host.REPO / "tests/requirements_standing_provider.py"
        self.provider.write_text(
            f'#!{sys.executable}\nimport runpy\nrunpy.run_path({str(script)!r}, run_name="__main__")\n'
        )
        self.provider.chmod(0o755)
        self.base = self.bindir / "base_codex.py"
        self.helper = self.bindir / "fake_changed_code.py"
        shutil.copy2(host.REPO / "scenarios/harness/fake_codex.py", self.base)
        shutil.copy2(host.REPO / "scenarios/harness/fake_changed_code.py", self.helper)
        config = self.root / "fake-config.json"
        config.write_text("{}")
        reads = self.root / "reads"
        reads.touch()
        login = self.root / "login"
        login.mkdir()
        self.ambient = login / "codex"
        self.ambient.touch(mode=0o700)
        self.enterContext(
            patch.dict(
                os.environ,
                {
                    "PATH": str(login),
                    "AUTOCODE_HOST_READS": str(reads),
                    "AUTOCODE_HOME": str(self.root / "registry"),
                    "AUTOCODE_SOURCE_HOST": "0",
                    "STANDING_BASE_PROVIDER": str(self.base),
                    "SCENARIO_FAKE_CONFIG": str(config),
                },
            )
        )

    def test_owned_script_wins_over_a_conflicting_login_path(self):
        self.assertEqual(str(self.ambient), shutil.which("codex"))
        host.pin_provider()
        self.assertEqual(str(self.provider), shutil.which("codex"))

    def test_changed_or_unbound_fixture_refuses_before_selecting_provider(self):
        original = {path: path.read_bytes() for path in (self.provider, self.base, self.helper)}
        for path in original:
            with self.subTest(path=path.name):
                try:
                    path.write_bytes(original[path] + b"\n# changed\n")
                    with self.assertRaisesRegex(ValueError, "cannot identify"):
                        host.pin_provider()
                    self.assertEqual(str(self.ambient), shutil.which("codex"))
                finally:
                    path.write_bytes(original[path])
        for name in ("STANDING_BASE_PROVIDER", "SCENARIO_FAKE_CONFIG", "AUTOCODE_HOME", "AUTOCODE_SOURCE_HOST"):
            with self.subTest(name=name), patch.dict(os.environ, {name: "not-the-owned-fixture"}):
                with self.assertRaisesRegex(ValueError, "cannot identify"):
                    host.pin_provider()
                self.assertEqual(str(self.ambient), shutil.which("codex"))


if __name__ == "__main__":
    unittest.main()
