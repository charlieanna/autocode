"""The editor host refuses an unverified scripted provider, regardless of login PATH."""

import os
import runpy
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
WRAPPER = REPO / "vscode-extension/test/host/held_cli.py"
SCRIPTED = REPO / "scenarios/harness/fake_codex.py"


class HostOfflineProviderTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        reads = self.root / "reads"
        reads.touch()
        config = self.root / "fake-config.json"
        config.write_text("{}")
        self.provider = self.root / "bin/codex"
        self.provider.parent.mkdir()
        shutil.copy2(SCRIPTED, self.provider)
        self.provider.chmod(0o755)
        self.helper = self.provider.with_name("fake_changed_code.py")
        shutil.copy2(SCRIPTED.with_name(self.helper.name), self.helper)
        login = self.root / "login"
        login.mkdir()
        self.alternative = login / "codex"
        self.alternative.touch(mode=0o700)
        env = patch.dict(
            os.environ, {"AUTOCODE_HOST_READS": str(reads), "SCENARIO_FAKE_CONFIG": str(config), "PATH": str(login)}
        )
        env.start()
        self.addCleanup(env.stop)
        self.pin = runpy.run_path(str(WRAPPER))["_offline_fixture"]

    def test_the_scripted_route_wins_over_login_path(self):
        self.assertEqual(str(self.alternative), shutil.which("codex"))
        self.pin()
        self.assertEqual(str(self.provider), shutil.which("codex"))

    def test_a_changed_provider_refuses_to_launch(self):
        self.provider.write_text("changed fixture")
        with self.assertRaisesRegex(ValueError, "cannot identify its scripted provider"):
            self.pin()
        self.assertEqual(str(self.alternative), shutil.which("codex"))

    def test_a_changed_scripted_helper_refuses_to_launch(self):
        self.helper.write_text("changed helper")
        with self.assertRaisesRegex(ValueError, "cannot identify its scripted provider"):
            self.pin()
        self.assertEqual(str(self.alternative), shutil.which("codex"))


if __name__ == "__main__":
    unittest.main()
