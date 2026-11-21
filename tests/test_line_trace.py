"""Changed-line coverage beside a project's own coverage session (#4039).

A project that starts coverage itself (pytest-cov's ``--cov`` in ``addopts``, a conftest that
calls ``coverage.Coverage().start()``) took over the tracer ``coverage run`` used, so the proof's
measurement collected no data and every changed line read unmeasured. These tests run the real
collection in a disposable project with the interpreter running them.
"""

import importlib.util
import json
import os
import shlex
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

import autocode_diff_coverage as diff_coverage
import autocode_verify as verify

HAS_COVERAGE = importlib.util.find_spec("coverage") is not None
HAS_MONITORING = hasattr(sys, "monitoring")
needs_trace = unittest.skipUnless(
    HAS_COVERAGE and HAS_MONITORING, "needs coverage.py and Python 3.12 or later (sys.monitoring)"
)

PRODUCT = """\
def sign(x):
    if x > 0:
        return 1
    return -1
"""
DIFF = "diff --git a/pkg.py b/pkg.py\n+++ b/pkg.py\n@@ -0,0 +1,4 @@\n" + "".join(
    "+" + line + "\n" for line in PRODUCT.splitlines()
)
# The project's own coverage session, started as pytest-cov starts it: before any test is imported,
# stopped when pytest finishes. Its data goes to the project's own file, not the proof's.
OWN_SESSION = """\
import coverage

_own = coverage.Coverage(data_file=".project-coverage", source=["."])
_own.start()


def pytest_unconfigure(config):
    _own.stop()
    _own.save()
"""


def run(tree):
    def runner(command, log):
        done = subprocess.run(shlex.split(command), cwd=tree, capture_output=True, text=True, timeout=120)
        Path(log).write_text(done.stdout + done.stderr)
        return {"command": command, "exit_code": done.returncode, "output": str(log)}

    return runner


