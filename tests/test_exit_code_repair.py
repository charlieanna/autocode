"""A repair is shown by the project's own failing suite where no runner names a test.

"The test 'counts a partial last page' fails. Fix src/pager.js so that it passes. Do not change the tests", in
a Jest project (Claude models, 2026-11-13): the plan changed ``src/pager.js`` alone, the proof failed twice
with "No regression test was added or changed", and the Resolver asked the person to type a regression
command. The same request in a Python project completes: the runner names each test, so the test that fails on
the original code and passes with the change is the proof (first_suite.existing_flip). A suite that reports
only an exit code shows the same thing as a whole: it fails on the original code and passes with the change,
and the change is code alone, with the suite's own files as they were.

The cases run real commands in a Ruby project, with Minitest's detected suite and with a test script only the
plan names, in a Perl distribution with prove, in a PHPUnit project and in Maven and Gradle projects (their commands
stand-ins) and in a shell and a Makefile project; no provider runs.
tools/autocode_first_suite.py
"""

from __future__ import annotations

import shutil
import sys
import unittest
from unittest import mock

import autocode_first_suite as first_suite
import autocode_launch_inputs as launch_inputs
import autocode_regression as regression
import autocode_util as util
import autocode_verify as verify
from autocode_test_framework import Framework

from . import test_perl_suite as perl
from . import test_phpunit_suite as php
from . import test_verify
from .test_verify import Project

FIXED = "module Pager\n  def self.page_count(total, size)\n    (total + size - 1) / size\n  end\nend\n"
# Rounds down: eleven items in pages of five is three pages, and this says two.
BROKEN = FIXED.replace("(total + size - 1) / size", "total / size")
STILL_BROKEN = BROKEN.replace("total / size", "total.div(size)")
MINITEST = (
    'require "minitest/autorun"\nrequire_relative "../lib/pager"\n\nclass PagerTest < Minitest::Test\n'
    "  def test_exact_multiple\n    assert_equal 2, Pager.page_count(10, 5)\n  end\n\n"
    "  def test_partial_last_page\n    assert_equal 3, Pager.page_count(11, 5)\n  end\nend\n"
)
# A plain script under tests/: no runner AutoCode detects runs it, so the approved plan's command does.
SCRIPT = (
    'require_relative "../lib/pager"\n'
    'abort "exact multiple" unless Pager.page_count(10, 5) == 2\n'
    'abort "partial last page" unless Pager.page_count(11, 5) == 3\n'
)
NARROWED = SCRIPT.splitlines(keepends=True)[0] + SCRIPT.splitlines(keepends=True)[1]
RUN_SCRIPT = "`ruby tests/pager_check.rb` fails on the original code and passes with the fix"
SH_FIXED = "page_count() { echo $(( ($1 + $2 - 1) / $2 )); }\n"
SH_BROKEN = SH_FIXED.replace("($1 + $2 - 1) / $2", "$1 / $2")
SH_CHECK = '. ./pager.sh\n[ "$(page_count 11 5)" = 3 ] || { echo "partial last page"; exit 1; }\n'
NO_TEST = "No regression test was added or changed"
SHOWN = "fails on the original code (exit 1) and passes with the change, so the tests the project already has show"


def contract(*methods, kind="bugfix"):
    rows = [{"id": f"AC{n}", "criterion": "pages", "verification_method": m} for n, m in enumerate(methods, 1)]
    sealed = {"task_id": "task-1", "revision": 1, "body": {"task_kind": kind, "acceptance_criteria": rows}}
    return {**sealed, "hash": util.digest(sealed), "approval_status": "approved"}


class Proving(unittest.TestCase):
    seed: dict = {}
    installed = False  # whether the project has ignored packages the proof's copies need

    def setUp(self):
        self.project = Project(self.seed)
        self.addCleanup(self.project.close)

    def prove(self, goal_contract, files):
        self.project.write(files)
        state = {
            "workspace": str(self.project.root),
            "base_commit": self.project.base,
            "iteration": 1,
            "stages": [],
            "history": [],
            "settings": {"regression": {}},
            "goal_contract": goal_contract,
        }
        run_dir = self.project.root.parent / "run"
        if self.installed:
            run_dir.mkdir()
            launch_inputs.record(state, self.project.root, run_dir)  # as a run does when it starts
        with mock.patch.object(verify.diff_coverage, "collect", return_value=None):
            return regression.prove(state, self.project.root, run_dir)

    def shown(self, proof):
        return any(SHOWN in note for note in proof["notes"])


