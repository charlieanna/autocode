"""A suite or command started by git never works in the repository git started it from.

tests/__init__.py drops GIT_DIR and its companions for the first test process, and
AutoCode drops them at CLI startup and in its Git/scratch-index helpers (#3504, #3505).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import autocode_source_host as host
import autocode_util as util
import autocode_verify as verify
import autocode_workspaces as workspaces
import autocode_worktrees as worktrees

import tests
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


def _plain_git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=T", "-c", "user.email=t@example.test", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _init_committed_repo(root: Path, branch: str, files: dict[str, str]) -> str:
    root.mkdir(parents=True, exist_ok=True)
    _plain_git(root, "init", "-q", "-b", branch)
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _plain_git(root, "add", ".")
    _plain_git(root, "commit", "-qm", f"seed {branch}")
    return _plain_git(root, "rev-parse", "HEAD")


class GitRepositoryIsolationTests(unittest.TestCase):
    def test_repository_variable_list_matches_git_local_env_vars_and_stays_unified(self):
        observed = subprocess.run(
            ["git", "rev-parse", "--local-env-vars"], check=True, capture_output=True, text=True
        ).stdout.splitlines()
        self.assertTrue((set(observed) - {"GIT_CONFIG_COUNT"}).issubset(util.GIT_REPOSITORY_VARIABLES))
        self.assertEqual(util.GIT_REPOSITORY_VARIABLES, tests.GIT_REPOSITORY_VARIABLES)
        self.assertEqual(util.GIT_REPOSITORY_VARIABLES, host.GIT_REPOSITORY_VARIABLES)

    def test_git_helpers_and_scratch_indexes_operate_on_the_named_workspace_not_exported_git_dir(self):
        with tempfile.TemporaryDirectory(prefix="git-iso-helpers-") as tmp:
            temp = Path(tmp).resolve()
            repo_a = temp / "repo-a"
            repo_b = temp / "repo-b"
            _init_committed_repo(repo_a, "main-A", {"only-a.txt": "from A\n"})
            head_b = _init_committed_repo(repo_b, "main-B", {"only-b.txt": "from B\n"})
            (repo_b / "only-b.txt").write_text("from B edited\n", encoding="utf-8")
            (repo_b / "added-b.txt").write_text("new in B\n", encoding="utf-8")
            repo_unborn = temp / "repo-unborn"
            repo_unborn.mkdir()
            _plain_git(repo_unborn, "init", "-q", "-b", "main-U")
            empty_dir = temp / "empty-dir"
            empty_dir.mkdir()

            head_a = _plain_git(repo_a, "rev-parse", "HEAD")
            status_a = _plain_git(repo_a, "status", "--porcelain")
            index_a = (repo_a / ".git" / "index").read_bytes()

            exported = {
                "GIT_DIR": str(repo_a / ".git"),
                "GIT_WORK_TREE": str(repo_a),
                "GIT_INDEX_FILE": str(repo_a / ".git" / "index"),
                "GIT_COMMON_DIR": str(repo_a / ".git"),
                "GIT_OBJECT_DIRECTORY": str(repo_a / ".git" / "objects"),
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(repo_a / ".git" / "objects"),
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "autocode.testmarker",
                "GIT_CONFIG_VALUE_0": "kept",
            }
            with mock.patch.dict(os.environ, exported, clear=False):
                self.assertEqual("main-B", util.git(repo_b, "rev-parse", "--abbrev-ref", "HEAD"))
                self.assertEqual("main-B", verify._git(repo_b, "rev-parse", "--abbrev-ref", "HEAD").strip())
                self.assertEqual("kept", util.git(repo_b, "config", "--get", "autocode.testmarker"))
                self.assertTrue(util._unborn(repo_unborn))
                self.assertFalse(util._unborn(repo_b))

                snap = util.snapshot(repo_b)
                self.assertIn("only-b.txt", snap["files"])
                self.assertIn("added-b.txt", snap["files"])
                self.assertNotIn("only-a.txt", snap["files"])

                with verify._staged(repo_b, head_b) as (staged_env, repositories):
                    self.assertEqual([], repositories)
                    diff_names = verify._git(repo_b, "diff-index", "--cached", "--name-only", head_b, env=staged_env)
                    self.assertEqual(
                        diff_names.strip(),
                        util.git(repo_b, "diff-index", "--cached", "--name-only", head_b, env=staged_env),
                    )
                    self.assertIn("only-b.txt", diff_names)
                    self.assertIn("added-b.txt", diff_names)
                    self.assertNotIn("only-a.txt", diff_names)

                with verify._effective_base(repo_b, head_b) as (entries, _, _):
                    self.assertIn("only-b.txt", entries)
                    self.assertNotIn("only-a.txt", entries)

                tree_b = worktrees.source_tree(repo_b)
                listed_b = util.git(repo_b, "ls-tree", "-r", "--name-only", tree_b).splitlines()
                self.assertEqual(["added-b.txt", "only-b.txt"], sorted(listed_b))

                workspaces.bootstrap(empty_dir)
                self.assertTrue((empty_dir / ".git").is_dir())
                self.assertEqual(
                    str(empty_dir), str(Path(util.git(empty_dir, "rev-parse", "--show-toplevel")).resolve())
                )

            self.assertEqual(head_a, _plain_git(repo_a, "rev-parse", "HEAD"))
            self.assertEqual(status_a, _plain_git(repo_a, "status", "--porcelain"))
            self.assertEqual(index_a, (repo_a / ".git" / "index").read_bytes())

    def test_cli_and_warm_host_ignore_exported_git_dir_in_cold_and_warm_modes(self):
        # Short /tmp path keeps Unix domain socket paths within macOS sun_path limits.
        temp = Path(tempfile.mkdtemp(prefix="git-iso-cli-", dir="/tmp" if os.path.isdir("/tmp") else None)).resolve()
        self.addCleanup(shutil.rmtree, temp, ignore_errors=True)
        tmpdir = temp / "t"
        tmpdir.mkdir(mode=0o700)
        home = temp / "h"
        xdg = temp / "x"
        autocode_home = temp / "a"
        for directory in (home, xdg, autocode_home):
            directory.mkdir()

        repo_a = temp / "repo-a"
        repo_b = temp / "repo-b"
        empty_dir = temp / "empty"
        empty_dir.mkdir()
        _init_committed_repo(repo_a, "main-A", {"only-a.txt": "from A\n"})
        _init_committed_repo(repo_b, "main-B", {"only-b.txt": "from B\n"})
        status_a = _plain_git(repo_a, "status", "--porcelain")
        index_a = (repo_a / ".git" / "index").read_bytes()

        base_env = {
            **os.environ,
            "TMPDIR": str(tmpdir),
            "TEMP": str(tmpdir),
            "TMP": str(tmpdir),
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(xdg),
            "AUTOCODE_HOME": str(autocode_home),
            "AUTOCODE_PROJECT_INDEX_HOME": str(home / ".autocode"),
            "PYTHONPATH": str(ROOT / "tools"),
            "GIT_DIR": str(repo_a / ".git"),
            "GIT_WORK_TREE": str(repo_a),
            "GIT_INDEX_FILE": str(repo_a / ".git" / "index"),
        }
        base_env.pop(USER_CONFIG_MARKER, None)

        cold_env = {**base_env, host.SETTING: "0"}
        for cmd, cwd, expected_folder, expected_branch in (
            (
                [sys.executable, str(ROOT / "tools" / "autocode.py"), "worktrees", "list", "--json"],
                repo_b,
                repo_b,
                "main-B",
            ),
            ([sys.executable, "-m", "autocode_entry", "worktrees", "list", "--json"], repo_b, repo_b, "main-B"),
            ([sys.executable, "-m", "autocode_entry", "worktrees", "list", "--json"], empty_dir, empty_dir, None),
        ):
            res = subprocess.run(cmd, cwd=str(cwd), env=cold_env, capture_output=True, text=True, timeout=60)
            self.assertEqual(0, res.returncode, res.stderr)
            payload = json.loads(res.stdout)
            self.assertEqual(str(expected_folder), str(Path(payload["folder"]).resolve()))
            self.assertEqual(expected_branch, payload["branch"])

        if os.name != "nt":
            warm_env = {**base_env, host.SETTING: "1"}
            clean_env = {k: v for k, v in warm_env.items() if k not in host.GIT_REPOSITORY_VARIABLES}
            sock = host.socket_path(clean_env)
            self.addCleanup(host.stop_host, sock, timeout=5.0)

            first = subprocess.run(
                [sys.executable, "-m", "autocode_entry", "worktrees", "list", "--json"],
                cwd=str(repo_b),
                env=warm_env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(0, first.returncode, first.stderr)
            self.assertEqual("main-B", json.loads(first.stdout)["branch"])

            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline:
                if host.host_status(sock).get("running"):
                    break
                time.sleep(0.05)
            else:
                self.fail(f"warm host did not become ready on {sock}")

            second = subprocess.run(
                [sys.executable, "-m", "autocode_entry", "worktrees", "list", "--json"],
                cwd=str(repo_b),
                env=warm_env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(0, second.returncode, second.stderr)
            second_payload = json.loads(second.stdout)
            self.assertEqual(str(repo_b), str(Path(second_payload["folder"]).resolve()))
            self.assertEqual("main-B", second_payload["branch"])

            # Even if a client sends GIT_* directly to the warm host, _execute_cli scrubs it.
            seen_env = {}

            def probe_runner():
                seen_env["has_git_dir"] = "GIT_DIR" in os.environ
                return 0

            reply = host._execute_cli(
                probe_runner,
                {"argv": ["modes", "list", "--json"], "cwd": str(repo_b), "env": dict(warm_env)},
                str(temp),
            )
            self.assertEqual(0, reply["exit_code"])
            self.assertFalse(seen_env["has_git_dir"])

        self.assertEqual(status_a, _plain_git(repo_a, "status", "--porcelain"))
        self.assertEqual(index_a, (repo_a / ".git" / "index").read_bytes())


if __name__ == "__main__":
    unittest.main()
