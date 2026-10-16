"""A package run's change outside its package is a runner finding (#711).

The ledger rules are pure over a state dict; the check before review runs on a real Git checkout through
autocode_regression.before_review, in a run that owes no regression proof.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_completion as completion
import autocode_findings as findings_ledger
import autocode_package_writes as package_writes
import autocode_regression as regression

from tests.test_package_scope import GREET, MONOREPO, OTHER
from tests.test_verify import Project

BASE = "b" * 40


def package_state(package=GREET):
    return {"settings": {"package": package} if package else {}, "findings_ledger": []}


def runner_rows(state):
    return [row for row in state.get("findings_ledger") or [] if row.get("source") == "runner"]


class Reconcile(unittest.TestCase):
    def test_a_run_of_the_whole_repository_is_left_unchanged(self):
        state = package_state(None)
        self.assertIsNone(package_writes.reconcile(state, {"README.md": "modified"}, "r1", BASE))
        self.assertEqual({"settings": {}, "findings_ledger": []}, state)
        self.assertIsNone(package_writes.context({}))
        self.assertIsNone(package_writes.context(None))

    def test_changes_inside_the_package_open_nothing(self):
        state = package_state()
        changes = {f"{GREET}/greet.py": "modified", f"{GREET}/sub/new.py": "added", f"{GREET}/old.py": "deleted"}
        self.assertIsNone(package_writes.reconcile(state, changes, "r1", BASE))
        self.assertEqual([], state["findings_ledger"])

    def test_each_change_outside_is_named_in_one_blocking_runner_finding(self):
        state = package_state()
        changes = {
            f"{GREET}/greet.py": "modified",
            f"{OTHER}/other.py": "modified",
            f"{OTHER}/new.py": "added",
            "README.md": "deleted",
            "packages/greeter/x.py": "added",
            GREET: "added",  # a file at the package path is not inside it
        }
        row = package_writes.reconcile(state, changes, "r1", BASE)
        outside = {
            GREET: "added",
            "README.md": "deleted",
            "packages/greeter/x.py": "added",
            f"{OTHER}/new.py": "added",
            f"{OTHER}/other.py": "modified",
        }
        self.assertEqual(outside, row["paths"])
        self.assertEqual(list(sorted(outside)), list(row["paths"]))
        self.assertEqual(("runner", "open", True, None), (row["source"], row["status"], row["blocking"], row["scope"]))
        for path, change in outside.items():
            self.assertIn(f"{path} ({change})", row["finding"])
        self.assertNotIn(f"{GREET}/greet.py", row["finding"])
        self.assertIn(BASE[:12], row["finding"])
        # The ordinary gate: completion and every stage's open_findings carry it.
        self.assertEqual([row], findings_ledger.blocking_entries(state))
        self.assertIn(row["id"], completion.ledger_rejection(state))
        self.assertIn(f"Files outside the package {GREET}/ changed", completion.ledger_rejection(state))
        self.assertEqual([row["id"]], [item["id"] for item in findings_ledger.handoff(state)])

    def test_one_finding_follows_the_changes_and_counts_each_new_source_once(self):
        state = package_state()
        first = package_writes.reconcile(state, {f"{OTHER}/other.py": "modified"}, "r1", BASE)
        again = package_writes.reconcile(state, {f"{OTHER}/other.py": "modified"}, "r1", BASE)
        self.assertIs(first, again)
        self.assertEqual(1, first["times_reported"])
        later = package_writes.reconcile(state, {"README.md": "modified"}, "r2", BASE)
        self.assertIs(first, later)
        self.assertEqual(
            (2, {"README.md": "modified"}, "r2"), (later["times_reported"], later["paths"], later["source_revision"])
        )
        self.assertNotIn(OTHER, later["finding"])
        self.assertEqual(1, len(runner_rows(state)))

    def test_only_the_runner_check_closes_it(self):
        state = package_state()
        row = package_writes.reconcile(state, {f"{OTHER}/other.py": "modified"}, "r1", BASE)
        # A reviewer's disposition applies to its own findings only.
        findings_ledger.record_validation(
            state,
            {
                "verdict": "PASS",
                "findings": [],
                "finding_dispositions": [{"id": row["id"], "disposition": "resolved", "evidence": "x"}],
            },
            {"output": "validator-01.json"},
        )
        self.assertEqual("open", row["status"])
        self.assertIsNone(package_writes.reconcile(state, {f"{GREET}/greet.py": "modified"}, "r2", BASE))
        self.assertEqual("resolved", row["status"])
        self.assertIn(f"no file outside the package {GREET}/ changed", row["resolution_evidence"])
        self.assertEqual([], findings_ledger.blocking_entries(state))

    def test_the_next_check_opens_a_new_finding_after_a_user_closes_one(self):
        # --accept-completion runs the same check (tests.test_package_scope_cli shows the refusal).
        state = package_state()
        row = package_writes.reconcile(state, {"pnpm-lock.yaml": "modified"}, "r1", BASE)
        findings_ledger.settle(row, "resolved", resolved_by="user", resolution_evidence="lockfile is fine")
        reopened = package_writes.reconcile(state, {"pnpm-lock.yaml": "modified"}, "r1", BASE)
        self.assertNotEqual(row["id"], reopened["id"])
        self.assertEqual([reopened], findings_ledger.blocking_entries(state))

    def test_the_builder_handoff_names_the_write_scope(self):
        scope = package_writes.context({"package": GREET})
        self.assertEqual(f"{GREET}/", scope["package"])
        self.assertIn(f"{GREET}/: change only files inside it", scope["instruction"])
        self.assertNotIn("{", scope["instruction"])


class BeforeReview(unittest.TestCase):
    """The check before review, on a real checkout, in a build that owes no regression proof."""

    def setUp(self):
        self.project = Project(MONOREPO)
        self.addCleanup(self.project.close)
        temp = tempfile.TemporaryDirectory(prefix="package-writes-")
        self.addCleanup(temp.cleanup)
        self.run_dir = Path(temp.name)

    def state(self, package=GREET):
        return {
            "base_commit": self.project.base,
            "settings": {"package": package} if package else {},
            "goal_contract": {"body": {"task_kind": "build", "acceptance_criteria": [], "milestones": [{"id": "M1"}]}},
        }

    def test_a_change_outside_the_package_is_found_before_review_and_cleared_when_it_is_gone(self):
        root = self.project.root
        original = (root / OTHER / "other.py").read_text()
        self.project.write(
            {
                f"{GREET}/greet.py": "print('changed')\n",
                f"{OTHER}/other.py": "def answer():\n    return 41 + 1\n",
                # AutoCode's own files and bytecode are not changes.
                ".autocode/evidence/check.txt": "evidence\n",
                f"{GREET}/__pycache__/greet.cpython-313.pyc": "bytecode",
            }
        )
        state = self.state()
        self.assertFalse(regression.required(state))
        for stage in (None, "terra", "astra_plan"):
            with self.subTest(stage=stage):
                regression.before_review(state, stage, root, self.run_dir)
                self.assertEqual([], runner_rows(state))
        regression.before_review(state, "sol", root, self.run_dir)
        (row,) = runner_rows(state)
        self.assertEqual({f"{OTHER}/other.py": "modified"}, row["paths"])
        self.assertEqual("open", row["status"])
        self.assertNotIn("regression_proof", state)
        (root / OTHER / "other.py").write_text(original)
        shutil.rmtree(root / ".autocode")
        regression.before_review(state, "astra_review", root, self.run_dir)
        self.assertEqual("resolved", row["status"])

    def test_files_the_runner_wrote_for_the_run_or_an_earlier_turn_left_are_not_the_builders_change(self):
        root = self.project.root
        self.project.write(
            {
                f"{GREET}/greet.py": "print('changed')\n",
                "docs/bugs/blank-name.json": "{}\n",  # a bug fix's diagnosis note (autocode_bug_job)
                "docs/design/blockers.json": "{}\n",  # a design check's blockers (autocode_design_check_job)
                "review/findings.json": "{}\n",  # an earlier review turn's report (autocode_follow_up)
                "docs/design/plan.md": "# Plan\n",  # an earlier design turn's document
            }
        )
        state = {
            **self.state(),
            "investigation": {"outcome": "reproduced", "note_path": "docs/bugs/blank-name.json"},
            "design_check": {"blockers": "docs/design/blockers.json"},
            "turns": [
                {"previous": {"wrote": ["review/findings.json"]}},
                {"previous": {"wrote": ["docs/design/plan.md"]}},
            ],
        }
        regression.before_review(state, "sol", root, self.run_dir)
        self.assertEqual([], runner_rows(state))
        # The Builder's own change outside the package still counts, and only it is named.
        self.project.write({"README.md": "# edited\n"})
        regression.before_review(state, "sol", root, self.run_dir)
        (row,) = runner_rows(state)
        self.assertEqual({"README.md": "modified"}, row["paths"])

    def test_a_nested_repository_or_submodule_work_held_at_launch_is_not_the_builders_change(self):
        # An in-place run's base (verify.commit_worktree) cannot hold either one, so changed_files reports
        # them against any base: an untracked Git repository as added, a submodule's uncommitted work as modified.
        root = Path(self.project.root)
        library = Project({"lib.c": "int lib;\n"})
        self.addCleanup(library.close)
        author = ["-c", "user.name=t", "-c", "user.email=t@example.test"]
        subprocess.run(
            ["git", "-C", str(root), "-c", "protocol.file.allow=always", "submodule", "add", "-q"]
            + [str(library.root), "libs/lib"],
            check=True,
            capture_output=True,
        )
        subprocess.run(["git", "-C", str(root), *author, "commit", "-qm", "add a submodule"], check=True)
        (root / "libs/lib/lib.c").write_text("int lib = 1;\n")  # the person's uncommitted submodule work
        nested = root / "vendored"
        nested.mkdir()
        subprocess.run(["git", "-C", str(nested), "init", "-q"], check=True)
        (nested / "x.txt").write_text("vendored\n")
        subprocess.run(["git", "-C", str(nested), "add", "x.txt"], check=True)
        subprocess.run(["git", "-C", str(nested), *author, "commit", "-qm", "vendored"], check=True)
        launched = self.state()
        regression.start(launched, root, self.run_dir, GREET)
        unrecorded = {**self.state(), "base_commit": launched["base_commit"]}
        self.project.write({f"{GREET}/greet.py": "print('changed')\n", "README.md": "# edited\n"})
        for state, expected in (
            (launched, {"README.md": "modified"}),
            # Without the launch record, both would be reported as the Builder's.
            (unrecorded, {"README.md": "modified", "libs/lib": "modified", "vendored/": "added"}),
        ):
            regression.before_review(state, "sol", root, self.run_dir)
            (row,) = runner_rows(state)
            self.assertEqual(expected, row["paths"])

    def test_a_run_without_a_package_is_not_checked(self):
        self.project.write({f"{OTHER}/other.py": "def answer():\n    return 0\n"})
        state = self.state(package=None)
        regression.before_review(state, "sol", self.project.root, self.run_dir)
        self.assertNotIn("findings_ledger", state)


if __name__ == "__main__":
    unittest.main()