class LineTraceTests(unittest.TestCase):
    def project(self, test_body, *, conftest=OWN_SESSION):
        folder = tempfile.TemporaryDirectory(prefix="line-trace-")
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        tree, evidence = root / "tree", root / "evidence"
        (tree / "tests").mkdir(parents=True)
        (tree / "pkg.py").write_text(PRODUCT)
        (tree / "tests" / "test_pkg.py").write_text(textwrap.dedent(test_body))
        if conftest:
            (tree / "conftest.py").write_text(conftest)
        return tree, evidence

    def collect(self, tree, evidence):
        command = shlex.join([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_pkg.py"])
        framework = verify.Framework("pytest", command)
        return diff_coverage.collect(framework, command, tree, evidence, lambda: DIFF, run(tree))

    @needs_trace
    def test_a_projects_own_coverage_session_leaves_the_changed_lines_measured(self):
        tree, evidence = self.project(
            """\
            from pkg import sign

            def test_positive():
                assert sign(2) == 1
            """
        )
        result = self.collect(tree, evidence)
        self.assertTrue(result["collected"], result)
        self.assertEqual({"pkg.py": {"executed": [1, 2, 3], "missing": [4]}}, result["measured_lines"])
        self.assertEqual([{"path": "pkg.py", "start": 1, "end": 4, "uncovered_lines": [4]}], result["uncovered"])
        self.assertEqual([0, 0], [receipt["exit_code"] for receipt in result["command_receipts"]])
        # The project's session still ran and recorded what it measures.
        self.assertTrue((tree / ".project-coverage").is_file())

    @needs_trace
    def test_the_lines_are_measured_without_a_session_of_the_projects_own(self):
        tree, evidence = self.project(
            """\
            from pkg import sign

            def test_negative():
                assert sign(-2) == -1
            """,
            conftest=None,
        )
        result = self.collect(tree, evidence)
        self.assertEqual({"pkg.py": {"executed": [1, 2, 4], "missing": [3]}}, result["measured_lines"])

    @needs_trace
    def test_the_command_runs_as_python_dash_m_would(self):
        tree, evidence = self.project(
            """\
            import json, os, sys

            def test_invocation():
                with open(os.environ["SEEN"], "w") as out:
                    json.dump({"path": sys.path, "argv": sys.argv, "cwd": os.getcwd()}, out)

            def test_fails():
                assert False
            """,
            conftest=None,
        )
        seen = {}
        for name in ("plain", "traced"):
            environment = {**os.environ, "SEEN": str(tree.parent / f"{name}.json")}
            with mock.patch.dict("os.environ", environment):
                if name == "plain":
                    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_pkg.py"]
                    exit_code = subprocess.run(command, cwd=tree, capture_output=True).returncode
                else:
                    result = self.collect(tree, evidence)
                    exit_code = result["command_receipts"][0]["exit_code"]
            seen[name] = (exit_code, json.loads((tree.parent / f"{name}.json").read_text()))
        # The same sys.path, sys.argv and directory as `python -m pytest`, and its exit code.
        self.assertEqual(seen["plain"], seen["traced"])
        self.assertEqual(1, seen["traced"][0])

    @needs_trace
    def test_a_test_cannot_supply_the_data_it_is_measured_by(self):
        # A stale database that says every line of pkg.py ran, copied over the proof's data file
        # while the test runs and again from an exit handler. The target function never runs.
        folder = tempfile.TemporaryDirectory(prefix="line-trace-stale-")
        self.addCleanup(folder.cleanup)
        import coverage

        stale = Path(folder.name) / "stale.coverage"
        tree, evidence = self.project("", conftest=None)
        data = coverage.CoverageData(basename=str(stale))
        data.add_lines({str((tree / "pkg.py").resolve()): [1, 2, 3, 4]})
        data.write()
        target = evidence / ".coverage"
        (tree / "tests" / "test_pkg.py").write_text(
            textwrap.dedent(
                f"""\
                import atexit, shutil

                def test_forges():
                    shutil.copyfile({str(stale)!r}, {str(target)!r})
                    atexit.register(shutil.copyfile, {str(stale)!r}, {str(target)!r})
                """
            )
        )
        result = self.collect(tree, evidence)
        self.assertNotIn("pkg.py", result.get("measured_lines", {}))
        self.assertEqual([], [row for row in result["files"] if any(h.get("covered") for h in row["hunks"])])

    def test_an_interpreter_without_sys_monitoring_keeps_coverage_run(self):
        with tempfile.TemporaryDirectory() as folder:
            command = "python3 -m pytest -q tests/test_pkg.py"
            framework = verify.Framework("pytest", command)

            def probe(arguments, tree):
                return "import coverage" in arguments[-1]

            with mock.patch.object(diff_coverage, "_probe", probe):
                spec = diff_coverage.prepare(framework, command, folder, Path(folder) / "evidence")
            self.assertEqual("coverage.py", spec["tool"])
            self.assertIn("-m coverage run", spec["command"])
            self.assertFalse((Path(folder) / "evidence" / diff_coverage.LINE_TRACE.name).exists())

    def test_an_interpreter_with_sys_monitoring_runs_the_copied_line_trace(self):
        with tempfile.TemporaryDirectory() as folder:
            command = "python3 -m pytest -q tests/test_pkg.py"
            framework = verify.Framework("pytest", command)
            with mock.patch.object(diff_coverage, "_probe", lambda *_: True):
                spec = diff_coverage.prepare(framework, command, folder, Path(folder) / "evidence")
            tracer = Path(folder) / "evidence" / diff_coverage.LINE_TRACE.name
            self.assertEqual(diff_coverage.LINE_TRACE.read_bytes(), tracer.read_bytes())
            data = Path(folder) / "evidence" / ".coverage"
            self.assertEqual(
                ["python3", str(tracer), f"--data-file={data}", "-m", "pytest", "-q", "tests/test_pkg.py"],
                shlex.split(spec["command"]),
            )
            self.assertNotIn("coverage run", spec["command"])


if __name__ == "__main__":
    unittest.main()
