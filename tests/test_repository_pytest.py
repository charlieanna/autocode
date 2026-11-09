"""AutoCode's self-hosted pytest suite respects its repository test boundary."""

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_verify as verify

REPO = Path(__file__).resolve().parents[1]


class RepositoryPytestTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        shutil.copyfile(REPO / "pyproject.toml", self.root / "pyproject.toml")
        self.write("tests/__init__.py", "")
        self.write("tests/test_root.py", "def test_root():\n    assert 2 + 2 == 4\n")
        for directory in ("seed", "reference", "broken", "hidden"):
            self.write(
                f"scenarios/catalog/example/{directory}/test_fixture.py",
                "raise RuntimeError('independent scenario project was collected')\n",
            )
        self.env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1"}
        self.env.pop("PYTEST_ADDOPTS", None)
        self.env.pop("PYTEST_PLUGINS", None)

    def write(self, name, source):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)

    def suite(self, *paths):
        framework = verify.detect_framework(self.root, python=sys.executable)
        self.assertEqual("pytest", framework.name)
        return subprocess.run(
            [*shlex.split(framework.suite), *paths],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_detected_default_suite_runs_root_tests_and_still_rejects_their_failure(self):
        result = self.suite()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("1 passed", result.stdout)
        self.write("tests/test_root.py", "def test_root():\n    assert 2 + 2 == 5\n")
        result = self.suite()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("1 failed", result.stdout)
        self.assertNotIn("independent scenario project was collected", result.stdout)

    def test_explicit_scenario_paths_override_the_default_test_root(self):
        selected = "scenarios/catalog/explicit/seed/test_explicit.py"
        self.write(selected, "def test_explicit():\n    assert 'scenario'.startswith('scen')\n")
        result = self.suite(selected)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("1 passed", result.stdout)
        self.write(selected, "def test_explicit():\n    assert False, 'scenario failure is still checked'\n")
        result = self.suite(selected)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("scenario failure is still checked", result.stdout)


if __name__ == "__main__":
    unittest.main()
