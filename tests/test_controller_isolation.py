"""Controller isolation through the public CLI and TaskRun interface."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_controller_isolation as isolation
import autocode_run_view as run_view
import autocode_taskrun as taskrun

from . import test_subprocess as fixtures
from .test_taskrun import BRIEF, FIXTURE_OPTIONS


class ControllerIsolationCliTests(unittest.TestCase):
    def setUp(self):
        fixtures.SubprocessFlow.setUp(self)
        source = Path(__file__).resolve().parents[1] / "tools"
        shutil.copy2(source / "live_fixture_provider.py", self.root / "fixture-bin" / "codex")
        (self.root / "fixture-bin" / "codex").chmod(0o755)
        (self.project / "tools").mkdir()
        (self.project / "tools" / "autocode.py").write_text("# AutoCode checkout marker\n")
        (self.project / "tools" / "autocode_controller_isolation.py").write_text("# AutoCode checkout marker\n")
        (self.project / "AGENTS.md").write_text("AutoCode fixture instructions\n")
        self.git("add", ".")
        self.git("commit", "-qm", "AutoCode markers")

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.project), "-c", "user.name=Fixture", "-c", "user.email=f@example.test", *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def invoke(self, workspace, *args, command=None):
        return subprocess.run(
            [*(command or self.entry), "--workspace", str(workspace), *args],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=240,
        )

    def linked(self, name="self-build"):
        tree = self.root / name
        self.git("worktree", "add", "--detach", str(tree), "HEAD")
        return tree

    def copy_controller(self):
        source = Path(__file__).resolve().parents[1] / "tools"
        shutil.copytree(
            source, self.project / "tools", dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__")
        )
        self.git("add", "tools")
        self.git("commit", "-qm", "Controller fixture")

    def test_external_controller_works_in_place_in_the_main_checkout(self):
        run = taskrun.TaskRun.start(
            self.project, BRIEF, command=self.entry, options=FIXTURE_OPTIONS, env=self.env, timeout=240
        )
        view = run.status()
        self.assertEqual("approve_plan", view["needs"]["kind"], view)
        self.assertFalse(Path(view["runner"]["path"]).is_relative_to(self.project))
        self.assertEqual([run.run_dir], taskrun.TaskRun.runs_in(self.project))

    def test_main_checkout_cannot_execute_its_own_controller(self):
        self.copy_controller()
        before = set(self.project.iterdir())
        local = [sys.executable, str(self.project / "tools/autocode.py")]
        result = self.invoke(
            self.project, BRIEF, "--in-place", "--no-chat", "--provider", "missing-provider", command=local
        )
        self.assertEqual(2, result.returncode, result.stderr)
        self.assertIn("Unsafe AutoCode self-build", result.stderr)
        self.assertIn("executing controller is inside", result.stderr)
        self.assertIn("worktree add --detach", result.stderr)
        self.assertEqual(before, set(self.project.iterdir()))

    def test_external_controller_can_start_in_a_caller_owned_linked_worktree(self):
        tree = self.linked("self build\nwith trailing space ")
        run = taskrun.TaskRun.start(tree, BRIEF, command=self.entry, options=FIXTURE_OPTIONS, env=self.env, timeout=240)
        view = run.status()
        self.assertEqual("approve_plan", view["needs"]["kind"], view)
        self.assertFalse(Path(view["runner"]["path"]).is_relative_to(tree))
        self.assertEqual(view["runner"], view["evidence"]["runner"])
        self.assertTrue(view["evidence"]["base_commit"])
        self.assertEqual([], taskrun.TaskRun.runs_in(self.project))

    def test_default_cli_launch_isolates_the_self_build(self):
        result = self.invoke(self.project, BRIEF, "--no-chat", *FIXTURE_OPTIONS)
        self.assertEqual(2, result.returncode, result.stderr)
        self.assertIn("Task worktree:", result.stdout)
        trees = list((self.project / ".autocode" / "worktrees").iterdir())
        self.assertEqual(1, len(trees))
        run = taskrun.TaskRun.attach(trees[0], command=self.entry, options=FIXTURE_OPTIONS, env=self.env, timeout=240)
        self.assertIsNotNone(run)
        self.assertEqual("approve_plan", run.status()["needs"]["kind"])

    def test_linked_worktree_cannot_execute_its_own_controller(self):
        self.copy_controller()
        tree = self.linked()
        result = self.invoke(
            tree, BRIEF, "--in-place", "--no-chat", command=[sys.executable, str(tree / "tools/autocode.py")]
        )
        self.assertEqual(2, result.returncode, result.stderr)
        self.assertIn("executing controller is inside", result.stderr)
        self.assertEqual([], taskrun.TaskRun.runs_in(tree))

    def test_status_of_a_saved_run_remains_read_only_under_an_unsafe_controller(self):
        self.copy_controller()
        tree = self.linked()
        run = taskrun.TaskRun.start(tree, BRIEF, command=self.entry, options=FIXTURE_OPTIONS, env=self.env, timeout=240)
        checkpoint = run.run_dir / "state.json"
        before = checkpoint.read_bytes()
        local = [sys.executable, str(tree / "tools/autocode.py")]
        shown = self.invoke(tree, "--run-dir", str(run.run_dir), "--status", command=local)
        self.assertEqual(0, shown.returncode, shown.stderr)
        self.assertEqual(run.status()["runner"], json.loads(shown.stdout)["view"]["runner"])
        refused = self.invoke(tree, "--run-dir", str(run.run_dir), "--no-chat", command=local)
        self.assertEqual(2, refused.returncode, refused.stderr)
        self.assertIn("Unsafe AutoCode self-build", refused.stderr)
        self.assertEqual(before, checkpoint.read_bytes())


class VendoredCopyTests(unittest.TestCase):
    """A project carrying an old copy of tools/autocode.py is not an AutoCode self-build."""

    def test_a_vendored_autocode_copy_is_an_ordinary_project(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp).resolve()
            (project / "tools").mkdir()
            (project / "tools" / "autocode.py").write_text("# vendored copy\n")
            (project / "AGENTS.md").write_text("the project's own instructions\n")
            controller = Path(isolation.__file__)
            isolation.require_isolated(project, controller)  # not refused
            (project / "tools" / "autocode_controller_isolation.py").write_text("# AutoCode itself\n")
            isolation.require_isolated(project, controller)  # an outside controller may edit it in place
            with self.assertRaisesRegex(ValueError, "Unsafe AutoCode self-build"):
                isolation.require_isolated(project, project / "tools" / "autocode_controller_isolation.py")


class ControllerIdentityViewTests(unittest.TestCase):
    def test_saved_identity_is_copied_and_never_inferred_from_builder_commit(self):
        state = {
            "status": "RUNNING",
            "base_commit": "builder",
            "runner": {"path": "/controller/tools", "commit": "old+modified"},
        }
        view = run_view.view(state)
        self.assertEqual(state["runner"], view["runner"])
        self.assertEqual("builder", view["evidence"]["base_commit"])
        view["runner"]["commit"] = "mutated"
        view["evidence"]["runner"]["path"] = "/mutated"
        self.assertEqual({"path": "/controller/tools", "commit": "old+modified"}, state["runner"])

    def test_legacy_missing_and_installed_unknown_commit_remain_unknown(self):
        self.assertIsNone(run_view.view({"status": "RUNNING"})["runner"])
        installed = {"path": "/venv/site-packages/autocode_cli", "commit": None}
        self.assertEqual(installed, run_view.view({"status": "RUNNING", "runner": installed})["runner"])
