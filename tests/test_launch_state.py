"""An in-place run's uncommitted and untracked launch files are original code, not its change.

Real Git, real CLI and real unittest suites in scratch worktrees; no provider is launched
except the fake one the CLI test puts on PATH.
"""
import subprocess
import sys
import unittest

import autocode_regression as regression
import autocode_verify as verify
from . import test_subprocess
from .test_verify import Project, git

APP = "def greet():\n    return 'hello'\n"
BROKEN_APP = "def greet():\n    return 'BROKEN'\n"
TEST_APP = ("import unittest\nfrom app import greet\n\n\nclass AppTests(unittest.TestCase):\n"
            "    def test_greet(self):\n        self.assertEqual('hello', greet())\n")
LIB = "def one():\n    return 1\n"
TEST_LIB = ("import unittest\nfrom lib import one\n\n\nclass LibTests(unittest.TestCase):\n"
            "    def test_one(self):\n        self.assertEqual(1, one())\n")
FEATURE = {"feature.py": "def feature():\n    return 'feature'\n",
           "test_feature.py": ("import unittest\nfrom feature import feature\n\n\n"
                               "class FeatureTests(unittest.TestCase):\n    def test_feature(self):\n"
                               "        self.assertEqual('feature', feature())\n")}


def build_state(base):
    return {"base_commit": base, "settings": {"regression": {"python": sys.executable}}, "iteration": 1,
            "stages": [], "history": [],
            "goal_contract": {"body": {"task_kind": "build", "acceptance_criteria": [], "milestones": [{"id": "M1"}]}}}


class InPlaceLaunchCli(unittest.TestCase):
    def test_a_builder_cannot_break_an_untracked_launch_suite_and_pass(self):
        flow = test_subprocess.SubprocessFlow()
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        project = flow.project  # one empty commit
        (project / "app.py").write_text(APP)
        (project / "test_app.py").write_text(TEST_APP)
        head, status = git(project, "rev-parse", "HEAD"), git(project, "status", "--porcelain")
        result = subprocess.run([*flow.entry, "--workspace", str(project), "--engine", "codex", "--no-chat",
                                 "--in-place", "Add a feature function"], cwd=flow.root, capture_output=True,
                                text=True, env={**flow.env, "AUTOCODE_FIXTURE_MODE": "no-human"}, timeout=240)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        run, state = flow.saved()
        checkout = (git(project, "rev-parse", "HEAD"), git(project, "status", "--porcelain"))
        base_files = git(project, "ls-tree", "-r", "--name-only", state["base_commit"]).split()
        ref = git(project, "for-each-ref", "--format=%(objectname)", f"refs/autocode/launch/{run.name}")
        # The Builder breaks greet(), deletes its test and adds a feature with its own test.
        (project / "app.py").write_text(BROKEN_APP)
        (project / "test_app.py").unlink()
        for name, text in FEATURE.items():
            (project / name).write_text(text)
        proof = regression.prove({**state, **build_state(state["base_commit"])}, project, run)
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertIn("Existing test files were deleted: test_app.py", proof["failures"])
        self.assertEqual((head, status), checkout, "the launch must not commit, stage or move anything")
        self.assertEqual(["app.py", "test_app.py"], base_files)
        self.assertEqual(state["base_commit"], ref)


class LaunchCommit(unittest.TestCase):
    def project(self, files):
        project = Project(files)
        self.addCleanup(project.close)
        return project

    def test_a_clean_checkout_starts_from_head(self):
        project = self.project({"app.py": APP})
        self.assertEqual(project.base, verify.commit_worktree(project.root))

    def test_only_changes_after_launch_count(self):
        project = self.project({"README.md": "x\n", "lib.py": LIB})
        project.write({"lib.py": LIB + "# unsaved\n", "app.py": APP, "test_app.py": TEST_APP, "staged.py": "s = 1\n"})
        git(project.root, "add", "staged.py")
        index, status = git(project.root, "ls-files", "-s"), git(project.root, "status", "--porcelain")
        base = verify.commit_worktree(project.root)
        self.assertEqual({}, verify.changed_files(project.root, base))
        self.assertEqual((index, status), (git(project.root, "ls-files", "-s"),
                                           git(project.root, "status", "--porcelain")))
        self.assertEqual(project.base, git(project.root, "rev-parse", f"{base}^"))
        project.write({"app.py": APP + "\n\ndef bye():\n    return 'bye'\n", "new.py": "n = 1\n"})
        (project.root / "test_app.py").unlink()
        changes = verify.changed_files(project.root, base)
        self.assertEqual({"app.py": "modified", "new.py": "added", "test_app.py": "deleted"}, changes)
        stats = verify.diff_stats(project.root, base, changes)
        self.assertEqual((5, 7), (stats["lines_added"], stats["lines_removed"]))

    def test_a_suite_untracked_at_launch_is_preserved(self):
        """README-only and non-empty tracked bases alike: committing first would give the same FAIL."""
        for tracked in ({"README.md": "x\n"}, {"README.md": "x\n", "lib.py": LIB, "test_lib.py": TEST_LIB}):
            with self.subTest(tracked=sorted(tracked)):
                project = self.project(tracked)
                project.write({"app.py": APP, "test_app.py": TEST_APP})
                base = verify.commit_worktree(project.root)
                project.write({"app.py": BROKEN_APP, **FEATURE})
                proof = regression.prove(build_state(base), project.root, project.evidence)
                self.assertEqual(verify.FAIL, proof["verdict"], proof)
                self.assertTrue(any("test_app" in reason for reason in proof["failures"]), proof)


if __name__ == "__main__":
    unittest.main()