class RuleTests(unittest.TestCase):
    def test_only_a_suite_whose_inputs_are_its_test_files_shows_a_repair_by_its_exit_code(self):
        suite = "npm test --silent"
        for framework, command, adopted, eligible in (
            (Framework("jest", suite), suite, None, True),
            (Framework("mocha", "npx --no-install mocha"), "npx --no-install mocha", None, True),
            (Framework("minitest", "ruby -e 1"), "ruby -e 1", None, True),
            (Framework("phpunit", "vendor/bin/phpunit"), "vendor/bin/phpunit", None, True),
            (Framework("prove", "prove -lr t"), "prove -lr t", None, True),
            (Framework("maven", "mvn -B -q test"), "mvn -B -q test", None, True),
            (Framework("gradle", "./gradlew --no-daemon -q test"), "./gradlew --no-daemon -q test", None, True),
            (Framework("cmake", "ctest"), "ctest", None, False),
            (None, "ruby tests/pager_check.rb", "ruby tests/pager_check.rb", True),
            # A command the person typed, a recipe and a package script run whatever they say.
            (None, "sh run.sh", None, False),
            (Framework("jest", suite), "sh run.sh", None, False),
            (Framework("make", "make test"), "make test", None, False),
            (Framework("npm", suite), suite, None, False),
            (Framework("pytest", "python -m pytest"), "python -m pytest", None, False),
            (None, None, None, False),
        ):
            with self.subTest(framework=framework and framework.name, command=command):
                self.assertEqual(eligible, first_suite.exit_repair_suite(framework, command, adopted))

    def test_the_files_that_say_what_phpunit_and_prove_run_are_held_to_the_original(self):
        # They are the suite's own files, as jest.config.js is: a change to one is a change to what runs.
        for path in ("phpunit.xml", "phpunit.xml.dist", "api/phpunit.dist.xml", ".proverc"):
            with self.subTest(path=path):
                self.assertTrue(verify._definition_file(path))
        for path in ("composer.json", "cpanfile", "src/phpunit.xml.md"):
            with self.subTest(path=path):
                self.assertFalse(verify._definition_file(path))

    def test_a_suite_fails_as_a_whole_when_it_completed_nonzero_without_naming_a_test(self):
        ran = lambda health, command="make test": {"command": command, "health": health}  # noqa: E731
        self.assertTrue(first_suite.repairable_exit(ran("failing"), "make test"))
        for base_suite in (ran("passing"), ran("failing_tests"), ran("broken"), ran("timeout"), None):
            self.assertFalse(first_suite.repairable_exit(base_suite, "make test"))
        self.assertFalse(first_suite.repairable_exit(ran("failing", "sh other.sh"), "make test"))
        self.assertFalse(first_suite.repairable_exit(ran("failing"), None))


@unittest.skipUnless(shutil.which("ruby"), "the project's tests are Ruby scripts")
class PlanCommandTests(Proving):
    """No runner is detected, so the approved plan's command is the suite (autocode_plan_proof_command)."""

    seed = {"lib/pager.rb": BROKEN, "tests/pager_check.rb": SCRIPT}

    def test_the_failing_suite_that_passes_with_the_change_shows_the_repair(self):
        self.assertIsNone(verify.detect_framework(self.project.root))
        proof = self.prove(contract(RUN_SCRIPT), {"lib/pager.rb": FIXED})
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual((["lib/pager.rb"], []), (proof["source_files"], proof["test_files"]))
        self.assertEqual("ruby tests/pager_check.rb", proof["commands"]["suite"])
        self.assertEqual(0, proof["checks"]["suite_on_candidate"]["exit_code"])
        self.assertTrue(self.shown(proof), proof["notes"])
        self.assertIn("its suite `ruby tests/pager_check.rb` fails", " ".join(proof["notes"]))
        self.assertIn(
            "the repair rests on the whole suite's exit code on the original code and with the change",
            proof["review_reasons"],
        )

    def test_a_change_that_leaves_the_suite_failing_shows_no_repair(self):
        proof = self.prove(contract(RUN_SCRIPT), {"lib/pager.rb": STILL_BROKEN})
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertIn(
            f"{NO_TEST}, and the suite that fails on the original code is not shown to pass with the change",
            " ".join(proof["failures"]),
        )
        self.assertFalse(self.shown(proof), proof["notes"])

    def test_a_change_to_the_tests_is_not_this_repair(self):
        # The candidate drops the failing check and leaves the product wrong: the ordinary proof judges it.
        proof = self.prove(contract(RUN_SCRIPT), {"lib/pager.rb": STILL_BROKEN, "tests/pager_check.rb": NARROWED})
        self.assertNotEqual(verify.PASS, proof["verdict"], proof)
        self.assertFalse(self.shown(proof), proof["notes"])

    def test_a_change_to_more_than_code_is_not_this_repair(self):
        # A file that is not code may be one the suite reads; the ordinary judgement stands.
        proof = self.prove(contract(RUN_SCRIPT), {"lib/pager.rb": FIXED, "Rakefile": "task :default\n"})
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertIn(f"{NO_TEST}, so the bug is not shown to be reproduced", " ".join(proof["failures"]))

    def test_a_suite_that_passes_on_the_original_code_shows_no_repair(self):
        working = Project({"lib/pager.rb": FIXED, "tests/pager_check.rb": SCRIPT})
        self.addCleanup(working.close)
        self.project = working
        proof = self.prove(contract(RUN_SCRIPT), {"lib/pager.rb": FIXED.replace("- 1)", "- 1 )")})
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertIn(f"{NO_TEST}, so the bug is not shown to be reproduced", " ".join(proof["failures"]))


