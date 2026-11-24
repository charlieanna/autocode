"""Mutation evidence for runners that name their tests only in JUnit XML: Maven, Gradle, PHPUnit and CTest.

The reports below have the shapes the real tools wrote (Surefire 3, CTest 3.28, PHPUnit 11). No tool and no
model run: a scripted runner writes each run's report from the bytes of the source it is given.
"""

import re
import shlex
import tempfile
import unittest
from pathlib import Path

import autocode_junit as junit
import autocode_mutation_check as mutation_check
import autocode_mutation_results as mutation_results
import autocode_runner_reports as runner_reports
import autocode_text_mutants as text_mutants
import autocode_toolchain_suite as toolchain_suite

SUREFIRE = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="{cls}" time="0.01" tests="1" errors="0" skipped="0" failures="{failures}">
<properties><property name="java.version" value="21"/></properties>
<testcase name="{name}" classname="{cls}" time="0.001">{failure}</testcase>
</testsuite>
"""
CTEST = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="(empty)" tests="{count}" failures="{failures}" disabled="0" skipped="0" hostname="" time="0">
{cases}</testsuite>
"""
PAGER_C = b"int page_count(int total, int per_page) {\n    if (per_page <= 0) {\n        return 0;\n    }\n    return (total + per_page - 1) / per_page;\n}\n"


def surefire(cls, name, failed=False):
    failure = '<failure message="expected 4" type="java.lang.AssertionError">trace</failure>' if failed else ""
    return SUREFIRE.format(cls=cls, name=name, failures=int(failed), failure=failure)


