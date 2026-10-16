"""A package run's change outside its package is a runner finding (#711).

The ledger rules are pure over a state dict; the check before review runs on a real Git checkout through
autocode_regression.before_review, in a run that owes no regression proof, and the --accept-completion
refusal (autocode_package_writes.refuse) on the same checkout.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_bug_job as bug_job
import autocode_completion as completion
import autocode_findings as findings_ledger
import autocode_follow_up as follow_up
import autocode_package_writes as package_writes
import autocode_regression as regression
import autocode_util as util
import autocode_workflows as workflows
from autocode_contract_identity import token

from tests.test_bug_job import diagnosis
from tests.test_package_scope import GREET, MONOREPO, OTHER
from tests.test_verify import Project

BASE = "b" * 40
AUTHOR = ["-c", "user.name=t", "-c", "user.email=t@example.test"]
NOTE = "docs/bugs/duplicate-renew.json"  # where tests.test_bug_job's diagnosis asks the runner to write it


def package_state(package=GREET):
    return {"settings": {"package": package} if package else {}, "findings_ledger": []}


def runner_rows(state):
    return [row for row in state.get("findings_ledger") or [] if row.get("source") == "runner"]


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def approve(state, affected_paths):
    """Give ``state`` an approved contract whose one milestone declares ``affected_paths`` (its source paths)."""
    contract = {
        "task_id": "fixture",
        "revision": 1,
        "body": {
            "task_kind": "build",
            "acceptance_criteria": [],
            "milestones": [{"id": "M1", "affected_paths": affected_paths}],
            "open_blocking_questions": [],
        },
    }
    contract["hash"] = util.digest(contract)
    event = {"actor": "user_cli", "token": token(contract)}
    contract.update(approval_status="approved", approval_event=event)
    state.update(goal_contract=contract, user_events=[event])
    return state


class Reconcile(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="package-writes-")
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name)

    def reconcile(self, state, changes, revision):
        return package_writes.reconcile(state, self.workspace, changes, revision, BASE)

    def test_a_run_of_the_whole_repository_is_left_unchanged(self):
        state = package_state(None)
        self.assertIsNone(self.reconcile(state, {"README.md": "modified"}, "r1"))
        self.assertEqual({"settings": {}, "findings_ledger": []}, state)
        self.assertIsNone(package_writes.context({}))
        self.assertIsNone(package_writes.context(None))

    def test_changes_inside_the_package_open_nothing(self):
        state = package_state()
        changes = {f"{GREET}/greet.py": "modified", f"{GREET}/sub/new.py": "added", f"{GREET}/old.py": "deleted"}
        self.assertIsNone(self.reconcile(state, changes, "r1"))
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
        row = self.reconcile(state, changes, "r1")
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
        # Restoring the content alone does not clear a changed mode (changed_files reports it as modified).
        self.assertIn(
            "a file restored with the same content but a different mode still counts as modified", row["finding"]
        )
        # The ordinary gate: completion and every stage's open_findings carry it.
        self.assertEqual([row], findings_ledger.blocking_entries(state))
        self.assertIn(row["id"], completion.ledger_rejection(state))
        self.assertIn(f"Files outside the package {GREET}/ changed", completion.ledger_rejection(state))
        self.assertEqual([row["id"]], [item["id"] for item in findings_ledger.handoff(state)])

    def test_one_finding_follows_the_changes_and_counts_each_new_source_once(self):
        state = package_state()
        first = self.reconcile(state, {f"{OTHER}/other.py": "modified"}, "r1")
        again = self.reconcile(state, {f"{OTHER}/other.py": "modified"}, "r1")
        self.assertIs(first, again)
        self.assertEqual(1, first["times_reported"])
        later = self.reconcile(state, {"README.md": "modified"}, "r2")
        self.assertIs(first, later)
        self.assertEqual(
            (2, {"README.md": "modified"}, "r2"), (later["times_reported"], later["paths"], later["source_revision"])
        )
        self.assertNotIn(OTHER, later["finding"])
        self.assertEqual(1, len(runner_rows(state)))

    def test_only_the_runner_check_closes_it(self):
        state = package_state()
        row = self.reconcile(state, {f"{OTHER}/other.py": "modified"}, "r1")
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
        self.assertIsNone(self.reconcile(state, {f"{GREET}/greet.py": "modified"}, "r2"))
        self.assertEqual("resolved", row["status"])
        self.assertIn(f"no file outside the package {GREET}/ changed", row["resolution_evidence"])
        self.assertEqual([], findings_ledger.blocking_entries(state))

    def test_the_next_check_opens_a_new_finding_after_a_user_closes_one(self):
        # --accept-completion runs the same check (Checkout's refusal test, tests.test_package_scope_cli).
        state = package_state()
        row = self.reconcile(state, {"pnpm-lock.yaml": "modified"}, "r1")
        findings_ledger.settle(row, "resolved", resolved_by="user", resolution_evidence="lockfile is fine")
        reopened = self.reconcile(state, {"pnpm-lock.yaml": "modified"}, "r1")
        self.assertNotEqual(row["id"], reopened["id"])
        self.assertEqual([reopened], findings_ledger.blocking_entries(state))

    def test_the_builder_handoff_names_the_write_scope_and_allows_its_evidence_directory(self):
        scope = package_writes.context({"package": GREET})
        self.assertEqual(f"{GREET}/", scope["package"])
        self.assertIn(f"{GREET}/: change only files inside it", scope["instruction"])
        self.assertNotIn("{", scope["instruction"])
        # builder_artifact_policy asks for optional evidence under .autocode/: the scope must not forbid it.
        self.assertIn(
            "optional evidence written under builder_artifact_policy.evidence_directory is fine", scope["instruction"]
        )
        self.assertNotIn("leave those as they are too", scope["instruction"])

    def test_the_investigator_handoff_names_the_package_and_the_suite_the_run_has(self):
        suite = f"cd {GREET} && python3 -m unittest discover -v"
        settings = {"package": GREET, "protected_tests": {"command": suite}}
        scope = package_writes.context(settings, bug_job.STAGE)
        self.assertEqual((f"{GREET}/", suite), (scope["package"], scope["suite"]))
        self.assertIn(f"inside {GREET}/: the runner rejects a report that names anything else", scope["instruction"])
        # A fix that does need a file outside the package keeps it named, so the report is refused and the run pauses.
        self.assertIn(f"outside {GREET}/, name that file anyway", scope["instruction"])
        self.assertNotIn("{", scope["instruction"])
        # A package with no tests and no --test-command has no suite to name; the Builder's scope never names one.
        self.assertNotIn("suite", package_writes.context({"package": GREET, "protected_tests": {}}, bug_job.STAGE))
        self.assertNotIn("suite", package_writes.context(settings))
        self.assertIsNone(package_writes.context({}, bug_job.STAGE))


class Checkout(unittest.TestCase):
    """The check before review and the --accept-completion refusal, on a real checkout, in a build that owes
    no regression proof."""

    def setUp(self):
        self.project = Project({**MONOREPO, ".gitignore": "build/\n"})
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

    def test_each_tester_and_completion_reviewer_dispatch_checks_and_no_other(self):
        self.project.write({f"{GREET}/greet.py": "print('changed')\n", f"{OTHER}/other.py": "VALUE = 2\n"})
        for stage in (None, "terra", "astra_plan", "review_change", "review_design", "check_design"):
            with self.subTest(stage=stage):
                state = self.state()
                regression.before_review(state, stage, self.project.root, self.run_dir)
                self.assertEqual([], runner_rows(state))
        for stage in ("sol", "astra_checkpoint", "astra_review"):
            with self.subTest(stage=stage):
                state = self.state()
                self.assertFalse(regression.required(state))
                regression.before_review(state, stage, self.project.root, self.run_dir)
                (row,) = runner_rows(state)
                self.assertEqual({f"{OTHER}/other.py": "modified"}, row["paths"])
                self.assertNotIn("regression_proof", state)

    def test_a_change_outside_the_package_is_cleared_when_it_is_gone(self):
        root = self.project.root
        original = (root / OTHER / "other.py").read_text()
        self.project.write(
            {
                f"{GREET}/greet.py": "print('changed')\n",
                f"{OTHER}/other.py": "def answer():\n    return 41 + 1\n",
                # AutoCode's own files, bytecode and a file Git ignores are not changes.
                ".autocode/evidence/check.txt": "evidence\n",
                f"{GREET}/__pycache__/greet.cpython-313.pyc": "bytecode",
                "build/out.txt": "built\n",
            }
        )
        state = self.state()
        regression.before_review(state, "sol", root, self.run_dir)
        (row,) = runner_rows(state)
        self.assertEqual({f"{OTHER}/other.py": "modified"}, row["paths"])
        self.assertEqual("open", row["status"])
        (root / OTHER / "other.py").write_text(original)
        shutil.rmtree(root / ".autocode")
        regression.before_review(state, "astra_review", root, self.run_dir)
        self.assertEqual("resolved", row["status"])

    def test_a_mode_change_alone_counts(self):
        (self.project.root / OTHER / "other.py").chmod(0o755)
        state = self.state()
        regression.before_review(state, "sol", self.project.root, self.run_dir)
        (row,) = runner_rows(state)
        self.assertEqual({f"{OTHER}/other.py": "modified"}, row["paths"])

    def test_an_ignored_file_counts_only_where_the_approved_plan_declares_it(self):
        # The check reads changed_files with the approved plan's source paths, as the proof does.
        self.project.write({f"{GREET}/greet.py": "print('changed')\n", "build/out.txt": "built\n"})
        undeclared = approve(self.state(), [f"{GREET}/greet.py"])
        regression.before_review(undeclared, "sol", self.project.root, self.run_dir)
        self.assertEqual([], runner_rows(undeclared))
        declared = approve(self.state(), [f"{GREET}/greet.py", "build/out.txt"])
        regression.before_review(declared, "sol", self.project.root, self.run_dir)
        (row,) = runner_rows(declared)
        self.assertEqual({"build/out.txt": "added"}, row["paths"])

    def test_accept_completion_is_refused_while_a_change_outside_the_package_is_there(self):
        root = self.project.root
        state = self.state()
        self.assertIsNone(package_writes.refuse(state, root))
        self.project.write({"README.md": "# edited\n"})
        with self.assertRaises(ValueError) as refused:
            package_writes.refuse(state, root)
        self.assertIn("Completion acceptance requires no change outside the package.", str(refused.exception))
        self.assertIn("README.md (modified)", str(refused.exception))
        # A person closing the finding accepts nothing: the next refusal checks again.
        (row,) = runner_rows(state)
        findings_ledger.settle(row, "resolved", resolved_by="user", resolution_evidence="fine")
        with self.assertRaises(ValueError):
            package_writes.refuse(state, root)
        git(root, "checkout", "--", "README.md")
        self.assertIsNone(package_writes.refuse(state, root))
        self.assertEqual([], findings_ledger.blocking_entries(state))
        whole = self.state(package=None)
        self.project.write({"README.md": "# edited\n"})
        self.assertIsNone(package_writes.refuse(whole, root))
        self.assertNotIn("findings_ledger", whole)


class RunnerFiles(unittest.TestCase):
    """The files the runner wrote for the run: exempt while unchanged where it recorded their content."""

    def setUp(self):
        self.project = Project(MONOREPO)
        self.addCleanup(self.project.close)
        temp = tempfile.TemporaryDirectory(prefix="package-writes-")
        self.addCleanup(temp.cleanup)
        self.run_dir = Path(temp.name)
        self.state = {
            "base_commit": self.project.base,
            "settings": {"package": GREET},
            "workflow": {"kind": "bugfix"},
            "goal_contract": {"body": {"task_kind": "build", "acceptance_criteria": [], "milestones": [{"id": "M1"}]}},
        }
        self.project.write({f"{GREET}/greet.py": "print('changed')\n"})

    def check(self):
        regression.before_review(self.state, "sol", self.project.root, self.run_dir)
        return runner_rows(self.state)

    @staticmethod
    def report():
        """A reproduced diagnosis inside the package, as a package run's Investigator must give it."""
        return diagnosis(affected_paths=[f"{GREET}/greet.py"], test_paths=[f"{GREET}/test_greet.py"])

    def write_note(self):
        """The bug-fix workflow's runner note, written as autocode_bug_job.apply writes it."""
        report = self.report()
        output = self.run_dir / "bug-investigation-01.json"
        output.write_text(json.dumps(report))
        bug_job.apply(self.state, report, {"changed_files": [], "output": str(output)}, self.project.root)
        return self.project.root / NOTE

    def test_the_unchanged_runner_note_is_left_out_and_a_modified_one_is_reported(self):
        note = self.write_note()
        self.assertEqual([], self.check())
        rewritten = json.loads(note.read_text())
        rewritten["conclusion"] = "the Builder's own conclusion"
        note.write_text(json.dumps(rewritten, indent=2) + "\n")
        (row,) = self.check()
        self.assertEqual(({}, {NOTE: "added"}, {}), (row["paths"], row["runner_files"], row["held"]))
        self.assertIn(f"The runner wrote {NOTE} for this run, and it changed since", row["finding"])
        self.assertIn("do not delete it", row["finding"])
        self.assertNotIn("remove an added file", row["finding"])
        # Put back, it is the runner's again and the finding closes.
        note.write_text(json.dumps({**bug_job.note(self.report()), "proven_by": ""}, indent=2) + "\n")
        self.assertEqual([], [row for row in self.check() if row["status"] == "open"])
        self.assertEqual("resolved", row["status"])

    def test_the_note_of_an_investigation_that_did_not_reproduce_is_compared_by_its_content(self):
        """Such a note has no proof for diagnosis_artifact to identify. Its turn completes; a follow-up build
        keeps the investigation, owes no proof and is still checked before the Tester."""
        self.state = {"base_commit": self.project.base, "settings": {"package": GREET}, "workflow": {"kind": "bugfix"}}
        package_writes.at_launch(self.state, self.project.root)
        report = diagnosis("not_reproduced", questions=[])
        output = self.run_dir / "bug-investigation-01.json"
        output.write_text(json.dumps(report))
        bug_job.apply(self.state, report, {"changed_files": [], "output": str(output)}, self.project.root)
        self.assertEqual("TASK_COMPLETE", self.state["status"])
        follow_up.accept(self.state, "Add a None guard to greet anyway", self.project.root, "2026-10-16T00:00:00+00:00")
        workflows.apply(self.state, {"workflow": "build", "reason": "", "signals": []}, {})
        self.assertFalse(regression.required(self.state))
        self.assertEqual([], self.check())
        self.assertIsNone(package_writes.refuse(self.state, self.project.root))
        # An edit to it is seen, as the runner's file, and putting it back closes the finding.
        path = report["note_path"]
        note = self.project.root / path
        written = note.read_text()
        note.write_text(json.dumps({**json.loads(written), "conclusion": "the Builder's own"}, indent=2) + "\n")
        (row,) = self.check()
        self.assertEqual(({}, {path: "added"}), (row["paths"], row["runner_files"]))
        note.write_text(written)
        self.assertEqual([], [row for row in self.check() if row["status"] == "open"])
        self.assertEqual("resolved", row["status"])

    def test_a_report_with_a_saved_hash_is_compared_and_path_only_files_are_left_out_unread(self):
        report = self.project.root / "review/findings.json"
        report.parent.mkdir()
        report.write_text('{"findings": []}\n')
        self.project.write({"docs/design/blockers.json": "{}\n", "docs/design/plan.md": "# Plan\n"})
        self.state.update(
            review={
                "report_path": "review/findings.json",
                "report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
            },
            design_check={"blockers": "docs/design/blockers.json"},
            # An earlier turn's report and what else it wrote (autocode_follow_up).
            turns=[{"previous": {"wrote": ["review/findings.json", "docs/design/plan.md"]}}],
        )
        self.assertEqual([], self.check())
        # Only path is recorded for these two: an edit to them is not seen (docs/workflow.md says so).
        self.project.write({"docs/design/blockers.json": '{"edited": true}\n', "docs/design/plan.md": "# Edited\n"})
        self.assertEqual([], self.check())
        # The report's saved hash is: an edited report is reported, as the runner's file.
        report.write_text('{"findings": [], "edited": true}\n')
        (row,) = self.check()
        self.assertEqual(({}, {"review/findings.json": "added"}), (row["paths"], row["runner_files"]))
        # The Builder's own change outside the package is named beside it.
        self.project.write({"README.md": "# edited\n"})
        (row,) = self.check()
        self.assertEqual({"README.md": "modified"}, row["paths"])


