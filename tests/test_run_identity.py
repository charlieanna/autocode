"""The one run-identity mechanism the #3892 and #3890 fixes share (autocode_run_identity)."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_workspaces as workspaces

try:
    import autocode_run_identity as run_identity
except ModuleNotFoundError as error:
    if error.name != "autocode_run_identity":
        raise
    run_identity = None

REAL = "20261120-1200-fix-chat-7f31f2cdb7c939"
TYPO = "20261120-1200-fix-chat-7f31f2ddb7c939"


def checkout(test: unittest.TestCase) -> Path:
    folder = tempfile.TemporaryDirectory(prefix="run-identity-")
    test.addCleanup(folder.cleanup)
    root = Path(folder.name).resolve() / "project"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    identity = ["-c", "user.name=T", "-c", "user.email=t@example.test"]
    subprocess.run(["git", "-C", str(root), *identity, "commit", "-q", "--allow-empty", "-m", "base"], check=True)
    return root


def saved_run(root: Path, name: str = REAL, **written) -> Path:
    run_dir = root / ".autocode" / "runs" / name
    run_dir.mkdir(parents=True, exist_ok=True)
    state = {"version": 3, "workspace": str(root), "task": "build the thing", "status": "RUNNING", **written}
    (run_dir / "state.json").write_text(json.dumps(state))
    return run_dir


def moved_run(root: Path, name: str = REAL) -> tuple[Path, Path]:
    """A run carried to a real task worktree, with both records naming each other as move writes."""
    tree = Path(workspaces.create(root, "moved")["workspace"])
    old, child = root / ".autocode" / "runs" / name, tree / ".autocode" / "runs" / name
    old.mkdir(parents=True, exist_ok=True)
    child.mkdir(parents=True)
    (old / "state.json").write_text(
        json.dumps(
            {
                "version": 3,
                "workspace": str(root),
                "status": "PAUSED_REQUESTED",
                "build_worktree": {"state": "moved", "moved_to": {"workspace": str(tree), "run_dir": str(child)}},
            }
        )
    )
    (child / "state.json").write_text(
        json.dumps(
            {
                "version": 3,
                "workspace": str(tree),
                "status": "RUNNING",
                "build_worktree": {
                    "state": "building",
                    "moved_from": {"workspace": str(root), "run_dir": str(old), "at": "2026-11-20T12:00:00Z"},
                },
            }
        )
    )
    return tree, child


@unittest.skipIf(run_identity is None, "unit tests require the candidate's run-identity module")
class AddressedRunTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="addressed-run-")
        self.addCleanup(temporary.cleanup)
        self.store = Path(temporary.name).resolve() / ".autocode"
        self.runs = self.store / "runs"
        self.runs.mkdir(parents=True)

    def test_an_output_inside_runs_names_the_run_it_is_aimed_at(self):
        addressed = run_identity.addressed_run(self.runs / REAL / "evidence" / "repro.json", [self.store])
        self.assertEqual((self.runs / REAL, REAL), addressed)

    def test_a_path_directly_under_runs_or_outside_it_addresses_no_run(self):
        for path in (self.store / "evidence" / "x.json", self.runs / "repro.json", self.store / "runs-x" / REAL / "y"):
            with self.subTest(path=path):
                self.assertIsNone(run_identity.addressed_run(path, [self.store]))

    def test_the_id_is_read_before_symlink_resolution_so_an_alias_is_not_the_run(self):
        real = self.runs / REAL
        real.mkdir()
        (real / "state.json").write_text("{}")
        alias = self.runs / "alias-of-real"
        alias.symlink_to(real)
        self.assertEqual(
            (alias, "alias-of-real"), run_identity.addressed_run(alias / "evidence" / "repro.json", [self.store])
        )


@unittest.skipIf(run_identity is None, "unit tests require the candidate's run-identity module")
class CheckpointTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="run-checkpoint-")
        self.addCleanup(temporary.cleanup)
        self.runs = Path(temporary.name).resolve() / ".autocode" / "runs"
        self.runs.mkdir(parents=True)

    def test_a_canonical_state_json_is_the_runs_checkpoint(self):
        real = self.runs / REAL
        real.mkdir()
        (real / "state.json").write_text("{}")
        self.assertEqual(real / "state.json", run_identity.checkpoint(real))

    def test_a_missing_or_non_directory_run_has_no_checkpoint(self):
        self.assertIsNone(run_identity.checkpoint(self.runs / TYPO))

    def test_a_symlinked_run_directory_has_no_checkpoint_of_its_own(self):
        real = self.runs / REAL
        real.mkdir()
        (real / "state.json").write_text("{}")
        alias = self.runs / "alias-of-real"
        alias.symlink_to(real)
        self.assertIsNone(run_identity.checkpoint(alias))

    def test_a_symlinked_state_json_is_no_canonical_checkpoint(self):
        real = self.runs / REAL
        real.mkdir()
        elsewhere = self.runs.parent / "elsewhere.json"
        elsewhere.write_text("{}")
        (real / "state.json").symlink_to(elsewhere)
        self.assertIsNone(run_identity.checkpoint(real))


@unittest.skipIf(run_identity is None, "unit tests require the candidate's run-identity module")
class ExpectedOwnerTests(unittest.TestCase):
    def setUp(self):
        self.root = checkout(self)
        self.store = self.root / ".autocode"

    def test_the_single_active_run_is_named(self):
        saved_run(self.root)
        self.assertEqual(REAL, run_identity.expected_owner([self.store]))

    def test_with_no_or_several_active_runs_no_owner_is_guessed(self):
        self.assertIsNone(run_identity.expected_owner([self.store]))
        saved_run(self.root)
        saved_run(self.root, "other-working-run-2")
        self.assertIsNone(run_identity.expected_owner([self.store]))

    def test_a_settled_run_alone_is_not_an_active_run(self):
        saved_run(self.root, status="TASK_COMPLETE")
        self.assertIsNone(run_identity.expected_owner([self.store]))

    def test_the_capture_context_names_the_owner_from_its_attempt_path(self):
        context = {
            "attempt": str(self.store / "runs" / REAL / "iterations" / "001" / "tester-01.json"),
            "nonce": "n1",
            "source_revision": "abc123",
        }
        self.assertEqual(REAL, run_identity.expected_owner([self.store], context))

    def test_a_moved_run_counts_through_its_authenticated_worktree_record_only(self):
        tree, child = moved_run(self.root)
        self.assertEqual({REAL: [child]}, run_identity.active_runs(self.store))
        self.assertEqual(REAL, run_identity.expected_owner([self.store]))
        shutil.rmtree(child)
        self.assertIsNone(
            run_identity.expected_owner([self.store]), "the record left in the checkout never counts alone"
        )

    def test_a_claimed_move_the_records_do_not_corroborate_is_not_counted(self):
        tree = Path(workspaces.create(self.root, "claimed")["workspace"])
        claimed = tree / ".autocode" / "runs" / "wt-claimed-2222"
        claimed.mkdir(parents=True)
        (claimed / "state.json").write_text(
            json.dumps(
                {
                    "version": 3,
                    "workspace": str(tree),
                    "status": "RUNNING",
                    "build_worktree": {
                        "state": "building",
                        "moved_from": {
                            "workspace": str(self.root),
                            "run_dir": str(self.root / ".autocode" / "runs" / "no-such-origin"),
                        },
                    },
                }
            )
        )
        self.assertEqual({}, run_identity.active_runs(self.store))
        self.assertIsNone(run_identity.expected_owner([self.store]))

    def test_a_run_that_started_in_a_worktree_still_counts(self):
        tree = Path(workspaces.create(self.root, "started")["workspace"])
        started = tree / ".autocode" / "runs" / "wt-start-1111"
        started.mkdir(parents=True)
        (started / "state.json").write_text(json.dumps({"version": 3, "workspace": str(tree), "status": "RUNNING"}))
        self.assertEqual({"wt-start-1111": [started]}, run_identity.active_runs(self.store))
        self.assertEqual("wt-start-1111", run_identity.expected_owner([self.store]))

    def test_a_tree_of_another_repository_is_never_an_authenticated_worktree(self):
        saved = saved_run(self.root)
        tree = self.root / ".autocode" / "worktrees" / "fake-run-9f3e-tree"
        tree.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(tree)], check=True)
        (tree / ".autocode").mkdir()
        (tree / ".autocode" / "task-workspace.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "kind": "task",
                    "project_workspace": str(self.root),
                    "workspace": str(tree),
                    "branch": "autocode/x",
                    "base_commit": "0" * 40,
                }
            )
        )
        fake = tree / ".autocode" / "runs" / "fake-run-9f3e"
        fake.mkdir(parents=True)
        (fake / "state.json").write_text(json.dumps({"version": 3, "workspace": str(tree), "status": "RUNNING"}))
        self.assertEqual({REAL: [saved]}, run_identity.active_runs(self.store))
        self.assertEqual(REAL, run_identity.expected_owner([self.store]))


@unittest.skipIf(run_identity is None, "unit tests require the candidate's run-identity module")
class OutputRefusalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="run-refusal-")
        self.addCleanup(temporary.cleanup)
        self.store = Path(temporary.name).resolve() / ".autocode"
        saved_run(self.store.parent)

    def test_a_missing_checkpoint_is_refused_naming_the_addressed_run_and_the_owner(self):
        refusal = run_identity.output_refusal(self.store / "runs" / TYPO / "evidence" / "repro.json", [self.store])
        self.assertIn(TYPO, refusal)
        self.assertIn("not an existing run", refusal)
        self.assertIn(REAL, refusal)

    def test_an_existing_run_and_paths_outside_runs_are_not_refused(self):
        bare = self.store / "runs" / "existing-shape-without-checkpoint"
        bare.mkdir(parents=True)
        self.assertIsNotNone(run_identity.output_refusal(bare / "evidence" / "owned.json", [self.store]))
        for output in (
            self.store / "runs" / REAL / "evidence" / "owned.json",
            self.store / "evidence" / "project.json",
            self.store / "tool-containment-ab12cd34" / "scratch" / "batch" / "capture.json",
        ):
            with self.subTest(output=output):
                self.assertIsNone(run_identity.output_refusal(output, [self.store]))


@unittest.skipIf(run_identity is None, "unit tests require the candidate's run-identity module")
class FollowTests(unittest.TestCase):
    def setUp(self):
        self.root = checkout(self)

    def test_follow_reads_the_worktree_record_of_an_authenticated_move(self):
        tree, child = moved_run(self.root)
        self.assertEqual((child, tree), run_identity.follow(self.root / ".autocode" / "runs" / REAL, self.root))

    def test_follow_keeps_a_run_that_never_moved_where_it_is(self):
        run_dir = saved_run(self.root)
        self.assertEqual((run_dir, self.root), run_identity.follow(run_dir, self.root))

    def test_follow_leaves_an_unreadable_checkpoint_alone(self):
        run_dir = self.root / ".autocode" / "runs" / "broken"
        run_dir.mkdir(parents=True)
        (run_dir / "state.json").write_text("{not json")
        self.assertEqual((run_dir, self.root), run_identity.follow(run_dir, self.root))


if __name__ == "__main__":
    unittest.main()
