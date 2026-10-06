"""Uncommitted files at the start of an in-place run are original code (#540).

The proof runs them on the base and counts only what the run changed. Real git
and real unittest suites; no model.
"""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_regression as regression
import autocode_verify as verify
from tests.test_verify import Project


APP = "def greet():\n    return 'hello'\n"
APP_BROKEN = "def greet():\n    return 'BROKEN'\n"
TEST_APP = (
    "import unittest\nfrom app import greet\n\n"
    "class AppTests(unittest.TestCase):\n"
    "    def test_greet(self):\n"
    "        self.assertEqual('hello', greet())\n")
FEATURE = "def feature():\n    return 'feature'\n"
TEST_FEATURE = (
    "import unittest\nfrom feature import feature\n\n"
    "class FeatureTests(unittest.TestCase):\n"
    "    def test_c1_feature(self):\n"
    "        self.assertEqual('feature', feature())\n")
LIB = "def kept():\n    return 1\n"
TEST_LIB = (
    "import unittest\nfrom lib import kept\n\n"
    "class LibTests(unittest.TestCase):\n"
    "    def test_kept(self):\n"
    "        self.assertEqual(1, kept())\n")


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


class LaunchOverlayProofTests(unittest.TestCase):
    def setUp(self):
        self.run = Path(tempfile.mkdtemp(prefix="launch-overlay-"))
        self.addCleanup(shutil.rmtree, self.run, True)

    def contract(self, project):
        return {"base_commit": project.base, "settings": {"regression": {"test_timeout": 60}},
                "iteration": 1, "stages": [], "history": [],
                "goal_contract": {"body": {
                    "task_kind": "build",
                    "acceptance_criteria": [{
                        "id": "C1", "criterion": "feature returns feature",
                        "verification_method": "test: test_c1_feature — the new behavior",
                    }],
                    "milestones": [{"id": "M1"}]}}}

    def leave_untracked(self, project, app=APP):
        (project.root / "app.py").write_text(app)
        (project.root / "test_app.py").write_text(TEST_APP)

    def add_feature(self, project):
        (project.root / "feature.py").write_text(FEATURE)
        (project.root / "test_feature.py").write_text(TEST_FEATURE)

    def prove(self, project, state):
        return regression.prove(state, project.root, self.run)

    def test_capture_leaves_head_index_and_files_unchanged_and_skips_ignored_files(self):
        project = Project({"README.md": "Notes\n", ".gitignore": "secret.py\n"})
        self.addCleanup(project.close)
        self.leave_untracked(project, APP + "# local\n")
        (project.root / "secret.py").write_text("hidden = True\n")
        (project.root / "staged.py").write_text("x = 1\n")
        git(project.root, "add", "staged.py")

        def snapshot():
            return (git(project.root, "status", "--porcelain=v1", "-z", "--untracked-files=all"),
                    git(project.root, "rev-parse", "HEAD"),
                    git(project.root, "write-tree"),
                    git(project.root, "diff", "--cached", "--name-status"),
                    (project.root / "app.py").read_bytes(),
                    (project.root / "test_app.py").read_bytes(),
                    (project.root / "staged.py").read_bytes())

        before = snapshot()
        state = {}
        regression.remember_launch_files(state, project.root, self.run)
        self.assertEqual(before, snapshot())
        files = state["launch_overlay"]["files"]
        self.assertEqual("added", files["app.py"])
        self.assertEqual("added", files["test_app.py"])
        self.assertEqual("added", files["staged.py"])
        self.assertNotIn("secret.py", files)
        self.assertEqual("x = 1\n", (self.run / "launch-overlay" / "staged.py").read_text())
        (project.root / "app.py").write_text(APP_BROKEN)
        regression.remember_launch_files(state, project.root, self.run)
        self.assertEqual(APP + "# local\n", (self.run / "launch-overlay" / "app.py").read_text())

    def test_a_worktree_run_does_not_record_or_apply_a_launch_snapshot(self):
        project = Project({"README.md": "Notes\n"})
        self.addCleanup(project.close)
        self.leave_untracked(project)
        state = {"project_workspace": str(project.root)}
        regression.remember_launch_files(state, project.root, self.run)
        self.assertNotIn("launch_overlay", state)
        state["launch_overlay"] = {"root": "launch-overlay", "files": {"app.py": "added"}}
        self.assertIsNone(regression.launch_overlay(state, self.run))

    def test_breaking_or_deleting_an_untracked_launch_suite_fails_the_proof(self):
        project = Project({"README.md": "Notes\n", "lib.py": LIB, "test_lib.py": TEST_LIB})
        self.addCleanup(project.close)
        self.leave_untracked(project)
        state = self.contract(project)
        regression.remember_launch_files(state, project.root, self.run)
        (project.root / "app.py").write_text(APP_BROKEN)
        (project.root / "test_app.py").unlink()
        self.add_feature(project)
        proof = self.prove(project, state)
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertTrue(any("test_app.py" in reason for reason in proof["failures"]), proof["failures"])
        self.assertIn("app.py", proof["source_files"])
        self.assertIn("test_app.py", proof["test_files"])

    def test_the_same_break_with_the_launch_test_kept_still_fails(self):
        project = Project({"README.md": "Notes\n"})
        self.addCleanup(project.close)
        self.leave_untracked(project)
        state = self.contract(project)
        regression.remember_launch_files(state, project.root, self.run)
        (project.root / "app.py").write_text(APP_BROKEN)
        self.add_feature(project)
        proof = self.prove(project, state)
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertTrue(any("test_greet" in reason for reason in proof["failures"]), proof["failures"])

    def test_a_feature_that_leaves_untracked_launch_files_alone_passes(self):
        project = Project({"README.md": "Notes\n"})
        self.addCleanup(project.close)
        self.leave_untracked(project)
        state = self.contract(project)
        regression.remember_launch_files(state, project.root, self.run)
        self.add_feature(project)
        proof = self.prove(project, state)
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual(["feature.py"], proof["source_files"], proof)
        self.assertEqual(["test_feature.py"], proof["test_files"], proof)
        self.assertIn("test_feature.FeatureTests.test_c1_feature", proof["fail_to_pass"], proof)

    def test_untracked_code_on_a_readme_only_head_is_not_a_new_project(self):
        project = Project({"README.md": "Notes\n"})
        self.addCleanup(project.close)
        (project.root / "app.py").write_text(APP)
        state = self.contract(project)
        regression.remember_launch_files(state, project.root, self.run)
        (project.root / "app.py").write_text(APP_BROKEN)
        self.add_feature(project)
        proof = self.prove(project, state)
        self.assertNotEqual(verify.PASS, proof["verdict"], proof)
        self.assertFalse(any("documentation-only" in note for note in proof["notes"]), proof["notes"])

    def test_committing_the_same_files_before_the_run_still_fails_when_they_break(self):
        project = Project({"README.md": "Notes\n", "app.py": APP, "test_app.py": TEST_APP})
        self.addCleanup(project.close)
        state = self.contract(project)
        regression.remember_launch_files(state, project.root, self.run)
        self.assertEqual({}, state["launch_overlay"]["files"])
        (project.root / "app.py").write_text(APP_BROKEN)
        (project.root / "test_app.py").unlink()
        self.add_feature(project)
        proof = self.prove(project, state)
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertTrue(any("test_app" in reason for reason in proof["failures"]), proof["failures"])


if __name__ == "__main__":
    unittest.main()
