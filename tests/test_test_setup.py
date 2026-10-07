"""Where a unittest failure's traceback ends in a test command's output (autocode_test_setup).

AutoCode's verifier reads each failure's traceback from the command's output to tell a test that could
not prepare itself (a missing mock target, a test-origin import error) from a real failure. A runner that
prints several modules' reports, as tools/run_suite.py does, puts other modules' output after one module's
closing "Ran N tests ... FAILED" footer, and a test's print() to a block-buffered stdout lands after it in
stock output too; none of it belongs to the last traceback (#545).
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest

import autocode_test_setup as test_setup
import autocode_verify as verify

TREE = "/project-545"
RULE, SEPARATOR = "-" * 70, "=" * 70

ASSERTION = ('Traceback (most recent call last):\n  File "tests/test_a.py", line 5, in test_x\n'
             '    self.assertEqual(1, 2)\nAssertionError: 1 != 2\n')
MISSING_MOCK = ('Traceback (most recent call last):\n  File "tests/test_a.py", line 7, in test_x\n'
                '    with mock.patch("app.gone"):\n'
                '  File "/usr/lib/python3/unittest/mock.py", line 1446, in __enter__\n'
                '    original, local = self.get_original()\n'
                "AttributeError: <module 'app' from 'app.py'> does not have the attribute 'gone'\n")
# A later module's passing test whose own code logs a caught error: a test frame and an import error.
LOGGED_IMPORT = ('test_y (tests.test_b.B.test_y) ... ERROR:root:optional dependency missing\n'
                 'Traceback (most recent call last):\n  File "tests/test_b.py", line 9, in test_y\n'
                 "    import optional_dep\nModuleNotFoundError: No module named 'optional_dep'\nok\n")
# A later module's passing test that logs an error raised in application code.
LOGGED_APP_ERROR = ('test_z (tests.test_c.C.test_z) ... ERROR:root:handled in app\n'
                    'Traceback (most recent call last):\n  File "app.py", line 3, in work\n'
                    '    raise ValueError("x")\nValueError: x\nok\n')


def report(traceback, flavour="FAIL"):
    """What `python -m unittest` writes after its last test when tests.test_a.A.test_x fails."""
    return (f"{SEPARATOR}\n{flavour}: test_x (tests.test_a.A.test_x)\n{RULE}\n{traceback}\n"
            f"{RULE}\nRan 1 test in 0.001s\n\nFAILED (failures=1)\n")


def setup_errors(output):
    return [test_setup.setup_error(detail, TREE, lambda path: path.startswith("tests/"))
            for _, _, detail in test_setup.failure_details(output)]


class FailureDetailsTests(unittest.TestCase):
    def test_a_traceback_ends_at_unittests_closing_footer(self):
        [(name, owner, detail)] = test_setup.failure_details(report(ASSERTION) + LOGGED_IMPORT)
        self.assertEqual(("test_x", "tests.test_a.A.test_x"), (name, owner))
        self.assertIn(ASSERTION, detail)
        self.assertNotIn("optional_dep", detail)
        self.assertNotIn("Ran 1 test", detail)

    def test_a_real_failure_never_borrows_a_later_modules_import_error(self):
        self.assertEqual([None], setup_errors(report(ASSERTION)))
        self.assertEqual([None], setup_errors(report(ASSERTION) + LOGGED_IMPORT))

    def test_a_missing_mock_is_recognised_whatever_output_follows(self):
        reason = "The test could not prepare its mock: target 'gone' is absent on base"
        self.assertEqual([reason], setup_errors(report(MISSING_MOCK, "ERROR")))
        self.assertEqual([reason], setup_errors(report(MISSING_MOCK, "ERROR") + LOGGED_APP_ERROR))

    def test_failures_in_one_report_still_end_at_the_next_one(self):
        first = report(ASSERTION).split(f"{RULE}\nRan")[0]  # unittest writes one footer, after the last failure
        second = report(MISSING_MOCK, "ERROR").replace("test_x (tests.test_a.A.test_x)", "test_w (tests.test_a.A.test_w)")
        found = list(test_setup.failure_details(first + second + LOGGED_IMPORT))
        self.assertEqual(["test_x", "test_w"], [name for name, _, _ in found])
        self.assertIn(ASSERTION, found[0][2])
        self.assertNotIn("test_w", found[0][2])
        self.assertIn(MISSING_MOCK, found[1][2])
        self.assertNotIn("optional_dep", found[1][2])

    def test_output_cut_off_before_the_footer_keeps_the_rest(self):
        [(_, _, detail)] = test_setup.failure_details(f"{SEPARATOR}\nFAIL: test_x (tests.test_a.A.test_x)\n"
                                                      f"{RULE}\n{ASSERTION}")
        self.assertIn(ASSERTION, detail)


# Test modules run with stock `python -m unittest -v`.
STOCK_MODULES = {
    # Prints a caught ModuleNotFoundError traceback, then fails a real assertion.
    "tests/test_prints_import.py": '''
        import traceback
        import unittest

        class A(unittest.TestCase):
            def test_1_prints_a_caught_import_error(self):
                try:
                    import optional_dep_545  # noqa: F401
                except ImportError:
                    print(traceback.format_exc())

            def test_2_real_failure(self):
                self.assertEqual(1, 2)
    ''',
    # Prints a caught application error traceback, then cannot prepare its mock.
    "tests/test_prints_app_error.py": '''
        import traceback
        import unittest
        from unittest import mock
        import app

        class B(unittest.TestCase):
            def test_1_prints_a_caught_app_error(self):
                try:
                    app.work()
                except ValueError:
                    print(traceback.format_exc())

            def test_2_missing_mock(self):
                with mock.patch("app.gone"):
                    pass
    ''',
    # Runs another unittest module and quotes its whole output, footer included, in its failure message.
    "tests/test_quotes_a_run.py": '''
        import subprocess
        import sys
        import unittest

        class O(unittest.TestCase):
            def test_quotes_a_run(self):
                run = subprocess.run([sys.executable, "-m", "unittest", "-v", "tests.inner_run"],
                                     capture_output=True, text=True)
                self.assertIn("expected banner", run.stdout, run.stderr + run.stdout)
    ''',
    # The quoted run: it passes, after logging a caught import error and printing a caught application error.
    "tests/inner_run.py": '''
        import logging
        import traceback
        import unittest
        import app

        class C(unittest.TestCase):
            def test_y(self):
                try:
                    import optional_nested_545  # noqa: F401
                except ImportError:
                    logging.warning("optional dependency missing", exc_info=True)
                try:
                    app.work()
                except ValueError:
                    print(traceback.format_exc())
    ''',
}


# The application frame of a traceback a test caught and printed, at the start of its line.
APP_FRAME = re.compile(r'^\s*File "[^"\n]*app\.py", line \d+, in work$', re.M)


class StockUnittestOutputTests(unittest.TestCase):
    """Real `python -m unittest -v` output captured the way AutoCode captures a test command (stdout and
    stderr into one file, Python's default buffering), read by the verifier."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="test-setup-545-")
        cls.root = Path(cls.tmp.name).resolve()
        (cls.root / "tests").mkdir()
        (cls.root / "tests" / "__init__.py").write_text("")
        (cls.root / "app.py").write_text("def work():\n    raise ValueError('x')\n")
        for path, body in STOCK_MODULES.items():
            (cls.root / path).write_text(textwrap.dedent(body))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_module(self, module):
        log = self.root / f"{module}.log"
        environment = {name: value for name, value in os.environ.items() if name != "PYTHONUNBUFFERED"}
        with log.open("w") as handle:
            subprocess.run([sys.executable, "-m", "unittest", "-v", module], cwd=self.root, env=environment,
                           stdout=handle, stderr=subprocess.STDOUT, check=False)
        results = verify.per_test_results(verify.Framework("unittest", f"python -m unittest -v {module}"),
                                          {"output": str(log)}, self.root / f"{module}.junit.xml", tree=str(self.root))
        return log.read_text(), results

    def test_output_printed_after_the_footer_is_not_part_of_the_last_traceback(self):
        # A test's print() to a block-buffered stdout reaches the file when the interpreter exits, after
        # unittest's footer on stderr. Read as part of the last traceback, the caught import error made a
        # real assertion failure "could not import its dependency on base".
        output, results = self.run_module("tests.test_prints_import")
        self.assertLess(output.index("FAILED (failures=1)"), output.index("optional_dep_545"))
        self.assertEqual(["tests.test_prints_import.A.test_2_real_failure"], results["failed"])
        self.assertIsNone(results.get("setup_errors"))
        # And the caught application error's frame hid a missing mock, which then counted as a reproduction.
        output, results = self.run_module("tests.test_prints_app_error")
        self.assertLess(output.index("FAILED (errors=1)"), APP_FRAME.search(output).start())
        self.assertEqual({"tests.test_prints_app_error.B.test_2_missing_mock":
                          "The test could not prepare its mock: target 'gone' is absent on base"},
                         results.get("setup_errors"))

    def test_a_passing_run_quoted_in_a_failure_message_does_not_end_its_traceback(self):
        # The quoted run's footer says OK, so it cannot close a report with a failure in it. Ending the
        # traceback there would drop the application frame quoted after it and leave only the quoted
        # import error: a real failure would read as "could not import its dependency on base".
        output, results = self.run_module("tests.test_quotes_a_run")
        quoted_footer, footer = (found.start() for found in re.finditer(r"^Ran 1 test in ", output, re.M))
        self.assertLess(output.index("ModuleNotFoundError: No module named 'optional_nested_545'"), quoted_footer)
        self.assertTrue(quoted_footer < APP_FRAME.search(output).start() < footer)
        self.assertEqual(["tests.test_quotes_a_run.O.test_quotes_a_run"], results["failed"])
        self.assertIsNone(results.get("setup_errors"))


if __name__ == "__main__":
    unittest.main()