@unittest.skipUnless(shutil.which("ruby"), "the project's suite is its Minitest files")
class MinitestTests(Proving):
    seed = {"lib/pager.rb": BROKEN, "test/pager_test.rb": MINITEST}

    def test_the_detected_suite_shows_the_repair(self):
        framework = verify.detect_framework(self.project.root)
        self.assertEqual("minitest", framework.name)
        criterion = "`ruby test/pager_test.rb` fails on the original code and passes with the fix"
        proof = self.prove(contract(criterion), {"lib/pager.rb": FIXED})
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual((framework.suite, "detected:minitest"), tuple(proof["commands"].values())[:2])
        self.assertIn(
            "Existing tests that fail on the original code pass with the change: "
            "test/pager_test.rb::PagerTest::test_partial_last_page",
            proof["notes"],
        )


@unittest.skipUnless(shutil.which("prove"), "the project's suite is its .t files, run with Perl's prove")
class PerlTests(Proving):
    """ "t/pager.t fails. Fix the module", in a Perl distribution: the test that shows the bug is already there."""

    seed = {"cpanfile": perl.CPANFILE, "lib/Acme/Pager.pm": perl.PAGER, "t/pager.t": perl.REGRESSION}
    criterion = "`prove -l t/pager.t` fails on the original code and passes with the fix"

    def test_the_detected_suite_shows_the_repair(self):
        proof = self.prove(contract(self.criterion), {"lib/Acme/Pager.pm": perl.FIXED})
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual(("prove -lr t", "detected:prove"), tuple(proof["commands"].values())[:2])
        self.assertEqual((["lib/Acme/Pager.pm"], []), (proof["source_files"], proof["test_files"]))
        self.assertTrue(self.shown(proof), proof["notes"])

    def test_a_change_that_leaves_the_suite_failing_shows_no_repair(self):
        proof = self.prove(contract(self.criterion), {"lib/Acme/Pager.pm": perl.PAGER.replace("+ 1", "+ 1 + 0")})
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertFalse(self.shown(proof), proof["notes"])

    def test_options_that_make_prove_run_nothing_are_not_this_repair(self):
        # `prove -D` is a dry run: it lists the test files and exits 0. A .proverc is not code.
        proof = self.prove(
            contract(self.criterion), {"lib/Acme/Pager.pm": perl.PAGER.replace("+ 1", "+ 1 + 0"), ".proverc": "-D\n"}
        )
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertIn(f"{NO_TEST}, so the bug is not shown to be reproduced", " ".join(proof["failures"]))
        self.assertFalse(self.shown(proof), proof["notes"])