class Launch(unittest.TestCase):
    """What an in-place checkout holds outside the package at launch that the run's base cannot hold."""

    def checkout(self, *, held=True):
        """A two-package checkout; with ``held``, beside the person's submodule with uncommitted work in
        libs/lib and an untracked Git repository in vendored/. An in-place base (verify.commit_worktree)
        cannot hold either, so changed_files reports them against any base."""
        project = Project(MONOREPO)
        self.addCleanup(project.close)
        temp = tempfile.TemporaryDirectory(prefix="package-writes-")
        self.addCleanup(temp.cleanup)
        self.run_dir = Path(temp.name)
        root = Path(project.root)
        if held:
            library = Project({"lib.c": "int lib;\n"})
            self.addCleanup(library.close)
            git(root, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(library.root), "libs/lib")
            git(root, *AUTHOR, "commit", "-qm", "add a submodule")
            (root / "libs/lib/lib.c").write_text("int lib = 1;\n")
            nested = root / "vendored"
            nested.mkdir()
            git(nested, "init", "-q")
            (nested / "x.txt").write_text("vendored\n")
            git(nested, "add", "x.txt")
            git(nested, *AUTHOR, "commit", "-qm", "vendored")
        return root

    def launched(self, root, package=GREET):
        state = {"settings": {"package": package} if package else {}}
        regression.start(state, root, self.run_dir, package)
        return state

    def check(self, state, root):
        regression.before_review(state, "sol", root, self.run_dir)
        return runner_rows(state)

    def test_every_new_package_run_records_what_its_checkout_held(self):
        root = self.checkout(held=False)
        self.assertEqual({}, self.launched(root)["package_outside_at_launch"])
        self.assertNotIn("package_outside_at_launch", self.launched(root, package=None))
        root = self.checkout()
        self.assertEqual(
            {"libs/lib": "modified", "vendored/": "added"}, self.launched(root)["package_outside_at_launch"]
        )

    def test_held_git_work_is_left_out_while_it_is_still_a_repository(self):
        root = self.checkout()
        state = self.launched(root)
        (root / GREET / "greet.py").write_text("print('changed')\n")
        (root / "README.md").write_text("# edited\n")
        (root / "libs/lib/lib.c").write_text("int lib = 2;\n")  # a later change inside it is not seen
        (row,) = self.check(state, root)
        self.assertEqual(({"README.md": "modified"}, {}), (row["paths"], row["held"]))

    def test_held_git_work_deleted_or_replaced_is_reported_and_left_to_the_person(self):
        def delete_the_submodule(root):
            shutil.rmtree(root / "libs/lib")

        def replace_the_submodule_with_a_file(root):
            shutil.rmtree(root / "libs/lib")
            (root / "libs/lib").write_text("not a submodule\n")

        def unmake_the_repository_and_add_code(root):
            shutil.rmtree(root / "vendored/.git")
            (root / "vendored/new.py").write_text("VALUE = 1\n")

        for change, held in (
            (delete_the_submodule, {"libs/lib": "deleted"}),
            (replace_the_submodule_with_a_file, {"libs/lib": "no longer a Git repository"}),
            (unmake_the_repository_and_add_code, {"vendored/": "no longer a Git repository"}),
        ):
            with self.subTest(change=change.__name__):
                root = self.checkout()
                state = self.launched(root)
                change(root)
                (row,) = self.check(state, root)
                self.assertEqual(({}, held), (row["paths"], row["held"]))
                self.assertIn("Do not delete, move or reset anything there: the person must restore it", row["finding"])
                # Never the generic advice, which would have the Builder delete the person's files.
                self.assertNotIn("remove an added file", row["finding"])
                self.assertNotIn("vendored/x.txt", row["finding"])
                self.assertNotIn("vendored/new.py", row["finding"])

    def test_a_run_saved_without_the_launch_record_never_reports_a_nested_repository(self):
        # A package run started before the record existed: it cannot tell what the checkout held at launch.
        root = self.checkout()
        saved = self.launched(root)
        legacy = {key: value for key, value in saved.items() if key != "package_outside_at_launch"}
        (root / GREET / "greet.py").write_text("print('changed')\n")
        (root / "README.md").write_text("# edited\n")
        (row,) = self.check(legacy, root)
        self.assertEqual({"README.md": "modified"}, row["paths"])
        self.assertNotIn("vendored", row["finding"])
        self.assertNotIn("libs/lib", row["finding"])


if __name__ == "__main__":
    unittest.main()
