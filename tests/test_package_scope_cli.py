"""A task in one package of a two-package repository, through the public CLI (#711).

Every stage runs on the offline fixture provider. The run is started with --package: its suite is
detected in that package and runs from its directory, only that package's tests are protected, and a
Builder write outside the package is a runner finding that keeps the run from completing, in a bug fix
and in a build that owes no regression proof.
"""

from __future__ import annotations

import json
import subprocess
import unittest

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

    def run_to_end(self, expected):
        run, _ = self.start()
        self.launch(["--run-dir", str(run), "--answer", "Q1=CLI"], 0)
        self.launch(["--run-dir", str(run), "--no-chat"], 2)
        approved = self.saved()[1]
        self.assertEqual("AWAITING_GOAL_APPROVAL", approved["status"])
        self.launch(["--run-dir", str(run), "--approve-goal", approved["displayed_goal"]], 0)
        result = self.launch(["--run-dir", str(run), "--no-chat"], expected)
        return run, self.saved()[1], result

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
        self.assertEqual(["test_greet.TestGreet.test_blank_name_rejected"], proof["fail_to_pass"])
        self.assertEqual(protected["command"], proof["commands"]["suite"])
        replay = final["validation"]["check_replay"]["protected_tests"]
        self.assertEqual(("PASS", [f"{GREET}/test_greet.py"]), (replay["verdict"], replay["changed_tests"]))

    def outside_finding(self, final, result):
        """The completion gate refused the Completion Reviewer's COMPLETE on the runner's one open package finding."""
        self.assertNotEqual("TASK_COMPLETE", final["status"], result.stdout + result.stderr)
        rows = [row for row in final["findings_ledger"] if row.get("package") == GREET]
        self.assertEqual(1, len(rows), final["findings_ledger"])
        row = rows[0]
        self.assertEqual(("runner", "open", True), (row["source"], row["status"], row["blocking"]))
        self.assertIn(
            "Completion rejected: the findings ledger still lists open blocking findings", final["stop_reason"]
        )
        self.assertIn(row["id"], final["stop_reason"])
        self.assertIn("Files outside the package packages/greet/ changed", final["stop_reason"])
        return row

    def test_a_builder_write_to_the_other_package_keeps_the_run_from_completing(self):
        self.builder_writes({**FIX, f"{OTHER}/other.py": "def answer():\n    return 41 + 1\n"})
        _, final, result = self.run_to_end(2)
        row = self.outside_finding(final, result)
        self.assertEqual({f"{OTHER}/other.py": "modified"}, row["paths"])
        # The proof still says its suite never ran the other package.
        proof = final["regression_proof"]
        self.assertEqual("UNVERIFIED", proof["verdict"])
        self.assertIn(
            "Files outside the package packages/greet/ changed, and its suite does not cover them: "
            "packages/other/other.py",
            proof["unverified"],
        )

    def test_a_run_that_owes_no_proof_reports_each_change_outside_the_package(self):
        # A build whose criteria name no test owes no regression proof, so nothing else judges its diff.
        self.env["AUTOCODE_FIXTURE_TASK_KIND"] = "build"
        self.builder_writes(
            {
                **FIX,
                f"{OTHER}/other.py": "def answer():\n    return 41 + 1\n",
                f"{OTHER}/notes.md": "A file the other package did not have\n",
                "README.md": "# Two packages, edited from the greeting package's task\n",
            }
        )
        run, final, result = self.run_to_end(2)
        self.assertIsNone(final.get("regression_proof"))
        row = self.outside_finding(final, result)
        expected = {"README.md": "modified", f"{OTHER}/notes.md": "added", f"{OTHER}/other.py": "modified"}
        self.assertEqual(expected, row["paths"])
        for path in expected:
            self.assertIn(path, row["finding"])
        # Closing the finding accepts nothing: --accept-completion checks again and refuses.
        self.launch(["--run-dir", str(run), "--close-finding", row["id"], "--close-reason", "the edits are fine"], 0)
        refused = self.launch(["--run-dir", str(run), "--accept-completion"], 2)
        self.assertIn("Completion acceptance requires no change outside the package", refused.stderr)
        self.assertIn("README.md (modified)", refused.stderr)
        self.assertNotEqual("TASK_COMPLETE", self.saved()[1]["status"])

    def test_a_nested_repository_the_checkout_held_at_launch_is_not_the_builders_change(self):
        # An in-place run's base cannot hold an untracked Git repository, so it is recorded at launch.
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
        _, final, result = self.run_to_end(0)
        self.assertEqual("TASK_COMPLETE", final["status"], result.stdout + result.stderr)
        self.assertEqual([], [row for row in final.get("findings_ledger") or [] if row.get("source") == "runner"])
        self.assertEqual("vendored\n", (nested / "x.txt").read_text())

    def test_a_run_that_owes_no_proof_completes_when_every_change_is_in_the_package(self):
        self.env["AUTOCODE_FIXTURE_TASK_KIND"] = "build"
        self.builder_writes(FIX)
        _, final, result = self.run_to_end(0)
        self.assertEqual("TASK_COMPLETE", final["status"], result.stdout + result.stderr)
        self.assertIsNone(final.get("regression_proof"))
        self.assertEqual([], [row for row in final.get("findings_ledger") or [] if row.get("source") == "runner"])

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


if __name__ == "__main__":
    unittest.main()
