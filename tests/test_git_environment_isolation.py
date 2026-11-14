"""A suite started by git never works in the repository git started it from.

tests/__init__.py drops GIT_DIR and its companions for the first test process; see the comment there.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests import USER_CONFIG_MARKER

ROOT = Path(__file__).resolve().parents[1]
# What a fixture does: start a repository in its own temporary folder and name its author there.
FIXTURE = (
    "import subprocess, sys; "
    "subprocess.run(['git', 'init', '-q', sys.argv[1]], check=True, capture_output=True); "
    "subprocess.run(['git', '-C', sys.argv[1], 'config', 'user.name', 'Fixture Person'], check=True)"
)


def run(code, environment, *arguments):
    command = [sys.executable, "-B", "-c", code, *arguments]
    return subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, check=True).stdout


def author(repository):
    result = subprocess.run(
        ["git", "config", "--file", str(Path(repository) / ".git" / "config"), "user.name"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


class GitEnvironmentIsolationTests(unittest.TestCase):
    def developer(self, folder):
        """The environment `git bisect run` or a hook gives its command in the developer's repository."""
        subprocess.run(["git", "init", "-q", folder], check=True, capture_output=True)
        environment = {**os.environ, "GIT_DIR": str(Path(folder) / ".git"), "GIT_WORK_TREE": folder}
        environment.pop(USER_CONFIG_MARKER, None)
        return environment

    def test_a_suite_started_by_git_keeps_its_fixtures_out_of_the_developers_repository(self):
        with tempfile.TemporaryDirectory() as developer, tempfile.TemporaryDirectory() as fixture:
            environment = self.developer(developer)
            # Without the test package, the fixture's git writes to the repository git named.
            run(FIXTURE, environment, str(Path(fixture) / "outside"))
            self.assertEqual("Fixture Person", author(developer))
        with tempfile.TemporaryDirectory() as developer, tempfile.TemporaryDirectory() as fixture:
            environment = self.developer(developer)
            repository = Path(fixture) / "inside"
            run("import tests; " + FIXTURE, environment, str(repository))
            self.assertEqual("", author(developer))
            self.assertEqual("Fixture Person", author(repository))

    def test_a_process_a_test_starts_keeps_the_git_variables_that_test_chose(self):
        with tempfile.TemporaryDirectory() as scratch:
            index = str(Path(scratch) / "index")
            environment = {**os.environ, "GIT_INDEX_FILE": index}
            seen = run("import os, tests; print(os.environ['GIT_INDEX_FILE'])", environment)
        self.assertEqual(index, seen.strip())


if __name__ == "__main__":
    unittest.main()