def ctest(outcomes):
    cases = "".join(
        f'<testcase name="{name}" classname="{name}" time="0" status="{"fail" if failed else "run"}">'
        + ('<failure message="Failed"/>' if failed else "")
        + "<properties/><system-out></system-out></testcase>\n"
        for name, failed in outcomes.items()
    )
    return CTEST.format(count=len(outcomes), failures=sum(outcomes.values()), cases=cases)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.tree = Path(folder.name).resolve() / "tree"
        self.tree.mkdir()
        self.report = Path(folder.name).resolve() / "unmutated.junit.xml"

    def test_the_runner_is_the_last_step_of_the_command(self):
        for command, expected in (
            ("mvn -B -q test", "maven"),
            ("./mvnw -B -q test -Dtest=PagerTest", "maven"),
            ("./gradlew --no-daemon -q test", "gradle"),
            ("vendor/bin/phpunit", "phpunit"),
            ("php vendor/bin/phpunit --testsuite unit", "phpunit"),
            (toolchain_suite.CMAKE_SUITE, "ctest"),
            ("npm test", None),
            ("sh run_tests.sh", None),
            ("mvn test | tee log", None),
            ("mvn test; true", None),
            ("", None),
        ):
            with self.subTest(command=command):
                self.assertEqual(expected, runner_reports.runner(command))

    def test_a_build_tools_earlier_reports_are_removed_and_its_command_kept(self):
        for module in ("core", "app"):
            stale = self.tree / module / "target" / "surefire-reports"
            stale.mkdir(parents=True)
            (stale / "TEST-example.PagerTest.xml").write_text(surefire("example.PagerTest", "rounds"))
        self.assertEqual("mvn -B -q test", runner_reports.prepare("mvn -B -q test", self.tree, self.report))
        self.assertEqual([], list(self.tree.rglob("TEST-*.xml")))
        self.assertIsNone(runner_reports.results("mvn -B -q test", self.tree, self.report))

    def test_phpunit_and_ctest_are_asked_for_their_report(self):
        self.report.write_text("an earlier run's report")
        command = runner_reports.prepare("vendor/bin/phpunit", self.tree, self.report)
        self.assertEqual(f"vendor/bin/phpunit --log-junit {self.report}", command)
        self.assertFalse(self.report.exists())
        command = runner_reports.prepare(toolchain_suite.CMAKE_SUITE, self.tree, self.report)
        self.assertEqual(f"{toolchain_suite.CMAKE_SUITE} --output-junit {self.report}", command)

    def test_each_modules_class_reports_are_merged(self):
        for module, cls, failed in (("core", "example.core.PagerTest", True), ("app", "example.app.ReportTest", False)):
            directory = self.tree / module / "target" / "surefire-reports"
            directory.mkdir(parents=True)
            (directory / f"TEST-{cls}.xml").write_text(surefire(cls, "rounds", failed))
        results = runner_reports.results("mvn -B -q test", self.tree, self.report)
        self.assertEqual(["example.app.ReportTest::rounds"], results["passed"])
        self.assertEqual(["example.core.PagerTest::rounds"], results["failed"])
        self.assertEqual((2, True), (results["total"], results["complete"]))
        # Gradle keeps them per test task.
        gradle = self.tree / "lib" / "build" / "test-results" / "test"
        gradle.mkdir(parents=True)
        (gradle / "TEST-example.LabelTest.xml").write_text(surefire("example.LabelTest", "label"))
        self.assertEqual(
            ["example.LabelTest::label"], runner_reports.results("gradle test", self.tree, self.report)["passed"]
        )

    def test_a_ctest_failure_is_a_failed_test_and_an_unknown_status_is_refused(self):
        self.report.write_text(ctest({"rounds": False, "zero": True}))
        results = runner_reports.results(toolchain_suite.CMAKE_SUITE, self.tree, self.report)
        self.assertEqual((["rounds::rounds"], ["zero::zero"]), (results["passed"], results["failed"]))
        for case in (
            '<testcase name="t" classname="t" status="fail"/>',
            '<testcase name="t" classname="t" status="notrun"><skipped/></testcase>',
        ):
            with self.subTest(case=case), self.assertRaises(ValueError):
                junit._parse(f"<testsuite>{case}</testsuite>".encode())

    def test_an_ambiguous_or_refused_report_has_no_results(self):
        for module in ("core", "app"):
            directory = self.tree / module / "target" / "surefire-reports"
            directory.mkdir(parents=True)
            (directory / "TEST-example.PagerTest.xml").write_text(surefire("example.PagerTest", "rounds"))
        self.assertIsNone(runner_reports.results("mvn test", self.tree, self.report))
        self.report.write_text("<testsuite><testcase name='t'/>")
        self.assertIsNone(runner_reports.results("vendor/bin/phpunit", self.tree, self.report))

    def test_a_build_tool_stops_after_a_failing_module(self):
        self.assertTrue(mutation_results.stops_after_failure("mvn -B -q test"))
        self.assertTrue(mutation_results.stops_after_failure("cd core && ./gradlew test"))
        self.assertFalse(mutation_results.stops_after_failure(toolchain_suite.CMAKE_SUITE))
        self.assertFalse(mutation_results.stops_after_failure("vendor/bin/phpunit"))


class TextMutantTests(unittest.TestCase):
    def sites(self, path, source):
        lines = range(1, source.count(b"\n") + 2)
        return [(m.line, m.operator, m.after) for m in text_mutants.text_mutants(path, source, lines)[0]]

    def test_c_family_sites(self):
        self.assertEqual(
            [
                (2, "negate_condition", "!(per_page <= 0)"),
                (2, "swap_bound", "<"),
                (5, "swap_arithmetic", "-"),
                (5, "swap_arithmetic", "+"),
                (5, "constant_return", "0"),
            ],
            self.sites("pager.c", PAGER_C),
        )
        java = b'class P {\n    int f(int n) {\n        log("n < 0 " + n);\n        return n + 1;\n    }\n}\n'
        # Java has no constant of every type, and a call is a statement only with its semicolon.
        self.assertEqual([(3, "drop_call", "(removed)"), (4, "swap_arithmetic", "-")], self.sites("P.java", java))
        self.assertEqual([(1, "flip_boolean", "false")], self.sites("P.kt", b"val ok = true\n"))
        self.assertEqual([], self.sites("p.cpp", b"    builder.add(x)\n"))

    def test_php_comments_and_attributes(self):
        php = (
            b"<?php\n# if ($a < $b) {\n#[Attr(true)]\nfunction f($a) {\n    $this->save($a);\n    return $a === 1;\n}\n"
        )
        self.assertEqual(
            [
                (3, "flip_boolean", "false"),
                (5, "drop_call", "(removed)"),
                (6, "negate_equality", "!=="),
            ],
            self.sites("x.php", php),
        )


