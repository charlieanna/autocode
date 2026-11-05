"""The advisory diff coverage of this repository's own pull requests (#2754)."""

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import ci_diff_coverage

ROOT = Path(__file__).resolve().parents[1]
BASE_MODULE = "def kept():\n    return 1\n"
CHANGED_MODULE = textwrap.dedent(
    """\
    def kept():
        return 1


    def added(flag):
        if flag:
            return "ran"
        return "never ran"
    """
)


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True, env=GIT_ENV
    ).stdout.strip()


GIT_ENV = dict(
    os.environ,
    GIT_AUTHOR_NAME="t",
    GIT_AUTHOR_EMAIL="t@example.invalid",
    GIT_COMMITTER_NAME="t",
    GIT_COMMITTER_EMAIL="t@example.invalid",
    GIT_CONFIG_GLOBAL=os.devnull,
    GIT_CONFIG_NOSYSTEM="1",
)


class ReportTests(unittest.TestCase):
    """A pull request's changed tools/ lines, intersected with a coverage.py JSON report."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.root)]))
        (self.root / "tools" / "dashboard" / "tests").mkdir(parents=True)
        (self.root / "tools" / "module.py").write_text(BASE_MODULE)
        (self.root / "tools" / "retired.py").write_text("GONE = 1\n")
        git(self.root, "init", "-q")
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "base")
        self.base = git(self.root, "rev-parse", "HEAD")
        (self.root / "tools" / "module.py").write_text(CHANGED_MODULE)
        (self.root / "tools" / "fresh.py").write_text("VALUE = 2\n")
        (self.root / "tools" / "dashboard" / "tests" / "test_page.py").write_text("X = 1\n")
        (self.root / "tools" / "notes.md").write_text("not code\n")
        (self.root / "tools" / "retired.py").unlink()
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "change")

    def coverage_json(self, files):
        path = self.root / "coverage.json"
        path.write_text(json.dumps({"files": files}))
        return path

    def test_changed_files_are_product_python_added_or_modified_never_tests_or_deletions(self):
        self.assertEqual(
            [("tools/fresh.py", "added"), ("tools/module.py", "modified")],
            sorted(ci_diff_coverage.changed_files(self.root, self.base)),
        )

    def test_report_names_each_changed_line_no_test_executed(self):
        report = self.coverage_json(
            {
                "tools/module.py": {"executed_lines": [1, 2, 5, 6, 7], "missing_lines": [8]},
                "tools/fresh.py": {"executed_lines": [1], "missing_lines": []},
            }
        )
        result = ci_diff_coverage.report(self.base, report, root=self.root)
        self.assertEqual([("tools/module.py", [8])], [(h["path"], h["uncovered_lines"]) for h in result["uncovered"]])
        # Lines 5-8 are changed statements in module.py and line 1 in fresh.py; blank lines are not.
        self.assertEqual((5, 4), (result["statements"], result["covered"]))
        text = ci_diff_coverage.markdown(result)
        self.assertIn("4 of 5 changed statement lines ran under the selected tests (80%).", text)
        self.assertIn("| `tools/module.py` | 8 |", text)
        self.assertNotIn("fresh.py` |", text)
        self.assertEqual(
            [
                "::notice file=tools/module.py,line=8,endLine=8,title=Not executed by tests::"
                "No test in this pull request's selection executed this changed line."
            ],
            ci_diff_coverage.notices(result, 10),
        )

    def test_a_file_the_report_never_mentions_is_unmeasured_not_covered(self):
        result = ci_diff_coverage.report(self.base, self.coverage_json({}), root=self.root)
        self.assertEqual(["tools/fresh.py", "tools/module.py"], sorted(result["unmeasured"]))
        self.assertEqual(0, result["statements"])
        self.assertIn("Not measured", ci_diff_coverage.markdown(result))

    def test_a_missing_report_is_unmeasured_and_the_cli_still_succeeds(self):
        summary = self.root / "summary.md"
        out = self.root / "report.json"
        argv = ["report", "--base", self.base, "--coverage-json", str(self.root / "absent.json")]
        argv += ["--summary", str(summary), "--json-out", str(out)]
        previous = ci_diff_coverage.REPO_ROOT
        ci_diff_coverage.REPO_ROOT = self.root
        self.addCleanup(setattr, ci_diff_coverage, "REPO_ROOT", previous)
        with contextlib.redirect_stdout(io.StringIO()):
            status = ci_diff_coverage.main(argv)
        self.assertEqual(0, status)
        self.assertIn("Not measured", summary.read_text())
        self.assertEqual(["tools/fresh.py", "tools/module.py"], sorted(json.loads(out.read_text())["unmeasured"]))

    def test_the_cli_prints_the_summary_and_a_notice_per_uncovered_span(self):
        report = self.coverage_json({"tools/module.py": {"executed_lines": [5, 6], "missing_lines": [7, 8]}})
        previous = ci_diff_coverage.REPO_ROOT
        ci_diff_coverage.REPO_ROOT = self.root
        self.addCleanup(setattr, ci_diff_coverage, "REPO_ROOT", previous)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = ci_diff_coverage.main(["report", "--base", self.base, "--coverage-json", str(report)])
        self.assertEqual(0, status)
        self.assertIn("| `tools/module.py` | 7-8 |", output.getvalue())
        self.assertIn("::notice file=tools/module.py,line=7,endLine=8,", output.getvalue())

    def test_run_without_a_command_is_a_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stop:
            ci_diff_coverage.main(["run", "--data-dir", str(self.root / "data"), "--"])
        self.assertEqual(2, stop.exception.code)

    def test_annotations_are_one_per_span_and_bounded(self):
        result = {"uncovered": [{"path": "tools/a.py", "uncovered_lines": [3, 4, 5, 9, 20, 21]}]}
        notices = ci_diff_coverage.notices(result, 2)
        self.assertEqual(2, len(notices))
        self.assertIn("line=3,endLine=5", notices[0])
        self.assertIn("line=9,endLine=9", notices[1])

    def test_a_change_without_product_lines_says_so(self):
        text = ci_diff_coverage.markdown({"files": [], "uncovered": [], "unmeasured": []})
        self.assertIn("changes no Python product lines", text)


@unittest.skipUnless(importlib.util.find_spec("coverage"), "coverage.py is installed only in the diff-coverage job")
class RunTests(unittest.TestCase):
    def test_a_subprocess_the_command_starts_is_measured(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tools").mkdir()
            (root / "tools" / "child.py").write_text(
                "def hit():\n    return 1\n\n\nhit()\n\n\ndef miss():\n    return 2\n"
            )
            # The command itself never imports tools/: only the Python it launches does.
            launcher = "import subprocess, sys; sys.exit(subprocess.run([sys.executable, 'tools/child.py']).returncode)"
            data = root / "data"
            status = ci_diff_coverage.run(data, [sys.executable, "-c", launcher], root=root)
            self.assertEqual(0, status)
            files = json.loads((data / "coverage.json").read_text())["files"]
            entry = next(info for path, info in files.items() if path.endswith("child.py"))
            self.assertEqual([1, 2, 5, 8], entry["executed_lines"])
            self.assertEqual([9], entry["missing_lines"])

    def test_the_cli_returns_the_measured_commands_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tools").mkdir()
            previous = ci_diff_coverage.REPO_ROOT
            ci_diff_coverage.REPO_ROOT = root
            self.addCleanup(setattr, ci_diff_coverage, "REPO_ROOT", previous)
            argv = ["run", "--data-dir", str(root / "data"), "--", sys.executable, "-c", "raise SystemExit(3)"]
            self.assertEqual(3, ci_diff_coverage.main(argv))
            self.assertTrue((root / "data" / "coveragerc").is_file())


if __name__ == "__main__":
    unittest.main()
