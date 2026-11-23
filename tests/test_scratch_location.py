"""Proof scratch trees live outside the live workspace (#4057, phase 1).

A proof scratch built inside the workspace lets an ancestor-aware runner walk up into
the live candidate source: Go finds the candidate ``go.mod``, Node the candidate
``package.json``, Python the candidate project root. A purported base observation then
reads the candidate, so it is not an independent base failure. These tests pin the
external placement and the ancestor isolation it gives, on the real run-store layout
(``workspace/.autocode/runs/<run>``) the registry binds every run to.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_scratch_location as scratch_location
import autocode_verify as verify


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def workspace_with(files):
    temporary = tempfile.TemporaryDirectory(prefix="scratch-4057-")
    workspace = Path(temporary.name).resolve() / "project"
    workspace.mkdir()
    for name, text in files.items():
        (workspace / name).write_text(text)
    git(workspace, "init", "-q")
    git(workspace, "config", "maintenance.auto", "false")
    git(workspace, "config", "gc.auto", "0")
    git(workspace, "add", "-A")
    git(workspace, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "seed")
    return temporary, workspace


class ScratchLocationTests(unittest.TestCase):
    def test_run_owned_scratch_is_mirrored_outside_the_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary).resolve() / "ws"
            run = workspace / ".autocode" / "runs" / "r"
            root = scratch_location.scratch_root(run)
            self.assertFalse(root.is_relative_to(workspace))
            self.assertEqual(root.name, "scratch")
            self.assertEqual(scratch_location.source_base(root), run)
            self.assertEqual(
                scratch_location.scratch_root(run / "regression" / "proof"),
                root.parent / "regression" / "proof" / "scratch",
            )

    def test_external_or_non_store_base_is_left_in_place(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve() / "evidence"
            self.assertEqual(scratch_location.scratch_root(base), base / "scratch")
            self.assertIsNone(scratch_location.source_base(base / "scratch"))

    def test_nested_workspace_resolves_to_its_own_store(self):
        with tempfile.TemporaryDirectory() as temporary:
            outer = Path(temporary).resolve() / "outer"
            inner = outer / ".autocode" / "runs" / "r" / "inner-ws"
            run = inner / ".autocode" / "runs" / "r2"
            root = scratch_location.scratch_root(run)
            self.assertFalse(root.is_relative_to(inner))
            self.assertEqual(scratch_location.source_base(root), run)


class ExternalProofScratchTests(unittest.TestCase):
    """The regression: a base proof scratch inside the workspace can read live candidate
    source through an ancestor lookup; outside the workspace it cannot (#4057)."""

    def setUp(self):
        # The base revision has application code but no project-root marker. The candidate
        # working tree adds a go.mod (untracked), exactly the issue's first-Go-project case.
        self.temp, self.workspace = workspace_with({"app.py": "value = 7\n"})
        self.addCleanup(self.temp.cleanup)
        (self.workspace / "go.mod").write_text("module example.test/candidate\n\ngo 1.25\n")
        self.run_dir = self.workspace / ".autocode" / "runs" / "fixture"
        self.run_dir.mkdir(parents=True)
        self.head = git(self.workspace, "rev-parse", "HEAD").strip()

    def ancestor_marker(self, tree):
        """The first ancestor (including the tree) holding a go.mod, as Go's lookup walks."""
        for parent in (tree, *tree.parents):
            if (parent / "go.mod").is_file():
                return parent
        return None

    def test_base_proof_scratch_cannot_reach_candidate_source_through_ancestors(self):
        # verify.baseline chooses the base tree's location itself (the product decision);
        # the base worktree carries no candidate go.mod, so an ancestor lookup that reaches
        # one can only have walked up into the live candidate workspace.
        result = verify.baseline(
            self.workspace, self.head, self.run_dir, framework=None, suite_command="true", timeout=30
        )
        tree = Path(result["receipt"]["execution_root"]).resolve()
        workspace = self.workspace.resolve()
        # The scratch tree is outside the live workspace, and no ancestor of it is the
        # workspace, so an ancestor-aware runner cannot reach the candidate's go.mod.
        self.assertFalse(tree.is_relative_to(workspace), f"proof scratch {tree} is inside {workspace}")
        self.assertNotIn(workspace, tree.parents)
        self.assertIsNone(
            self.ancestor_marker(tree),
            "ancestor lookup from the base proof scratch reached the live candidate go.mod",
        )

    def test_prepared_proof_tree_is_outside_the_workspace(self):
        # The owned preparation path (autocode_scratch_preparation.prepare) places its tree
        # outside the workspace too. A clean tracked workspace passes the source pin gate.
        git(self.workspace, "add", "go.mod")
        git(self.workspace, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "candidate")
        tree = Path(verify.prepare_scratch_tree(self.workspace, self.run_dir)).resolve()
        self.addCleanup(verify.remove_tree, self.workspace, tree)
        self.assertFalse(tree.is_relative_to(self.workspace.resolve()))
        self.assertEqual(tree, scratch_location.scratch_root(self.run_dir) / "tree")
        self.assertEqual((tree / "app.py").read_text(), "value = 7\n")


if __name__ == "__main__":
    unittest.main()
