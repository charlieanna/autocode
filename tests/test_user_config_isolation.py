"""A test never reads the developer's own AutoCode user config.

tests/__init__.py gives every test an empty XDG_CONFIG_HOME and no AUTOCODE_PROVIDER; see the comment there.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests import USER_CONFIG_MARKER

ROOT = Path(__file__).resolve().parents[1]
CHOICE = "import json, autocode_providers as p; print(json.dumps([*p.default_choice(), str(p.user_config_path())]))"


def run(code, environment):
    command = [sys.executable, "-B", "-c", code]
    return subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, check=True).stdout


class UserConfigIsolationTests(unittest.TestCase):
    def test_a_developer_who_sends_new_runs_to_kilo_does_not_change_what_a_test_runs(self):
        with tempfile.TemporaryDirectory() as developer:
            config = Path(developer) / "autocode" / "config.toml"
            config.parent.mkdir()
            config.write_text('default_provider = "kilocode"\n')
            # The developer's shell that starts the suite, not a process a test started.
            environment = {**os.environ, "XDG_CONFIG_HOME": developer, "AUTOCODE_PROVIDER": "kilocode"}
            environment.pop(USER_CONFIG_MARKER, None)
            # Without the test package, that developer's machine picks Kilo for a new run.
            outside = json.loads(run("import sys; sys.path.insert(0, 'tools'); " + CHOICE, environment))
            self.assertEqual(["kilocode", "AUTOCODE_PROVIDER"], outside[:2])
            provider, source, path = json.loads(run("import tests; " + CHOICE, environment))
        self.assertEqual(("opencode", "built-in default"), (provider, source))
        self.assertFalse(Path(path).is_relative_to(developer), path)

    def test_a_process_a_test_starts_keeps_the_config_home_that_test_chose(self):
        """A fixture provider that imports the test package must see the config its test wrote for it."""
        with tempfile.TemporaryDirectory() as chosen:
            environment = {**os.environ, "XDG_CONFIG_HOME": chosen}
            seen = run("import os, tests; print(os.environ['XDG_CONFIG_HOME'])", environment)
        self.assertEqual(chosen, seen.strip())


if __name__ == "__main__":
    unittest.main()
