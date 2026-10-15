"""A bug fix in one package of a two-package repository, through the public CLI (#711).

Every stage runs on the offline fixture provider. The run is started with --package: its suite is
detected in that package and runs from its directory, only that package's tests are protected, and a
Builder write to the other package keeps the run from completing.
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

    def test_a_builder_write_to_the_other_package_keeps_the_run_from_completing(self):
        self.builder_writes({**FIX, f"{OTHER}/other.py": "def answer():\n    return 41 + 1\n"})
        _, final, result = self.run_to_end(2)
        self.assertNotEqual("TASK_COMPLETE", final["status"], result.stdout)
        proof = final["regression_proof"]
        self.assertEqual("UNVERIFIED", proof["verdict"])
        self.assertIn(
            "Files outside the package packages/greet/ changed, and its suite does not cover them: "
            "packages/other/other.py",
            proof["unverified"],
        )
        self.assertIn("no passing regression proof", final["stop_reason"])

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


if __name__ == "__main__":
    unittest.main()