class MutationCheckTests(unittest.TestCase):
    """The CTest suite of a C project, run by a script that judges the mutated source as the tests would."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.tree = Path(folder.name).resolve() / "tree"
        self.evidence = Path(folder.name).resolve() / "mutation-check"
        self.tree.mkdir()
        self.source = self.tree / "pager.c"
        self.source.write_bytes(PAGER_C)
        self.times = []

    def run_ctest(self, command, label, limit):
        self.times.append(self.source.stat().st_mtime_ns)
        text = self.source.read_text()
        output = self.evidence / f"{label}.log"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(command)
        if text.count("return 0;") == 2:  # as if the constant return did not compile: no test ran, no report
            return {"command": command, "output": str(output), "exit_code": 2, "timed_out": False, "error": ""}
        outcomes = {  # whether each test fails against this source
            "boundary": False,
            "rounds": "return (total + per_page - 1) / per_page;" not in text,
            "zero": "if (per_page <= 0)" not in text,
        }
        report = re.search(r"--output-junit (\S+)$", command)
        Path(shlex.split(report.group(1))[0]).write_text(ctest(outcomes))
        failed = any(outcomes.values())
        return {"command": command, "output": str(output), "exit_code": int(failed), "timed_out": False, "error": ""}

    def collect(self):
        diff = lambda files: "diff --git a/pager.c b/pager.c\n+++ b/pager.c\n@@ -0,0 +1,6 @@\n"
        return mutation_check.collect_selected_tests(
            "cmake",
            toolchain_suite.CMAKE_SUITE,
            [],
            self.tree,
            self.evidence,
            [("pager.c", "modified")],
            diff,
            self.run_ctest,
            enforcement=True,
        )

    def test_ctest_proof_tests_are_observed_and_kill_mutants_by_name(self):
        result = self.collect()
        self.assertTrue(result["collected"], result["note"])
        self.assertEqual(["boundary::boundary", "rounds::rounds", "zero::zero"], result["selected_tests"])
        rows = {(m["line"], m["operator"], m["after"]): m for m in result["files"][0]["mutants"]}
        self.assertEqual("killed", rows[(2, "negate_condition", "!(per_page <= 0)")]["outcome"])
        self.assertEqual(["zero::zero"], rows[(2, "negate_condition", "!(per_page <= 0)")]["failed_tests"])
        self.assertEqual(["rounds::rounds"], rows[(5, "swap_arithmetic", "-")]["failed_tests"])
        # The constant return wrote no report: a build that failed is not a site, never a kill.
        self.assertNotIn((5, "constant_return", "0"), rows)
        self.assertIn("pager.c:5: the constant_return mutant did not build; not a site", result["files"][0]["notes"])
        # A Make or Ninja build rebuilds only a source newer than its last build: every version is newer.
        self.assertEqual(sorted(set(self.times)), self.times)
        self.assertEqual(PAGER_C, self.source.read_bytes())

    def test_without_a_report_runner_the_record_says_so(self):
        result = mutation_check.collect_selected_tests(
            None, "sh run_tests.sh", [], self.tree, self.evidence, [], lambda files: "", self.run_ctest
        )
        self.assertEqual(mutation_check.NO_SOURCE, result["unavailable"])


if __name__ == "__main__":
    unittest.main()
