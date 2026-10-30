"""A task in one package of a two-package repository, through the public CLI (#711).

Every stage runs on the offline fixture provider. The run is started with --package: its suite is
detected in that package and runs from its directory, only that package's tests are protected, and a
Builder write outside the package is a runner finding that keeps the run from completing, in a bug fix
and in a build that owes no regression proof. The findings and the run's outcome are read through its
public status view (--status), not the ledger's private fields; the launch record, which the view does
not show, is read from the saved state.
"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

import autocode_checkout_lock as checkout_lock
import scenario_references as references

from tests import test_planning, test_subprocess
from tests.test_package_scope import FIX, GREET, MONOREPO, OTHER

# The offline Validator runs greet.py from the repository root. This launcher keeps that check meaningful
# without giving the package's tests a greet module at the root.
LAUNCHER = (
    '"""Run the greeting package\'s CLI from the repository root."""\n'
    "import runpy\nimport sys\nfrom pathlib import Path\n\n"
    'sys.argv[0] = str(Path(__file__).resolve().parent / "packages" / "greet" / "greet.py")\n'
    'runpy.run_path(sys.argv[0], run_name="__main__")\n'
)


class PackageRunCLI(unittest.TestCase):
    new_run_engine_args = ()
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    prepare = test_planning.JointFlow.prepare

    def setUp(self):
        test_subprocess.SubprocessFlow.setUp(self)
        references.write({**MONOREPO, "greet.py": LAUNCHER}, self.project)
        subprocess.run(["git", "-C", str(self.project), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(self.project), "-c", "user.name=t", "-c", "user.email=t@example.test"]
            + ["commit", "-qm", "seed: two packages, the greeting one with the blank-name bug"],
            check=True,
        )
        self.env["AUTOCODE_FIXTURE_TASK_KIND"] = "bugfix"
        self.prepare()

    def builder_writes(self, files):
        path = self.root / "builder-files.json"
        path.write_text(json.dumps(files))
        self.env["AUTOCODE_FIXTURE_FILES"] = str(path)

    def start(self):
        self.launch(["Fix the greeting CLI", "--package", f"{GREET}/", "--no-chat"], 2)
        return self.saved()

    def run_to_end(self, expected, *, started=None, before_approval=None):
        run = started or self.start()[0]
        self.launch(["--run-dir", str(run), "--answer", "Q1=CLI"], 0)
        self.launch(["--run-dir", str(run), "--no-chat"], 2)
        approved = self.saved()[1]
        self.assertEqual("AWAITING_GOAL_APPROVAL", approved["status"])
        if before_approval:
            before_approval(run)
        self.launch(["--run-dir", str(run), "--approve-goal", approved["displayed_goal"]], 0)
        result = self.launch(["--run-dir", str(run), "--no-chat"], expected)
        return run, self.saved()[1], result

    def view(self, run):
        """The run's public status view (autocode_run_view.view)."""
        return json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)["view"]

    @staticmethod
    def runner_findings(view):
        return [row for row in view["evidence"]["findings"] if row["source"] == "runner"]

    def test_the_package_suite_proves_the_fix_and_only_its_tests_are_protected(self):
        self.builder_writes(FIX)
        run, state = self.start()
        self.assertEqual(GREET, state["settings"]["package"])
        protected = state["settings"]["protected_tests"]
        self.assertEqual([f"{GREET}/test_greet.py"], sorted(protected["files"]))
        self.assertTrue(protected["command"].startswith("cd packages/greet && "), protected["command"])
        self.assertTrue(protected["command"].endswith(" -m unittest discover -v"), protected["command"])
        status = json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)
        self.assertEqual(GREET, status["view"]["package"])

        _, final, result = self.run_to_end(0)
        self.assertEqual("TASK_COMPLETE", final["status"], result.stdout + result.stderr)
        proof = final["regression_proof"]
        self.assertEqual("PASS", proof["verdict"], proof)
        self.assertEqual("PASS", proof["changed_code_evidence"]["status"])
        self.assertTrue(proof["mutation_check"]["collected"])
        self.assertEqual(["test_greet.TestGreet.test_blank_name_rejected"], proof["fail_to_pass"])
        self.assertEqual(protected["command"], proof["commands"]["suite"])
        replay = final["validation"]["check_replay"]["protected_tests"]
        self.assertEqual(("PASS", [f"{GREET}/test_greet.py"]), (replay["verdict"], replay["changed_tests"]))

    def outside_finding(self, run, result):
        """The completion gate refused the Completion Reviewer's COMPLETE on the runner's one open package
        finding, as the status view shows it. The unpassed regression proof is itself a separate public
        runner finding (#2475), so the package violation is selected by its public wording; each test
        then asserts the stop surface its own completion route produces."""
        view = self.view(run)
        self.assertFalse(view["done"], result.stdout + result.stderr)
        self.assertNotEqual("TASK_COMPLETE", view["status"])
        (row,) = [row for row in self.runner_findings(view) if row["finding"].startswith("Files outside the package ")]
        self.assertEqual(("open", True, "high"), (row["status"], row["blocking"], row["severity"]))
        self.assertTrue(row["finding"].startswith("Files outside the package packages/greet/ changed: "), row)
        return view, row

    def test_a_builder_write_to_the_other_package_keeps_the_run_from_completing(self):
        self.builder_writes({**FIX, f"{OTHER}/other.py": "def answer():\n    return 41 + 1\n"})
        run, _, result = self.run_to_end(2)
        view, row = self.outside_finding(run, result)
        self.assertIn(f"Files outside the package {GREET}/ changed: {OTHER}/other.py (modified). ", row["finding"])
        # #2475: the refused proof is itself a second open blocking runner finding, separate from the
        # package violation, so both public rows keep the run from completing; its correction route
        # sends the completion back, and the fixture Investigator cannot diagnose, so the run pauses.
        # Each finding is selected by its own public wording; another legitimate runner finding
        # would not blur either one.
        [proof_finding] = [
            found
            for found in self.runner_findings(view)
            if found["finding"].startswith("The runner's regression proof does not pass")
        ]
        self.assertNotEqual(row["id"], proof_finding["id"])
        self.assertEqual(
            ("open", True, "high"), (proof_finding["status"], proof_finding["blocking"], proof_finding["severity"])
        )
        self.assertIn("regression proof does not pass", proof_finding["finding"])
        self.assertIn("asked again to complete while the runner's regression proof does not pass", view["stop_reason"])
        # The proof still says its suite never ran the other package.
        proof = view["evidence"]["regression_proof"]
        self.assertEqual("UNVERIFIED", proof["verdict"])
        self.assertIn(
            "Files outside the package packages/greet/ changed, and its suite does not cover them: "
            "packages/other/other.py",
            proof["unverified"],
        )

    def test_a_run_that_owes_no_proof_reports_each_change_outside_the_package(self):
        # Even a build whose criteria name no test owes changed-product proof;
        # the package finding must remain visible beside that proof's refusal.
        self.env["AUTOCODE_FIXTURE_TASK_KIND"] = "build"
        self.builder_writes(
            {
                **FIX,
                f"{OTHER}/other.py": "def answer():\n    return 41 + 1\n",
                f"{OTHER}/notes.md": "A file the other package did not have\n",
                "README.md": "# Two packages, edited from the greeting package's task\n",
            }
        )
        run, _, result = self.run_to_end(2)
        view, row = self.outside_finding(run, result)
        self.assertEqual("UNVERIFIED", view["evidence"]["regression_proof"]["verdict"])
        self.assertIn(
            f"changed: README.md (modified), {OTHER}/notes.md (added), {OTHER}/other.py (modified). ", row["finding"]
        )
        # Closing the finding supplies no passing proof. That earlier independent-evidence
        # guard refuses acceptance before the separate package boundary guard can speak.
        self.launch(["--run-dir", str(run), "--close-finding", row["id"], "--close-reason", "the edits are fine"], 0)
        self.assertFalse(self.view(run)["done"])
        refused = self.launch(["--run-dir", str(run), "--accept-completion"], 2)
        self.assertIn("Completion acceptance requires current passing independent evidence", refused.stderr)
        self.assertFalse(self.view(run)["done"])

    def test_a_nested_repository_a_busy_checkout_held_at_launch_is_not_the_builders_change(self):
        # An in-place run's base cannot hold an untracked Git repository, so it is recorded with the base. A
        # run that finds another run's agents in the checkout takes both once it holds the checkout.
        nested = self.project / "vendored"
        nested.mkdir()
        subprocess.run(["git", "-C", str(nested), "init", "-q"], check=True)
        (nested / "x.txt").write_text("vendored\n")
        subprocess.run(["git", "-C", str(nested), "add", "x.txt"], check=True)
        subprocess.run(
            ["git", "-C", str(nested), "-c", "user.name=t", "-c", "user.email=t@example.test"]
            + ["commit", "-qm", "the person's own repository"],
            check=True,
        )
        self.env["AUTOCODE_FIXTURE_TASK_KIND"] = "build"
        self.builder_writes(FIX)
        with checkout_lock.exclusive(self.project, self.project / ".autocode" / "runs" / "other-run"):
            busy = self.launch(["Fix the greeting CLI", "--package", f"{GREET}/", "--no-chat"], 2)
        self.assertIn("Another AutoCode run is working in this checkout", busy.stderr)
        run, waiting = self.saved()
        self.assertEqual((None, None), (waiting["base_commit"], waiting.get("package_outside_at_launch")))
        self.launch(["--run-dir", str(run), "--no-chat"], 2)
        self.assertEqual({"vendored/": "added"}, self.saved()[1]["package_outside_at_launch"])
        _, _, result = self.run_to_end(0, started=run)
        view = self.view(run)
        self.assertEqual((True, "TASK_COMPLETE"), (view["done"], view["status"]), result.stdout + result.stderr)
        self.assertEqual([], self.runner_findings(view))
        self.assertEqual("vendored\n", (nested / "x.txt").read_text())

    def test_a_run_that_owes_no_proof_completes_when_every_change_is_in_the_package(self):
        self.env["AUTOCODE_FIXTURE_TASK_KIND"] = "build"
        self.builder_writes(FIX)

        def unapproved_with_an_outside_edit(run):
            # --accept-completion's own guards speak before the package check: the plan is not approved yet.
            (self.project / "README.md").write_text("# edited\n")
            refused = self.launch(["--run-dir", str(run), "--accept-completion"], 2)
            self.assertIn("Completion acceptance requires an approved goal", refused.stderr)
            self.assertNotIn("change outside the package", refused.stderr)
            subprocess.run(["git", "-C", str(self.project), "checkout", "--", "README.md"], check=True)

        run, final, result = self.run_to_end(0, before_approval=unapproved_with_an_outside_edit)
        # A launch record is always written in a new package run: {} when nothing is held (not in the view).
        self.assertEqual({}, final["package_outside_at_launch"])
        view = self.view(run)
        self.assertEqual((True, "TASK_COMPLETE"), (view["done"], view["status"]), result.stdout + result.stderr)
        self.assertEqual("PASS", view["evidence"]["regression_proof"]["verdict"])
        self.assertEqual("PASS", view["evidence"]["regression_proof"]["changed_code_evidence"]["status"])
        self.assertEqual([], self.runner_findings(view))
        # And a finished run says so, not that a later edit outside the package is there.
        (self.project / "README.md").write_text("# edited after completion\n")
        refused = self.launch(["--run-dir", str(run), "--accept-completion"], 2)
        self.assertIn("Run is already complete", refused.stderr)
        self.assertNotIn("change outside the package", refused.stderr)

    def test_the_package_is_checked_and_fixed_when_the_run_starts(self):
        for package, message in (
            ("../elsewhere", "--package must be a directory inside the repository"),
            ("packages/missing", "--package packages/missing/ is not a directory"),
        ):
            with self.subTest(package=package):
                result = self.launch(["Fix the greeting CLI", "--package", package, "--no-chat"], 2)
                self.assertIn(message, result.stderr)
                self.assertFalse((self.project / ".autocode" / "runs").exists())
        result = self.launch(["Fix the greeting CLI", "--package", GREET, "--test-root", GREET, "--no-chat"], 2)
        self.assertIn("--package already selects the suite", result.stderr)
        run, _ = self.start()
        result = self.launch(["--run-dir", str(run), "--package", OTHER, "--no-chat"], 2)
        self.assertIn("--package is fixed when a run starts", result.stderr)
        self.assertEqual(GREET, self.saved()[1]["settings"]["package"])

    def test_a_new_task_worktree_needs_the_package_committed(self):
        # Without --in-place the run works in a new worktree of HEAD, where an uncommitted package is absent.
        (self.project / "packages/new").mkdir()
        (self.project / "packages/new/module.py").write_text("VALUE = 1\n")
        result = subprocess.run(
            [*self.entry, "--workspace", str(self.project), "Fix the module", "--package", "packages/new", "--no-chat"],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=240,
        )
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn(
            "--package packages/new/ holds no committed files (a new task worktree starts from HEAD)", result.stderr
        )
        worktrees = subprocess.run(
            ["git", "-C", str(self.project), "worktree", "list", "--porcelain"], capture_output=True, text=True
        )
        self.assertEqual(1, worktrees.stdout.count("worktree "), worktrees.stdout)
        self.assertFalse((self.project / ".autocode" / "runs").exists())
        # A committed package starts in a new worktree of HEAD, which holds nothing its base cannot: the launch
        # record is still written, {} (not in the status view).
        started = subprocess.run(
            [*self.entry, "--workspace", str(self.project), "Fix the greeting CLI", "--package", GREET, "--no-chat"],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=240,
        )
        worktree = next(
            line.removeprefix("Task worktree: ")
            for line in started.stdout.splitlines()
            if line.startswith("Task worktree: ")
        )
        (state,) = [json.loads(path.read_text()) for path in Path(worktree).glob(".autocode/runs/*/state.json")]
        self.assertEqual((GREET, {}), (state["settings"]["package"], state["package_outside_at_launch"]))


if __name__ == "__main__":
    unittest.main()
