"""scripts/publish-vscode-extension.sh: test, package and publish master's extension at a rising version.

npm and vsce are fakes; the Git repository is real, so the version comes from a real commit count.
The fake npm logs each call, rewrites package.json on `npm version` and writes the .vsix it is
asked for. The fake vsce logs its arguments and whether the .vsix existed when it was called.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests import ROOT

SCRIPT = ROOT / "scripts" / "publish-vscode-extension.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "publish-vscode-extension.yml"

FAKE_NPM = """#!/bin/sh
echo "npm $*" >> "$LOG_DIR/calls"
case "$1 $2" in
  "pkg get") sed -n 's/.*"version": *"\\([^"]*\\)".*/"\\1"/p' package.json ;;
  "version "*) printf '{"version": "%s"}\\n' "$2" > package.json ;;
  "test ") exit "${FAIL_TEST:-0}" ;;
  "run package") printf 'vsix' > "$5" ;;
esac
"""

FAKE_VSCE = """#!/bin/sh
echo "vsce $*" >> "$LOG_DIR/calls"
for last; do :; done
test -f "$last" && echo "vsix present" >> "$LOG_DIR/calls"
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
class PublishVscodeExtensionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        self.extension = self.repo / "vscode-extension"
        (self.extension / "node_modules" / ".bin").mkdir(parents=True)
        (self.repo / ".gitignore").write_text("node_modules/\n")
        (self.extension / "package.json").write_text('{"version": "0.1.0"}\n')
        (self.extension / "package-lock.json").write_text('{"version": "0.1.0"}\n')
        git(self.tmp, "init", "-q", "-b", "master", str(self.repo))
        for n in range(3):
            (self.repo / "change.txt").write_text(f"{n}\n")
            git(self.repo, "add", ".")
            git(self.repo, "commit", "-q", "-m", f"commit {n}")
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "npm").write_text(FAKE_NPM)
        (bin_dir / "npm").chmod(0o755)
        vsce = self.extension / "node_modules" / ".bin" / "vsce"
        vsce.write_text(FAKE_VSCE)
        vsce.chmod(0o755)
        self.log_dir = self.tmp / "log"
        self.log_dir.mkdir()
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "LOG_DIR": str(self.log_dir),
            "TMPDIR": str(self.tmp),
        }

    def run_script(self, *args, cwd=None, **env):
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            cwd=cwd or self.repo,
            env={**self.env, **env},
            capture_output=True,
            text=True,
        )

    def calls(self) -> list[str]:
        path = self.log_dir / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def assert_package_files_restored(self):
        self.assertEqual('{"version": "0.1.0"}\n', (self.extension / "package.json").read_text())
        self.assertEqual('{"version": "0.1.0"}\n', (self.extension / "package-lock.json").read_text())
        self.assertEqual("", git(self.repo, "status", "--porcelain"))

    def test_tests_then_publishes_major_minor_and_commit_count_with_trusted_publishing(self):
        result = self.run_script()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Published autocode-task-chat 0.1.3.", result.stdout)
        calls = self.calls()
        self.assertEqual(
            [
                "npm pkg get version",
                "npm ci",
                "npm version 0.1.3 --no-git-tag-version --allow-same-version",
                "npm run typecheck",
                "npm test",
            ],
            calls[:5],
        )
        self.assertTrue(calls[5].startswith("npm run package -- --out "), calls[5])
        vsix = calls[5].split("--out ", 1)[1]
        self.assertTrue(vsix.endswith("/autocode-task-chat-0.1.3.vsix"))
        self.assertEqual([f"vsce publish --oidc --skip-duplicate --packagePath {vsix}", "vsix present"], calls[6:])
        self.assertFalse(Path(vsix).exists(), "the .vsix is removed afterwards")
        self.assert_package_files_restored()

    def test_a_new_commit_publishes_a_higher_version(self):
        (self.repo / "change.txt").write_text("newer\n")
        git(self.repo, "commit", "-q", "-am", "newer")

        result = self.run_script(cwd=self.extension)

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("npm version 0.1.4 --no-git-tag-version --allow-same-version", self.calls())

    def test_a_failing_test_publishes_nothing(self):
        result = self.run_script(FAIL_TEST="1")

        self.assertNotEqual(0, result.returncode)
        self.assertFalse([call for call in self.calls() if call.startswith(("vsce", "npm run package"))])
        self.assert_package_files_restored()

    def test_dry_run_packages_without_publishing(self):
        result = self.run_script("--dry-run")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Packaged autocode-task-chat 0.1.3; not published (--dry-run).", result.stdout)
        self.assertTrue(any(call.startswith("npm run package") for call in self.calls()))
        self.assertFalse([call for call in self.calls() if call.startswith("vsce")])
        self.assert_package_files_restored()

    def test_a_shallow_clone_is_refused_before_anything_runs(self):
        shallow = self.tmp / "shallow"
        git(self.tmp, "clone", "-q", "--depth", "1", f"file://{self.repo}", str(shallow))

        result = self.run_script(cwd=shallow)

        self.assertEqual(1, result.returncode)
        self.assertIn("shallow clone", result.stderr)
        self.assertEqual([], self.calls())

    def test_the_workflow_publishes_only_master_after_setup_with_oidc_and_full_history(self):
        workflow = WORKFLOW.read_text()

        self.assertIn("if: github.ref == 'refs/heads/master' && vars.VSCODE_MARKETPLACE_PUBLISH == 'true'", workflow)
        self.assertIn("branches: [master]", workflow)
        self.assertIn("name: vscode-marketplace", workflow)
        self.assertEqual(1, workflow.count("id-token: write"), "only the publish job may request an OIDC token")
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("run: scripts/publish-vscode-extension.sh\n", workflow)
        self.assertNotIn("secrets.", workflow)


if __name__ == "__main__":
    unittest.main()
