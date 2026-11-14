"""A Perl distribution runs the ``.t`` files of its ``t/`` directory with prove (autocode_toolchain_suite.perl_tests).

A real Perl bug fix (Claude models, 2026-11-14) delivered a correct fix and a correct test in ``t/pager.t``,
and the proof failed twice: no suite was detected and ``t/pager.t`` was not a test file by its name, so
"No regression test was added or changed" and "No project test command was found". The Resolver then asked
the person to "correct the saved verification commands". The detection cases are pure. The proof cases run
a real regression proof with Perl's own ``prove`` in a Git project, with no provider.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_command_guards as command_guards
import autocode_toolchain_suite as toolchain_suite
import autocode_verification_plan as verification_plan
import autocode_verify as verify

CPANFILE = "requires 'perl', '5.010';\n\non 'test' => sub {\n    requires 'Test::More', '0.98';\n};\n"
PAGER = (
    "package Acme::Pager;\nuse strict;\nuse warnings;\nuse Exporter 'import';\nour @EXPORT_OK = qw(page_count);\n\n"
    "sub page_count {\n    my ($items, $per_page) = @_;\n    return int($items / $per_page) + 1;\n}\n\n1;\n"
)
FIXED = PAGER.replace("int($items / $per_page) + 1", "int(($items + $per_page - 1) / $per_page)")


def test_file(*cases):
    checks = "".join(f"is(page_count({items}, {per}), {pages}, '{name}');\n" for name, pages, items, per in cases)
    return f"use strict;\nuse warnings;\nuse Test::More;\nuse Acme::Pager qw(page_count);\n\n{checks}\ndone_testing;\n"


PARTIAL = ("a partial page counts as a page", 3, 21, 10)
EXISTING = test_file(PARTIAL)
REGRESSION = test_file(PARTIAL, ("an exact multiple needs no extra page", 2, 20, 10))
# A new check the original code already satisfies: it cannot show that anything was fixed.
VACUOUS = test_file(PARTIAL, ("fewer items than a page is one page", 1, 4, 10))
SEED = {"cpanfile": CPANFILE, "lib/Acme/Pager.pm": PAGER, "t/pager.t": EXISTING}


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def detect(files):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text)
        return toolchain_suite.detect(root, sorted(files), verify.is_test_path)


class DetectionTests(unittest.TestCase):
    def test_a_perl_distribution_runs_the_tests_of_its_t_directory_with_prove(self):
        framework = detect(SEED)
        self.assertEqual(("prove", "prove -lr t"), (framework.name, framework.suite))
        self.assertFalse(framework.per_test)  # judged by the suite's exit code, as PHPUnit is
        self.assertIsNone(framework.targeted(["t/pager.t"]))

    def test_a_root_file_or_a_module_under_lib_marks_the_distribution(self):
        for files in (
            {"cpanfile": CPANFILE, "t/pager.t": EXISTING},
            {"Makefile.PL": "use ExtUtils::MakeMaker;\nWriteMakefile(NAME => 'Acme::Pager');\n", "t/pager.t": EXISTING},
            {"Build.PL": "use Module::Build;\n", "t/unit/pager.t": EXISTING},
            {"lib/Acme/Pager.pm": PAGER, "t/pager.t": EXISTING},
        ):
            with self.subTest(files=sorted(files)):
                self.assertEqual("prove", detect(files).name)

    def test_a_project_that_is_not_a_pure_perl_distribution_with_tests_has_no_perl_suite(self):
        for files in (
            {"cpanfile": CPANFILE, "lib/Acme/Pager.pm": PAGER},  # no tests yet
            {"t/pager.t": EXISTING, "README.md": "# tests of something else\n"},  # nothing says Perl
            {"cpanfile": CPANFILE, "tests/pager.t": EXISTING},  # prove is not given this directory
            {"cpanfile": CPANFILE, "t/lib/Helper.pm": "package Helper;\n1;\n"},  # a helper is not a test
            {**SEED, "Pager.xs": "#include <EXTERN.h>\n"},  # C code must be built first
            {"api/cpanfile": CPANFILE, "api/lib/Pager.pm": PAGER, "api/t/pager.t": EXISTING},  # not at this root
        ):
            with self.subTest(files=sorted(files)):
                self.assertIsNone(detect(files))

    def test_a_suite_the_project_declares_another_way_keeps_priority(self):
        self.assertEqual("make", detect({**SEED, "Makefile": "test:\n\tprove -l t\n"}).name)

    def test_perl_files_are_code_and_the_t_directory_holds_tests(self):
        for path in ("t/pager.t", "t/unit/00-load.t", "t/lib/Helper.pm", "xt/author/pod.t"):
            with self.subTest(path=path):
                self.assertTrue(verify.is_test_path(path))
        for path in ("lib/Acme/Pager.pm", "bin/report.pl", "lib/Acme/t/Pager.pm", "format.t.md"):
            with self.subTest(path=path):
                self.assertFalse(verify.is_test_path(path))
        for path in ("lib/Acme/Pager.pm", "bin/report.pl", "t/pager.t"):
            with self.subTest(path=path):
                self.assertTrue(verify.is_code_path(path))

    def test_a_stray_python_helper_does_not_hide_prove(self):
        self.assertTrue(toolchain_suite.python_is_secondary(["cpanfile", "scripts/release.py"]))
        self.assertFalse(toolchain_suite.python_is_secondary(["cpanfile", "pyproject.toml", "pkg/core.py"]))

    def test_a_refactor_is_guarded_by_proves_own_command(self):
        framework = detect(SEED)
        guard = {"id": "AC2", "command": "prove -lr t"}
        self.assertEqual([guard], command_guards.usable([guard], framework, ["t/pager.t"]))
        self.assertIsNone(command_guards.problem([guard], framework, []))

    def test_a_plans_prove_command_is_a_command_the_runner_replays(self):
        for command in ("prove -l t/pager.t", "prove -lr t", "perl -Ilib t/pager.t"):
            with self.subTest(command=command):
                self.assertEqual([command], verification_plan.commands(f"`{command}`"))


@unittest.skipUnless(shutil.which("prove"), "runs the project's tests with Perl's prove")
class ProofTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="perl-suite-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "project"
        self.evidence = Path(self.temp.name) / "evidence"
        self.write(SEED)
        git(self.root, "init", "-q")
        git(self.root, "config", "maintenance.auto", "false")  # see tests.test_verify.Project
        git(self.root, "config", "gc.auto", "0")
        git(self.root, "add", "-A")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "seed")
        self.base = git(self.root, "rev-parse", "HEAD")

    def write(self, files):
        for name, text in files.items():
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text(text)

    def verify(self):
        framework = verify.detect_framework(self.root)
        self.assertEqual("prove", framework.name)
        common = {"framework": framework, "timeout": 300}
        base_suite = verify.baseline(self.root, self.base, self.evidence, suite_command=framework.suite, **common)
        return verify.verify(self.root, self.base, self.evidence, base_suite=base_suite, **common)

    def test_a_fix_with_its_regression_test_is_proved_without_a_command_from_the_person(self):
        self.write({"lib/Acme/Pager.pm": FIXED, "t/pager.t": REGRESSION})
        result = self.verify()
        self.assertEqual("PASS", result["verdict"], result)
        self.assertEqual([], result["unverified"], result)
        self.assertEqual("detected:prove", result["commands"]["suite_source"])
        self.assertEqual("prove -lr t", result["commands"]["suite"])
        self.assertEqual(["t/pager.t"], result["test_files"])
        # The new check ran against the original code and failed there.
        self.assertNotEqual(0, result["checks"]["regression_on_base"]["exit_code"], result["checks"])

    def test_a_new_test_file_in_a_directory_under_t_is_part_of_the_suite(self):
        self.write({"lib/Acme/Pager.pm": FIXED, "t/regression/exact_multiple.t": REGRESSION})
        result = self.verify()
        self.assertEqual("PASS", result["verdict"], result)
        self.assertEqual(["t/regression/exact_multiple.t"], result["test_files"])

    def test_a_new_test_the_original_code_already_passes_proves_nothing(self):
        self.write({"lib/Acme/Pager.pm": FIXED, "t/pager.t": VACUOUS})
        result = self.verify()
        self.assertEqual("FAIL", result["verdict"], result)
        self.assertIn("pass on the unfixed base code", " ".join(result["failures"]))

    def test_a_change_that_leaves_its_own_test_failing_is_refused(self):
        self.write({"t/pager.t": REGRESSION})
        self.write({"lib/Acme/Pager.pm": PAGER.replace("+ 1", "+ 1 + 0")})
        result = self.verify()
        self.assertEqual("FAIL", result["verdict"], result)


if __name__ == "__main__":
    unittest.main()