class PhpunitTests(Proving):
    """The same request in a Composer project: PHPUnit's suite, judged by its exit code."""

    seed = {**php.SEED, "tests/PagerTest.php": php.REGRESSION}
    installed = True
    criterion = "`vendor/bin/phpunit` fails on the original code and passes with the fix"

    def setUp(self):
        super().setUp()
        # `composer install` has run, as in a person's project: vendor/ is there and ignored.
        self.project.write({"vendor/bin/phpunit": php.STAND_IN, "vendor/autoload.php": "<?php\n"})
        (self.project.root / "vendor/bin/phpunit").chmod(0o755)

    def test_the_detected_suite_shows_the_repair(self):
        proof = self.prove(contract(self.criterion), {"src/Pager.php": php.FIXED})
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual(("vendor/bin/phpunit", "detected:phpunit"), tuple(proof["commands"].values())[:2])
        self.assertEqual((["src/Pager.php"], []), (proof["source_files"], proof["test_files"]))
        self.assertTrue(self.shown(proof), proof["notes"])

    def test_a_change_that_leaves_the_suite_failing_shows_no_repair(self):
        proof = self.prove(contract(self.criterion), {"src/Pager.php": php.PAGER.replace("+ 1", "+ 1 + 0")})
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertFalse(self.shown(proof), proof["notes"])

    def test_a_change_to_phpunits_configuration_is_not_this_repair(self):
        narrowed = php.CONFIG.replace("<directory>tests</directory>", "<directory>tests/unit</directory>")
        proof = self.prove(contract(self.criterion), {"src/Pager.php": php.FIXED, "phpunit.xml": narrowed})
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertIn(f"{NO_TEST}, so the bug is not shown to be reproduced", " ".join(proof["failures"]))
        self.assertFalse(self.shown(proof), proof["notes"])


JAVA = (
    "package example;\n\npublic final class Pager {\n    public static int pageCount(int total, int size) {\n"
    "        return total / size;\n    }\n}\n"
)
JAVA_FIXED = JAVA.replace("total / size", "(total + size - 1) / size")
JAVA_TEST = (
    "package example;\n\nimport static org.junit.jupiter.api.Assertions.assertEquals;\n\n"
    "import org.junit.jupiter.api.Test;\n\nclass PagerTest {\n"
    "    @Test\n    void exactMultiple() {\n        assertEquals(2, Pager.pageCount(10, 5));\n    }\n\n"
    "    @Test\n    void partialLastPage() {\n        assertEquals(3, Pager.pageCount(11, 5));\n    }\n}\n"
)
# What a project's Maven or Gradle wrapper does, reduced to this project: it evaluates the one return
# statement of Pager.java for each assertEquals of the test classes and exits 1 when one fails. As Gradle
# does, it runs no test when the build file disables the test task.
WRAPPER = (
    f"#!{sys.executable}\n"
    """import pathlib, re, sys

build = pathlib.Path("build.gradle.kts")
if build.is_file() and "enabled = false" in build.read_text():
    sys.exit(0)
source = pathlib.Path("src/main/java/example/Pager.java").read_text()
formula = re.search(r"return (.+);", source).group(1).replace("/", "//")
failed = 0
for path in sorted(pathlib.Path("src/test/java").rglob("*Test.java")):
    for pages, total, size in re.findall(r"assertEquals\\((\\d+), Pager\\.pageCount\\((\\d+), (\\d+)\\)\\)", path.read_text()):
        failed += eval(formula, {"total": int(total), "size": int(size)}) != int(pages)
print("BUILD FAILURE" if failed else "BUILD SUCCESS")
sys.exit(1 if failed else 0)
"""
)
JAVA_SEED = {"src/main/java/example/Pager.java": JAVA, "src/test/java/example/PagerTest.java": JAVA_TEST}
GRADLE_BUILD = "plugins {\n    java\n}\n\ntasks.test {\n    useJUnitPlatform()\n}\n"


