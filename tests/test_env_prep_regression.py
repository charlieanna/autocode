"""The wrong environment-construction order, reproduced in an isolated child."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


class EnvironmentOrderingRegressionTests(unittest.TestCase):
    def test_ac4_clear_before_build_loses_path_and_snapshot_first_preserves_it(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="env-ordering-") as name:
            fixture = Path(name)
            binary = fixture / "bin"
            binary.mkdir()
            provider = binary / "opencode"
            shutil.copy2(repo / "tools" / "fake_opencode.py", provider)
            provider.chmod(0o755)
            workspace = fixture / "project"
            workspace.mkdir()
            (workspace / ".git").mkdir()
            home = fixture / "home"
            home.mkdir()
            child_env = dict(os.environ)
            child_env.update(PATH=str(binary) + os.pathsep + child_env.get("PATH", ""),
                             HOME=str(home), XDG_CONFIG_HOME=str(fixture / "config"),
                             OPENCODE_TEST_MANAGED_CONFIG_DIR=str(fixture / "managed"))
            script = textwrap.dedent("""
                import json, os, sys
                sys.path.insert(0, sys.argv[1])
                from providers import env_prep, opencode
                snapshot = env_prep.snapshot_environment()
                prepared = env_prep.child_environment(snapshot)
                try:
                    os.environ.clear()
                    wrong_order = env_prep.child_environment()
                    assert 'PATH' not in wrong_order
                    try:
                        opencode.local_settings(sys.argv[2], env=wrong_order)
                    except RuntimeError as error:
                        assert 'not on PATH' in str(error)
                        assert 'no provider request was launched' in str(error)
                        wrong_error = str(error)
                    else:
                        raise AssertionError('wrong-order admission unexpectedly succeeded')
                    settings = opencode.local_settings(sys.argv[2], env=prepared)
                    assert settings['version'] == '1.18.31'
                    assert os.environ == {}
                finally:
                    os.environ.clear()
                    os.environ.update(snapshot)
                assert dict(os.environ) == snapshot
                print(json.dumps({'wrong_has_path': 'PATH' in wrong_order,
                                  'wrong_error': wrong_error,
                                  'prepared_version': settings['version'],
                                  'restored': dict(os.environ) == snapshot}))
            """)
            result = subprocess.run([sys.executable, "-c", script, str(repo / "tools"), str(workspace)],
                                    env=child_env, capture_output=True, text=True, timeout=30)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            record = json.loads(result.stdout)
            self.assertFalse(record["wrong_has_path"])
            self.assertIn("no provider request was launched", record["wrong_error"])
            self.assertEqual("1.18.31", record["prepared_version"])
            self.assertTrue(record["restored"])


if __name__ == "__main__":
    unittest.main()
