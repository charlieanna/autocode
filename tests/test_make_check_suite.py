"""A Makefile's ``check`` target runs the project's tests, as ``test`` does (autocode_toolchain_suite.make_target).

A real C bug fix (Claude models, 2026-11-14) in a project whose tests run with ``make check`` delivered a
correct fix and a correct test, and the proof ended ``UNVERIFIED`` ("No command to run the regression tests;
pass --regression-command"): only a ``test`` target was detected, and the approved plan's own command
(``make check``) names no test file, so it could not be adopted. The Resolver then asked the person to set
both commands. The detection cases are pure; the proof cases run ``make`` over a shell test, with no provider.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import autocode_command_guards as command_guards
import autocode_toolchain_suite as toolchain_suite
import autocode_verify as verify

from .test_verify import Project

PAGER = "page_count() { echo $(( $1 / $2 )); }\n"
FIXED = "page_count() { echo $(( ($1 + $2 - 1) / $2 )); }\n"
EXISTING = '. ./pager.sh\n[ "$(page_count 10 5)" = 2 ] || { echo "exact multiple"; exit 1; }\n'
REGRESSION = EXISTING + '[ "$(page_count 11 5)" = 3 ] || { echo "partial last page"; exit 1; }\n'
# A new check the original code already satisfies: it cannot show that anything was fixed.
VACUOUS = EXISTING + '[ "$(page_count 5 5)" = 1 ] || { echo "one page"; exit 1; }\n'
MAKEFILE = "all:\n\t@true\n\ncheck: all\n\tsh tests/test_pager.sh\n\n.PHONY: all check\n"
SEED = {"Makefile": MAKEFILE, "pager.sh": PAGER, "tests/test_pager.sh": EXISTING}


def detect(files):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text)
        return toolchain_suite.detect(root, sorted(files), verify.is_test_path)


class DetectionTests(unittest.TestCase):
    def test_a_check_target_runs_the_projects_tests(self):
        framework = detect(SEED)
        self.assertEqual(("make", "make check"), (framework.name, framework.suite))
        self.assertFalse(framework.per_test)
        self.assertIsNone(framework.targeted(["tests/test_pager.sh"]))

    def test_a_test_target_comes_first(self):
        both = "check: lint test\n\nlint:\n\t@true\n\ntest:\n\tsh tests/test_pager.sh\n"
        self.assertEqual("make test", detect({**SEED, "Makefile": both}).suite)
        self.assertEqual("make test", detect({**SEED, "Makefile": "test:\n\tsh tests/test_pager.sh\n"}).suite)

    def test_only_a_rule_of_that_name_is_a_test_target(self):
        for makefile in (
            "all:\n\t@true\n",
            "check := yes\nall:\n\t@true\n",
            "checks:\n\tsh tests/test_pager.sh\n",
            "distcheck:\n\tsh tests/test_pager.sh\n",
            "all:\n\t@echo run make check\n",
        ):
            with self.subTest(makefile=makefile):
                self.assertIsNone(detect({**SEED, "Makefile": makefile}))
        for makefile in ("check :\n\tsh tests/test_pager.sh\n", "check::\n\tsh tests/test_pager.sh\n"):
            with self.subTest(makefile=makefile):
                self.assertEqual("make check", detect({**SEED, "Makefile": makefile}).suite)

    def test_a_refactor_is_guarded_by_the_check_target(self):
        framework = detect(SEED)
        guard = {"id": "AC2", "command": "make check"}
        self.assertEqual([guard], command_guards.usable([guard], framework, ["tests/test_pager.sh"]))
        self.assertIsNone(command_guards.problem([guard], framework, []))


@unittest.skipUnless(shutil.which("make"), "the project's suite is make check")
class ProofTests(unittest.TestCase):
    def setUp(self):
        self.project = Project(SEED)
        self.addCleanup(self.project.close)

    def test_a_fix_with_its_regression_test_is_proved_without_a_command_from_the_person(self):
        self.project.write({"pager.sh": FIXED, "tests/test_pager.sh": REGRESSION})
        result = self.project.verify()
        self.assertEqual("PASS", result["verdict"], result)
        self.assertEqual([], result["unverified"], result)
        self.assertEqual(("make check", "detected:make"), tuple(result["commands"].values())[:2])
        self.assertEqual(["tests/test_pager.sh"], result["test_files"])
        # The new check ran against the original code and failed there.
        self.assertNotEqual(0, result["checks"]["regression_on_base"]["exit_code"], result["checks"])

    def test_a_new_test_the_original_code_already_passes_proves_nothing(self):
        self.project.write({"pager.sh": FIXED, "tests/test_pager.sh": VACUOUS})
        result = self.project.verify()
        self.assertEqual("FAIL", result["verdict"], result)
        self.assertIn("pass on the unfixed base code", " ".join(result["failures"]))


if __name__ == "__main__":
    unittest.main()
