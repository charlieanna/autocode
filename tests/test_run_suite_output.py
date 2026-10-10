"""tools/run_suite.py's parallel output, read the way AutoCode's verifier reads a declared suite command (#545).

A project can declare ``tools/run_suite.py --verbosity 2`` as its suite command
(docs/bugs/saved-verification-commands.md); the verifier then reads per-test results and each
failure's traceback from that output (``autocode_verify.per_test_results``,
``autocode_test_setup.failure_details``). run_parallel prints a failed module's output as soon as
the module finishes, so later modules' output follows it. These tests run real test modules in
real interpreters through run_parallel, with the order the modules finish in fixed by the test
(the first failure finishes first, the slow module last), and read the result as the verifier does.
"""

from __future__ import annotations

import contextlib
import io
import re
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from pathlib import Path
from unittest import mock

import autocode_verify as verify
import run_suite

# The modules, in the order they finish.
MODULES = {
    # A setup-only failure on base: the mock target does not exist yet.
    "tests.test_b_mock": """
        from unittest import mock
        import app

        class B(unittest.TestCase):
            def test_w(self):
                with mock.patch("app.gone"):
                    pass
    """,
    # A passing module whose test logs an error that application code raised and the test caught.
    "tests.test_d_logs_app": """
        import logging
        import app

        class D(unittest.TestCase):
            def test_z(self):
                try:
                    app.work()
                except ValueError:
                    logging.warning("handled in app", exc_info=True)
    """,
    # A genuine assertion failure, beside a passing test.
    "tests.test_a_fail": """
        class A(unittest.TestCase):
            def test_x(self):
                self.assertEqual(1, 2)

            def test_x_ok(self):
                pass
    """,
    # A passing module whose own test code catches and logs a ModuleNotFoundError.
    "tests.test_c_logs_import": """
        import logging

        class C(unittest.TestCase):
            def test_y(self):
                try:
                    import optional_dep_545  # noqa: F401
                except ImportError:
                    logging.warning("optional dependency missing", exc_info=True)
    """,
    # The slow module: still running when every failure has finished.
    "tests.test_e_slow": """
        class E(unittest.TestCase):
            def test_slow(self):
                pass

            def test_slow_2(self):
                pass
    """,
}
ORDER = list(MODULES)
MISSING_MOCK = "The test could not prepare its mock: target 'gone' is absent on base"
# Seconds a module waits for the one before it to be reported; reached only if that never happens.
GUARD = 60


class _Rows(io.StringIO):
    """stdout that signals once each module's result row has been written."""

    def __init__(self):
        super().__init__()
        self.shown = {module: threading.Event() for module in ORDER}

    def write(self, text):
        written = super().write(text)
        for module, shown in self.shown.items():
            if f"s  {module} (" in text:
                shown.set()
        return written


class ParallelOutputAsTheVerifierReadsItTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="run-suite-545-")
        cls.root = Path(cls.tmp.name).resolve()
        (cls.root / "tests").mkdir()
        (cls.root / "tests" / "__init__.py").write_text("")
        (cls.root / "app.py").write_text("def work():\n    raise ValueError('x')\n")
        for module, body in MODULES.items():
            (cls.root / f"{module.replace('.', '/')}.py").write_text("import unittest\n" + textwrap.dedent(body))
        cls.outputs = {verbosity: cls.run_parallel(verbosity) for verbosity in (1, 2)}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def run_parallel(cls, verbosity):
        """Run every module at once in its own interpreter; each is reported only after the one before it."""
        real_run_module = run_suite.run_module
        out = _Rows()

        def run_module(module, verbosity):
            row = real_run_module(module, verbosity)
            index = ORDER.index(module)
            if index and not out.shown[ORDER[index - 1]].wait(GUARD):
                raise AssertionError(f"{ORDER[index - 1]} was never reported")
            return row

        with (
            mock.patch.object(run_suite, "REPO_ROOT", cls.root),
            mock.patch.object(run_suite, "run_module", run_module),
            contextlib.redirect_stdout(out),
        ):
            ok = run_suite.run_parallel(ORDER, len(ORDER), verbosity)
        assert not ok
        return out.getvalue()

    def results(self, output, name):
        log = self.root / f"{name}.log"
        log.write_text(output)
        return verify.per_test_results(
            verify.Framework("unittest", "tools/run_suite.py --verbosity 2"),
            {"output": str(log)},
            self.root / f"{name}.junit.xml",
            tree=str(self.root),
        )

    def test_the_modules_finish_in_the_order_the_test_sets(self):
        for verbosity, output in self.outputs.items():
            with self.subTest(verbosity=verbosity):
                rows = re.findall(r"^(ok  |FAIL) +\d+\.\ds  (\S+) \((\d+) tests\)$", output, re.M)
                self.assertEqual(
                    [
                        ("FAIL", ORDER[0], "1"),
                        ("ok  ", ORDER[1], "1"),
                        ("FAIL", ORDER[2], "2"),
                        ("ok  ", ORDER[3], "1"),
                        ("ok  ", ORDER[4], "2"),
                    ],
                    rows,
                )
                self.assertRegex(
                    output,
                    r"\n\nRan 7 tests in 5 modules, 5 at a time, in \d+s: 2 module\(s\) "
                    r"FAILED: tests\.test_b_mock, tests\.test_a_fail\n$",
                )

    def test_a_missing_mock_on_base_is_still_recognised(self):
        # The verifier excludes a test that could not prepare its mock from a bug-fix proof. Later
        # modules' output, here an application frame logged by a passing test, is not its traceback.
        for verbosity, output in self.outputs.items():
            with self.subTest(verbosity=verbosity):
                self.assertEqual(
                    {"tests.test_b_mock.B.test_w": MISSING_MOCK},
                    self.results(output, f"v{verbosity}").get("setup_errors"),
                )

    def test_a_real_failure_never_reads_as_a_missing_import(self):
        # A later passing module logs a caught ModuleNotFoundError from its own test code. Read as part
        # of test_x's traceback, it would make a real failure on base "could not import its dependency",
        # and a regression proof would discount it.
        for verbosity, output in self.outputs.items():
            with self.subTest(verbosity=verbosity):
                results = self.results(output, f"v{verbosity}")
                self.assertEqual(["tests.test_a_fail.A.test_x", "tests.test_b_mock.B.test_w"], results["failed"])
                self.assertNotIn("tests.test_a_fail.A.test_x", results.get("setup_errors", {}))

    def test_the_verifier_reads_what_one_unittest_run_of_the_same_modules_reports(self):
        stock = subprocess.run(
            [sys.executable, "-m", "unittest", "-v", *ORDER], cwd=self.root, capture_output=True, text=True
        )
        expected = self.results(stock.stdout + stock.stderr, "stock")
        self.assertEqual(5, len(expected["passed"]))
        results = self.results(self.outputs[2], "v2")
        for key in ("passed", "failed", "skipped", "total", "complete", "setup_errors"):
            self.assertEqual(expected.get(key), results.get(key), key)

    def test_the_failures_are_shown_again_beside_the_summary(self):
        # A CI log opens at its end, and CI runs --verbosity 2: a failure that finished first must not
        # end up above every later module's output with only its module's name beside the summary.
        for verbosity, output in self.outputs.items():
            with self.subTest(verbosity=verbosity):
                after_the_last_module = output[output.index(f"s  {ORDER[-1]} (") :]
                again = after_the_last_module[after_the_last_module.index("\nThe failures again:\n") :]
                self.assertLess(
                    again.index("does not have the attribute 'gone'"), again.index("AssertionError: 1 != 2")
                )
                self.assertLess(again.index("AssertionError: 1 != 2"), again.index("\n\nRan 7 tests in 5 modules"))
                self.assertEqual(2, output.count("AssertionError: 1 != 2"))
                self.assertNotIn("optional_dep_545", again)
                self.assertNotIn("handled in app", again)
                # Per-test result lines are not repeated.
                self.assertEqual(int(verbosity > 1), output.count("test_x (tests.test_a_fail.A.test_x) ... FAIL"))
                self.assertEqual(int(verbosity > 1), output.count("test_x_ok (tests.test_a_fail.A.test_x_ok) ... ok"))


if __name__ == "__main__":
    unittest.main()