class BuildTool(Proving):
    """ "PagerTest.partialLastPage fails. Fix Pager.java", in a Maven or Gradle project: the build tool reports
    an exit code, and what it runs is what its build files say, which the proof holds to the original."""

    main = "src/main/java/example/Pager.java"
    wrapper = ""
    unfixed = JAVA.replace("total / size", "total / size + 0")

    def setUp(self):
        super().setUp()
        root = self.project.root
        (root / self.wrapper).chmod(0o755)  # the project's wrapper is committed executable
        test_verify.git(root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qam", "wrapper")
        self.project.base = test_verify.git(root, "rev-parse", "HEAD")


class MavenTests(BuildTool):
    seed = {**JAVA_SEED, "pom.xml": "<project/>\n", "mvnw": WRAPPER}
    wrapper = "mvnw"
    criterion = "`./mvnw -B -q test` fails on the original code and passes with the fix"

    def test_the_detected_suite_shows_the_repair(self):
        proof = self.prove(contract(self.criterion), {self.main: JAVA_FIXED})
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual(("./mvnw -B -q test", "detected:maven"), tuple(proof["commands"].values())[:2])
        self.assertEqual(([self.main], []), (proof["source_files"], proof["test_files"]))
        self.assertTrue(self.shown(proof), proof["notes"])

    def test_a_change_that_leaves_the_suite_failing_shows_no_repair(self):
        proof = self.prove(contract(self.criterion), {self.main: self.unfixed})
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertFalse(self.shown(proof), proof["notes"])

    def test_a_change_to_the_build_file_is_not_this_repair(self):
        proof = self.prove(contract(self.criterion), {self.main: JAVA_FIXED, "pom.xml": "<project><x/></project>\n"})
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertIn(f"{NO_TEST}, so the bug is not shown to be reproduced", " ".join(proof["failures"]))
        self.assertFalse(self.shown(proof), proof["notes"])


class GradleTests(BuildTool):
    seed = {**JAVA_SEED, "build.gradle.kts": GRADLE_BUILD, "gradlew": WRAPPER}
    wrapper = "gradlew"
    criterion = "`./gradlew test` fails on the original code and passes with the fix"

    def test_the_detected_suite_shows_the_repair(self):
        proof = self.prove(contract(self.criterion), {self.main: JAVA_FIXED})
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual("detected:gradle", proof["commands"]["suite_source"])
        self.assertTrue(self.shown(proof), proof["notes"])

    def test_a_build_file_that_stops_the_tests_running_shows_no_repair(self):
        # build.gradle.kts is Kotlin, so the change is "code alone", and the suite passes with it because it
        # ran nothing. The original build file over the new code still fails.
        disabled = GRADLE_BUILD.replace("useJUnitPlatform()", "enabled = false")
        proof = self.prove(contract(self.criterion), {self.main: self.unfixed, "build.gradle.kts": disabled})
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertIn("the suite that fails on the original code is not shown to pass", proof["failures"][0])
        self.assertEqual(1, proof["checks"]["suite_base_definition_on_candidate"]["exit_code"])
        self.assertFalse(self.shown(proof), proof["notes"])


class ShellTests(Proving):
    """A shell test sources the code under test, as a JavaScript test imports it (definition_scan.sourced_product)."""

    seed = {"pager.sh": SH_BROKEN, "tests/test_pager.sh": SH_CHECK}
    criterion = "`sh tests/test_pager.sh` fails on the original code and passes with the fix"

    def test_the_file_a_shell_test_sources_is_the_code_under_test(self):
        proof = self.prove(contract(self.criterion), {"pager.sh": SH_FIXED})
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual((["pager.sh"], []), (proof["source_files"], proof["test_files"]))
        self.assertTrue(self.shown(proof), proof["notes"])
        # The original test ran over the changed pager.sh: the suite's own files are the original ones.
        self.assertEqual(0, proof["checks"]["suite_base_definition_on_candidate"]["exit_code"])

    def test_a_script_the_test_runs_is_one_of_the_suites_own_files(self):
        # The test runs checks.sh, which holds the failing check. A change that drops the check from it and
        # leaves pager.sh wrong makes the suite pass by running less: the original checks.sh still fails.
        checks = '. ./pager.sh\n[ "$(page_count 10 5)" = 2 ] || exit 1\n[ "$(page_count 11 5)" = 3 ] || exit 1\n'
        ran = Project({"pager.sh": SH_BROKEN, "checks.sh": checks, "tests/test_pager.sh": "sh ./checks.sh\n"})
        self.addCleanup(ran.close)
        self.project = ran
        proof = self.prove(contract(self.criterion), {"checks.sh": "".join(checks.splitlines(keepends=True)[:2])})
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertIn(f"{NO_TEST}, and the suite that fails on the original code is not shown", proof["failures"][0])
        self.assertFalse(self.shown(proof), proof["notes"])


class OtherSuiteTests(Proving):
    """Suites whose inputs AutoCode cannot hold to the original: the missing-test judgement stands."""

    seed = {"pager.sh": SH_BROKEN, "tests/test_pager.sh": SH_CHECK}

    @unittest.skipUnless(shutil.which("make"), "the project's suite is make test")
    def test_a_recipe_can_run_anything_so_make_test_shows_no_repair(self):
        made = Project({**self.seed, "Makefile": "test:\n\tsh tests/test_pager.sh\n\n.PHONY: test\n"})
        self.addCleanup(made.close)
        self.project = made
        self.assertEqual("make test", verify.detect_framework(made.root).suite)
        proof = self.prove(
            contract("`make test` fails on the original code and passes with the fix"), {"pager.sh": SH_FIXED}
        )
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertIn(f"{NO_TEST}, so the bug is not shown to be reproduced", " ".join(proof["failures"]))


if __name__ == "__main__":
    unittest.main()
