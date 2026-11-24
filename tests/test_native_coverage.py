"""Coverage from a project's own gcov, JaCoCo, PHPUnit or coverlet, read from LCOV, Cobertura or JaCoCo XML.

The reports below are trimmed from real ones: gcovr 8.8 and lcov 2.0 over a C file compiled with
``--coverage``, and JaCoCo 0.8.12's Maven report over a Java class. No model, and no tool is run.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_coverage_reports as coverage_reports
import autocode_diff_coverage as diff_coverage
import autocode_native_coverage as native_coverage

PAGER_C = b'#include "pager.h"\n\nint page_count(int total, int per_page) {\n    if (per_page <= 0) {\n        return 0;\n    }\n    return (total + per_page - 1) / per_page;\n}\n'
GCOVR = """<?xml version='1.0' encoding='utf-8'?>
<!DOCTYPE coverage SYSTEM 'https://github.com/cobertura/cobertura/blob/master/coverage-04.dtd'>
<coverage line-rate="0.8" version="gcovr 8.8"><sources><source>{root}</source></sources><packages><package name="">
<classes><class name="pager_c" filename="pager.c"><methods><method name="page_count"><lines>
<line number="3" hits="1"/><line number="4" hits="1" branch="true"/><line number="5" hits="0"/><line number="7" hits="1"/>
</lines></method></methods><lines><line number="3" hits="1"/><line number="4" hits="1" branch="true"/>
<line number="5" hits="0"/><line number="7" hits="1"/></lines></class></classes></package></packages></coverage>
"""
LCOV = """TN:
SF:{root}/pager.c
FN:3,8,page_count
FNDA:1,page_count
DA:3,1
DA:4,1
DA:5,0
DA:7,1
LF:4
LH:3
end_of_record
"""
JACOCO = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?><!DOCTYPE report PUBLIC "-//JACOCO//DTD Report 1.1//EN" "report.dtd"><report name="pager"><package name="example"><class name="example/Pager" sourcefilename="Pager.java"><method name="pageCount" desc="(II)I" line="5"><counter type="LINE" missed="1" covered="2"/></method></class><sourcefile name="Pager.java"><line nr="3" mi="3" ci="0" mb="0" cb="0"/><line nr="5" mi="0" ci="2" mb="1" cb="1"/><line nr="6" mi="2" ci="0" mb="0" cb="0"/><line nr="8" mi="0" ci="8" mb="0" cb="0"/><counter type="LINE" missed="2" covered="2"/></sourcefile></package></report>"""
PAGER_JAVA = "core/src/main/java/example/Pager.java"


class ReportTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        (self.root / "pager.c").write_bytes(PAGER_C)

    def test_each_format_is_recognized_by_its_bytes(self):
        self.assertEqual("cobertura", coverage_reports.detect(GCOVR.format(root=self.root).encode()))
        self.assertEqual("lcov", coverage_reports.detect(LCOV.format(root=self.root).encode()))
        self.assertEqual("jacoco", coverage_reports.detect(JACOCO))
        for raw in (b"", b"{}", b"<html/>", b"<coverage", b"mode: set\n"):
            with self.subTest(raw=raw):
                self.assertIsNone(coverage_reports.detect(raw))
                with self.assertRaises(ValueError):
                    coverage_reports.read(raw, self.root)

    def test_gcov_reports_name_the_statements_and_leave_braces_out(self):
        for report in (GCOVR, LCOV):
            with self.subTest(report=report[:20]):
                executed, missing = coverage_reports.read(report.format(root=self.root).encode(), self.root)
                self.assertEqual({3, 4, 7}, executed["pager.c"])
                self.assertEqual({5}, missing["pager.c"])

    def test_a_java_package_path_is_matched_to_the_one_changed_file_it_names(self):
        executed, missing = coverage_reports.read(
            JACOCO, self.root, [PAGER_JAVA, "core/src/main/java/example/Other.java"]
        )
        self.assertEqual({5, 8}, executed[PAGER_JAVA])
        self.assertEqual({3, 6}, missing[PAGER_JAVA])
        # Two changed files the report could name: it measures neither.
        ambiguous = [PAGER_JAVA, "app/src/main/java/example/Pager.java"]
        executed, _ = coverage_reports.read(JACOCO, self.root, ambiguous)
        self.assertEqual(["example/Pager.java"], list(executed))

    def test_malformed_line_records_are_refused(self):
        for raw in (
            b"SF:pager.c\nDA:three,1\nend_of_record\n",
            b"SF:pager.c\nDA:3\nend_of_record\n",
            b"SF:pager.c\nDA:0,1\nend_of_record\n",
            b'<coverage><packages><package><classes><class filename="pager.c"><lines><line number="x" hits="1"/>'
            b"</lines></class></classes></package></packages></coverage>",
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                coverage_reports.read(raw, self.root)

    def test_finish_reads_a_native_report_against_the_changed_hunks(self):
        report = self.root / "gcov-report"
        report.write_text(GCOVR.format(root=self.root))
        diff = "diff --git a/pager.c b/pager.c\n+++ b/pager.c\n@@ -3,0 +3,6 @@\n"
        result = diff_coverage.finish("gcovr", report, self.root, diff)
        self.assertTrue(result["collected"])
        self.assertEqual({"executed": [3, 4, 7], "missing": [5]}, result["measured_lines"]["pager.c"])
        self.assertEqual([5], result["uncovered"][0]["uncovered_lines"])


class PrepareTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.tree = Path(folder.name).resolve() / "tree"
        self.evidence = Path(folder.name).resolve() / "evidence"
        self.tree.mkdir()

    def prepare(self, command, framework=None, probes=()):
        with patch.object(native_coverage, "_probe", side_effect=lambda words, tree: words[0] in probes):
            return diff_coverage.prepare(framework, command, self.tree, self.evidence)

    def test_gcov_notes_from_the_proof_run_select_gcovr_or_lcov_whatever_the_runner(self):
        (self.tree / "build").mkdir()
        (self.tree / "build" / "pager.gcno").write_bytes(b"notes")
        stale = self.tree / "build" / "pager.gcda"
        for runner, probes, tool in (("gcovr", ("gcovr", "lcov"), "gcovr"), ("lcov", ("lcov",), "lcov")):
            with self.subTest(runner=runner):
                stale.write_bytes(b"an earlier run's counts")
                spec = self.prepare("sh run_tests.sh", probes=probes)
                self.assertEqual(tool, spec["tool"])
                self.assertEqual("sh run_tests.sh", spec["command"])
                self.assertIn(str(spec["report"]), spec["finalize"])
                self.assertFalse(stale.exists(), "a report must describe only the run that follows")
        self.assertIsNone(self.prepare("sh run_tests.sh", probes=()))

    def test_no_native_tool_means_no_extra_run(self):
        for command in ("sh run_tests.sh", "mvn -B -q test", "vendor/bin/phpunit", "dotnet test"):
            with self.subTest(command=command):
                self.assertIsNone(self.prepare(command))

    def test_jacoco_data_the_build_left_selects_its_own_report_task(self):
        for command, data, task, written in (
            ("mvn -B -q test", "target/jacoco.exec", "jacoco:report", "target/site/jacoco/jacoco.xml"),
            ("./gradlew --no-daemon -q test", "build/jacoco/test.exec", "jacocoTestReport", "jacocoTestReport.xml"),
        ):
            with self.subTest(command=command):
                (self.tree / data).parent.mkdir(parents=True, exist_ok=True)
                (self.tree / data).write_bytes(b"agent data")
                spec = self.prepare(command)
                self.assertEqual(("jacoco", command), (spec["tool"], spec["command"]))
                self.assertTrue(spec["finalize"].startswith(command.split()[0] + " "))
                self.assertIn(task, spec["finalize"])
                self.assertIn(written, spec["finalize"])
                self.assertFalse((self.tree / data).exists())

    def test_phpunit_with_a_coverage_driver_writes_cobertura(self):
        self.assertIsNone(self.prepare("vendor/bin/phpunit", probes=()))
        spec = self.prepare("vendor/bin/phpunit --testsuite unit", probes=("php",))
        self.assertEqual("phpunit", spec["tool"])
        self.assertEqual(
            f"XDEBUG_MODE=coverage vendor/bin/phpunit --testsuite unit --coverage-cobertura {spec['report']}",
            spec["command"],
        )

    def test_dotnet_test_with_coverlet_collects_cobertura(self):
        (self.tree / "Pager.Tests").mkdir()
        project = self.tree / "Pager.Tests" / "Pager.Tests.csproj"
        project.write_text('<Project><ItemGroup><PackageReference Include="xunit" /></ItemGroup></Project>')
        self.assertIsNone(self.prepare("dotnet test"))
        project.write_text(
            '<Project><ItemGroup><PackageReference Include="coverlet.collector" /></ItemGroup></Project>'
        )
        spec = self.prepare("dotnet test")
        self.assertEqual("coverlet", spec["tool"])
        self.assertIn("--collect 'XPlat Code Coverage' --results-directory", spec["command"])
        self.assertIn("coverage.cobertura.xml", spec["finalize"])

    def test_python_and_javascript_runners_keep_their_own_tools(self):
        (self.tree / "pager.gcno").write_bytes(b"notes")
        framework = type("Framework", (), {"name": "pytest"})()
        with patch.object(diff_coverage, "_probe", return_value=False):
            self.assertIsNone(self.prepare("python -m pytest", framework, probes=("gcovr",)))


if __name__ == "__main__":
    unittest.main()
