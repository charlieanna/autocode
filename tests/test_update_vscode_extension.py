"""scripts/update-vscode-extension.sh: build the extension from master and install it, leaving the checkout alone.

npm and the editor command are fakes on PATH; the Git repositories are real. The fake npm writes
the .vsix the script asks for and records the directory it built in, so the test can tell which
source was packaged.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests import ROOT

SCRIPT = ROOT / "scripts" / "update-vscode-extension.sh"

FAKE_NPM = """#!/bin/sh
if [ "$1" = run ] && [ "$2" = package ]; then
  cat marker.txt >> "$LOG_DIR/built"
  printf 'vsix' > "$5"
fi
"""

FAKE_CODE = """#!/bin/sh
printf '%s\\n' "$@" > "$LOG_DIR/code"
test -f "$2" && echo present >> "$LOG_DIR/code"
"""


def git(cwd, *args) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@unittest.skipUnless(shutil.which("git") and shutil.which("bash"), "needs git and bash")
class UpdateVscodeExtensionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        upstream = self.tmp / "upstream"
        (upstream / "vscode-extension").mkdir(parents=True)
        git(upstream, "init", "-q", "-b", "master")
        (upstream / "vscode-extension" / "marker.txt").write_text("master\n")
        git(upstream, "add", ".")
        git(upstream, "commit", "-q", "-m", "master")
        self.checkout = self.tmp / "checkout"
        git(self.tmp, "clone", "-q", str(upstream), str(self.checkout))
        # The person's checkout: another branch with uncommitted work, behind a newer master.
        git(self.checkout, "checkout", "-q", "-b", "my-work")
        (self.checkout / "vscode-extension" / "marker.txt").write_text("my uncommitted work\n")
        (upstream / "vscode-extension" / "marker.txt").write_text("newer master\n")
        git(upstream, "commit", "-q", "-am", "newer master")

        self.logs = self.tmp / "logs"
        self.logs.mkdir()
        bindir = self.tmp / "bin"
        bindir.mkdir()
        for name, body in (("npm", FAKE_NPM), ("code", FAKE_CODE)):
            (bindir / name).write_text(body)
            (bindir / name).chmod(0o755)
        self.env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "LOG_DIR": str(self.logs)}
        self.env.pop("CODE", None)

    def run_script(self, *args, **env):
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            cwd=self.checkout,
            env={**self.env, **env},
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_builds_the_latest_master_and_installs_it_without_touching_the_checkout(self):
        result = self.run_script()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("newer master\n", (self.logs / "built").read_text(), "packaged the fetched master")
        installed = (self.logs / "code").read_text().splitlines()
        self.assertEqual("--install-extension", installed[0])
        self.assertTrue(installed[1].endswith(".vsix"))
        self.assertEqual(["--force", "present"], installed[2:], "the .vsix existed when it was installed")
        self.assertIn("Reload the VS Code window", result.stdout)
        self.assertEqual("my-work", git(self.checkout, "branch", "--show-current"))
        self.assertEqual("my uncommitted work\n", (self.checkout / "vscode-extension" / "marker.txt").read_text())
        self.assertEqual(1, len(git(self.checkout, "worktree", "list").splitlines()), "the build worktree is gone")
        self.assertFalse(Path(installed[1]).exists(), "the .vsix is removed once installed")

    def test_local_builds_the_checkout_as_it_stands(self):
        result = self.run_script("--local")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("my uncommitted work\n", (self.logs / "built").read_text())

    def test_a_missing_editor_command_stops_before_building(self):
        result = self.run_script(CODE="no-such-editor")
        self.assertEqual(1, result.returncode)
        self.assertIn("Cannot find the VS Code command 'no-such-editor'", result.stderr)
        self.assertFalse((self.logs / "built").exists())

    def test_an_unknown_option_is_refused(self):
        result = self.run_script("--bogus")
        self.assertEqual(2, result.returncode)
        self.assertIn("Unknown option: --bogus", result.stderr)


if __name__ == "__main__":
    unittest.main()
