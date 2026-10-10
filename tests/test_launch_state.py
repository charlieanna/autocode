"""An in-place run's uncommitted and untracked launch files are original code, not its change.

Real Git and real unittest suites in scratch checkouts. The CLI cases are in test_launch_state_cli.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_regression as regression
import autocode_verify as verify

from .test_verify import Project, git, isolated_python

APP = "def greet():\n    return 'hello'\n"
BROKEN_APP = "def greet():\n    return 'BROKEN'\n"
TEST_APP = (
    "import unittest\nfrom app import greet\n\n\nclass AppTests(unittest.TestCase):\n"
    "    def test_greet(self):\n        self.assertEqual('hello', greet())\n"
)
LIB = "def one():\n    return 1\n"
TEST_LIB = (
    "import unittest\nfrom lib import one\n\n\nclass LibTests(unittest.TestCase):\n"
    "    def test_one(self):\n        self.assertEqual(1, one())\n"
)
FEATURE = {
    "feature.py": "def feature():\n    return 'feature'\n",
    "test_feature.py": (
        "import unittest\nfrom feature import feature\n\n\n"
        "class FeatureTests(unittest.TestCase):\n    def test_feature(self):\n"
        "        self.assertEqual('feature', feature())\n"
    ),
}
TOOL_TEST = (
    "import os, subprocess, unittest\n\n\nclass ToolTests(unittest.TestCase):\n    def test_tool(self):\n"
    "        here = os.path.dirname(os.path.abspath(__file__))\n"
    "        self.assertEqual(b'ok\\n', subprocess.run([os.path.join(here, 'tool.sh')],"
    " capture_output=True).stdout)\n"
)
# What AutoCode appends to .git/info/exclude before an OpenCode stage (autocode_readonly_events).
AUTOCODE_EXCLUDE = "/.autocode/\n/.autocode-ui/\n__pycache__/\n*.pyc\n"


def build_state(base, python):
    """A build contract proven with ``python`` (isolated_python: no editable install another checkout changes)."""
    return {
        "base_commit": base,
        "settings": {"regression": {"python": python}},
        "iteration": 1,
        "stages": [],
        "history": [],
        "goal_contract": {"body": {"task_kind": "build", "acceptance_criteria": [], "milestones": [{"id": "M1"}]}},
    }


def files(root, commit):
    return git(root, "ls-tree", "-r", "--name-only", commit).split()


class LaunchCommit(unittest.TestCase):
    def project(self, files):
        project = Project(files)
        self.addCleanup(project.close)
        return project

    def test_a_clean_checkout_starts_from_head(self):
        project = self.project({"app.py": APP})
        self.assertEqual(project.base, verify.commit_worktree(project.root))

    def test_a_checkout_with_only_an_empty_commit_starts_from_its_files(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        git(root, "init", "-q")
        git(
            root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-q", "--allow-empty", "-m", "empty"
        )
        (root / "app.py").write_text(APP)
        base = verify.commit_worktree(root)
        self.assertEqual(["app.py"], files(root, base))
        self.assertEqual({}, verify.changed_files(root, base))

    def test_an_edit_right_after_a_commit_counts(self):
        """Git's racily clean entries: the copied index keeps the real one's timestamp."""
        project = self.project({"app.py": APP})
        project.write({"app.py": APP.replace("hello", "HELLO")})  # same size, likely the same second
        self.assertEqual({"app.py": "modified"}, verify.changed_files(project.root, project.base))

    def test_only_changes_after_launch_count(self):
        project = self.project({"README.md": "x\n", "lib.py": LIB})
        project.write({"lib.py": LIB + "# unsaved\n", "app.py": APP, "test_app.py": TEST_APP, "staged.py": "s = 1\n"})
        git(project.root, "add", "staged.py")
        index, status = git(project.root, "ls-files", "-s"), git(project.root, "status", "--porcelain")
        base = verify.commit_worktree(project.root)
        self.assertEqual({}, verify.changed_files(project.root, base))
        self.assertEqual(
            (index, status), (git(project.root, "ls-files", "-s"), git(project.root, "status", "--porcelain"))
        )
        self.assertEqual(project.base, git(project.root, "rev-parse", f"{base}^"))
        project.write({"app.py": APP + "\n\ndef bye():\n    return 'bye'\n", "new.py": "n = 1\n"})
        (project.root / "test_app.py").unlink()
        changes = verify.changed_files(project.root, base)
        self.assertEqual({"app.py": "modified", "new.py": "added", "test_app.py": "deleted"}, changes)
        stats = verify.diff_stats(project.root, base, changes)
        self.assertEqual((5, 7), (stats["lines_added"], stats["lines_removed"]))

    def test_a_suite_untracked_at_launch_is_preserved(self):
        """README-only and non-empty tracked bases alike: committing first would give the same FAIL."""
        python = isolated_python(self)
        for tracked in ({"README.md": "x\n"}, {"README.md": "x\n", "lib.py": LIB, "test_lib.py": TEST_LIB}):
            with self.subTest(tracked=sorted(tracked)):
                project = self.project(tracked)
                project.write({"app.py": APP, "test_app.py": TEST_APP})
                base = verify.commit_worktree(project.root)
                project.write({"app.py": BROKEN_APP, **FEATURE})
                proof = regression.prove(build_state(base, python), project.root, project.evidence)
                self.assertEqual(verify.FAIL, proof["verdict"], proof)
                self.assertTrue(any("test_app" in reason for reason in proof["failures"]), proof)

    # The next three come from #574 (tests/test_launch_overlay.py), the competing fix for #540.
    def launch(self, project):
        """The base_commit a new in-place run records (run creation calls regression.launch_base)."""
        return regression.launch_base(project.root, project.evidence)

    def test_a_feature_that_leaves_untracked_launch_files_alone_passes(self):
        project = self.project({"README.md": "x\n"})
        project.write({"app.py": APP, "test_app.py": TEST_APP})
        base = self.launch(project)
        project.write(FEATURE)
        proof = regression.prove(build_state(base, isolated_python(self)), project.root, project.evidence)
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual(["feature.py"], proof["source_files"], proof)
        self.assertEqual(["test_feature.py"], proof["test_files"], proof)
        self.assertIn("test_feature.FeatureTests.test_feature", proof["fail_to_pass"], proof)

    def test_untracked_code_on_a_readme_only_head_is_not_a_new_project(self):
        project = self.project({"README.md": "x\n"})
        project.write({"app.py": APP})
        base = self.launch(project)
        project.write({"app.py": BROKEN_APP, **FEATURE})
        proof = regression.prove(build_state(base, isolated_python(self)), project.root, project.evidence)
        self.assertNotEqual(verify.PASS, proof["verdict"], proof)
        self.assertFalse(any("documentation-only" in note for note in proof["notes"]), proof["notes"])

    @unittest.skipUnless(os.name == "posix", "needs POSIX file modes")
    def test_a_launch_commit_keeps_a_tracked_scripts_exec_bit(self):
        """An unrelated untracked file makes the launch base a new commit; dropping +x must still fail."""
        project = self.project({"README.md": "x\n", "tool.sh": "#!/bin/sh\necho ok\n", "test_tool.py": TOOL_TEST})
        (project.root / "tool.sh").chmod(0o755)
        git(project.root, "add", "tool.sh")
        git(project.root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "exec")
        project.write({"notes.md": "wip\n"})
        base = self.launch(project)
        self.assertNotEqual(git(project.root, "rev-parse", "HEAD"), base)
        (project.root / "tool.sh").chmod(0o644)
        project.write(FEATURE)
        proof = regression.prove(build_state(base, isolated_python(self)), project.root, project.evidence)
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertTrue(any("test_tool" in reason for reason in proof["failures"]), proof)

    def test_a_gitignored_autocode_directory_is_left_out(self):
        for rule in ("info/exclude", ".gitignore"):
            with self.subTest(rule=rule):
                project = self.project({"README.md": "x\n"})
                if rule == ".gitignore":
                    project.write({".gitignore": ".autocode\n__pycache__/\n"})
                else:
                    with (project.root / ".git/info/exclude").open("a") as exclude:
                        exclude.write(AUTOCODE_EXCLUDE)
                project.write(
                    {".autocode/runs/r/state.json": "{}\n", "__pycache__/app.cpython-312.pyc": "x", "app.py": APP}
                )
                base = verify.commit_worktree(project.root)
                self.assertEqual(
                    sorted(["README.md", "app.py", *([".gitignore"] if rule == ".gitignore" else [])]),
                    files(project.root, base),
                )
                self.assertEqual({}, verify.changed_files(project.root, base))
                project.write({"new.py": "n = 1\n", ".autocode/runs/r/log.txt": "x\n"})
                self.assertEqual({"new.py": "added"}, verify.changed_files(project.root, base))

    def test_an_untracked_repository_is_listed_not_staged(self):
        project = self.project({"README.md": "x\n"})
        for name, commit in (("empty", False), ("committed", True)):
            nested = project.root / name
            nested.mkdir()
            git(nested, "init", "-q")
            (nested / "lib.py").write_text(LIB)
            if commit:
                git(nested, "add", "lib.py")
                git(nested, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "lib")
        project.write({"app.py": APP})
        base = verify.commit_worktree(project.root)
        self.assertEqual(["README.md", "app.py"], files(project.root, base))
        changes = verify.changed_files(project.root, base)
        self.assertEqual({"committed/": "added", "empty/": "added"}, changes)
        self.assertEqual(2, verify.diff_stats(project.root, base, changes)["files"])
        git(project.root, "add", "committed")  # a submodule: uncommitted work in it is a change, as git diff says
        git(project.root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "submodule")
        head = git(project.root, "rev-parse", "HEAD")
        self.assertEqual({"app.py": "added", "empty/": "added"}, verify.changed_files(project.root, head))
        (project.root / "committed/lib.py").write_text(APP)
        self.assertEqual(
            {"app.py": "added", "committed": "modified", "empty/": "added"}, verify.changed_files(project.root, head)
        )

    def test_ignoring_a_launch_file_does_not_delete_it(self):
        project = self.project({"README.md": "x\n"})
        project.write({"app.py": APP, "test_app.py": TEST_APP})
        base = verify.commit_worktree(project.root)
        project.write({".gitignore": "test_app.py\n", "app.py": APP + "# more\n"})
        self.assertEqual({".gitignore": "added", "app.py": "modified"}, verify.changed_files(project.root, base))
        (project.root / "test_app.py").unlink()
        self.assertEqual("deleted", verify.changed_files(project.root, base)["test_app.py"])

    def test_comparing_writes_nothing_to_the_object_store(self):
        project = self.project({"README.md": "x\n"})
        project.write({"data.txt": "launch data\n", "README.md": "y\n"})
        objects = git(project.root, "count-objects", "-v")
        changes = verify.changed_files(project.root, project.base)
        self.assertEqual({"README.md": "modified", "data.txt": "added"}, changes)
        verify.diff_stats(project.root, project.base, changes)
        self.assertEqual(objects, git(project.root, "count-objects", "-v"))
        base = verify.commit_worktree(project.root)  # this commit must last, and so must its files
        self.assertEqual("launch data", git(project.root, "show", f"{base}:data.txt"))

    def test_launch_refs_last_while_their_run_does(self):
        project = self.project({"README.md": "x\n"})
        root, runs = project.root, project.root / ".autocode/runs"
        tree = git(root, "rev-parse", "HEAD^{tree}")
        old = {**os.environ, "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"}
        for name, env in (("removed", old), ("kept", old), ("launching", os.environ)):
            commit = subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=t",
                    "-c",
                    "user.email=t@example.test",
                    "commit-tree",
                    tree,
                    "-p",
                    "HEAD",
                    "-m",
                    name,
                ],
                cwd=root,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            git(root, "update-ref", regression.LAUNCH_REFS + name, commit)
        (runs / "kept").mkdir(parents=True)
        self.assertEqual(project.base, regression.launch_base(root, runs / "clean"))
        project.write({"app.py": APP})
        base = regression.launch_base(root, runs / "new")
        refs = dict(
            line.split()
            for line in git(
                root, "for-each-ref", "--format=%(refname:lstrip=3) %(objectname)", regression.LAUNCH_REFS
            ).splitlines()
        )
        self.assertEqual(["kept", "launching", "new"], sorted(refs))  # never one for a clean checkout
        self.assertEqual(base, refs["new"])


if __name__ == "__main__":
    unittest.main()
