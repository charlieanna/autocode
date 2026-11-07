"""First-use project registration through the public CLI, without a provider or task."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

CLI = Path(__file__).resolve().parents[1] / "tools/autocode.py"


class RegistryProjectCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="registry-project-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / "project"
        self.home = self.root / "registry"
        self.env = {**os.environ, "AUTOCODE_HOME": str(self.home)}
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)

    def commit(self):
        subprocess.run(
            [
                "git",
                "-C",
                str(self.project),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.test",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "--allow-empty",
                "-qm",
                "seed",
            ],
            check=True,
        )

    def invoke(self, *args):
        result = subprocess.run(
            [sys.executable, str(CLI), "registry", *map(str, args), "--json"],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode, json.loads(result.stdout)

    def test_fresh_project_is_available_without_a_task_and_aliases_are_idempotent(self):
        self.commit()
        code, first = self.invoke("add", self.project)
        self.assertEqual(0, code, first)
        self.assertFalse(first["already_registered"])
        alias = self.root / "alias"
        alias.symlink_to(self.project, target_is_directory=True)
        code, again = self.invoke("add", alias)
        self.assertEqual(0, code, again)
        self.assertTrue(again["already_registered"])
        self.assertEqual(first["workspace_id"], again["workspace_id"])
        _, listed = self.invoke("list")
        self.assertEqual([], listed["runs"])
        [project] = listed["workspaces"]
        self.assertEqual("available", project["availability"])
        self.assertEqual(str(self.project), project["workspace"])
        self.assertFalse((self.project / ".autocode").exists(), "registration creates no task or model work")

    def test_missing_non_git_subdirectory_and_uncommitted_roots_are_refused_without_registration(self):
        plain = self.root / "plain"
        plain.mkdir()
        subdir = self.project / "src"
        subdir.mkdir()
        expected = {
            self.root / "missing" / "deeper": "no such directory",
            plain: "select the root of an existing Git checkout or worktree",
            subdir: "select the root of an existing Git checkout or worktree",
            self.project: "the repository has no commit yet; make a first commit, then register it",
        }
        for path, reason in expected.items():
            with self.subTest(path=path):
                code, refused = self.invoke("add", path)
                self.assertEqual(2, code, refused)
                self.assertEqual("invalid_workspace", refused["error"]["code"])
                # The person's own path and the remedy, not a Python errno naming an ancestor
                # or Git's "fatal: Needed a single revision".
                self.assertEqual(f"Cannot register {path}: {reason}", refused["error"]["message"])
                self.assertFalse((self.home / "registry.json").exists())

    def test_git_worktree_root_is_supported(self):
        self.commit()
        worktree = self.root / "task-worktree"
        subprocess.run(["git", "-C", str(self.project), "worktree", "add", "-q", "--detach", str(worktree)], check=True)
        code, result = self.invoke("add", worktree)
        self.assertEqual(0, code, result)
        self.assertEqual(str(worktree), result["workspace"])
        self.assertFalse((worktree / ".autocode").exists())

    def test_git_environment_cannot_make_an_empty_repo_borrow_another_head(self):
        self.commit()
        empty = self.root / "empty"
        subprocess.run(["git", "init", "-q", str(empty)], check=True)
        self.env.update(GIT_DIR=str(self.project / ".git"), GIT_WORK_TREE=str(empty))
        code, result = self.invoke("add", empty)
        self.assertEqual(2, code, result)
        self.assertFalse((self.home / "registry.json").exists())

    def test_list_and_runs_all_from_a_project_find_its_task_worktree_run(self):
        """#2279: with no AUTOCODE_HOME a default run indexes in <project>/.autocode/registry while
        `registry list` and `runs --all` read the per-user home; from the project they now read both."""
        import autocode_registry as registry

        self.commit()
        lane = self.project / ".autocode" / "worktrees" / "add-a-hello-script-0123456789"
        subprocess.run(
            ["git", "-C", str(self.project), "worktree", "add", "-q", "-b", "autocode/lane", str(lane)], check=True
        )
        (lane / ".autocode").mkdir()
        (lane / ".autocode" / "task-workspace.json").write_text(
            json.dumps({"workspace": str(lane), "project_workspace": str(self.project)})
        )
        run = lane / ".autocode" / "runs" / "lane-run"
        run.mkdir(parents=True)
        state = {"version": 3, "workspace": str(lane), "task": "Add a hello script", "status": "WAITING_FOR_USER"}
        (run / "state.json").write_text(json.dumps(state))
        home = self.root / "account-home"
        home.mkdir()
        environment = {key: value for key, value in self.env.items() if key != "AUTOCODE_HOME"}
        environment["HOME"] = str(home)
        with patch.dict(os.environ, environment, clear=True):
            registry.configure_workspace_storage(lane)
            registry.register_run(lane, run, state)
        self.assertTrue((self.project / ".autocode" / "registry" / "registry.json").is_file())

        def invoke(*args):
            result = subprocess.run(
                [sys.executable, str(CLI), *args, "--json"],
                cwd=self.project,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            return json.loads(result.stdout)

        listed = invoke("registry", "list")
        self.assertEqual([str(run)], [row["run_dir"] for row in listed["runs"]])
        self.assertEqual(str(home / ".autocode"), invoke("registry", "location")["storage_root"])
        fleet = invoke("runs", "--all")
        self.assertEqual([str(run)], [row["run_dir"] for row in fleet["runs"]])
        self.assertFalse((home / ".autocode").exists(), "reading creates no per-user registry")
        # An explicit home still selects exactly one registry.
        environment["AUTOCODE_HOME"] = str(self.home)
        self.assertEqual([], invoke("registry", "list")["runs"])

    def test_corrupt_registry_is_preserved(self):
        self.commit()
        self.home.mkdir()
        path = self.home / "registry.json"
        path.write_text("broken registry\n")
        code, result = self.invoke("add", self.project)
        self.assertEqual(2, code, result)
        self.assertIn("error", result)
        self.assertEqual("broken registry\n", path.read_text())
