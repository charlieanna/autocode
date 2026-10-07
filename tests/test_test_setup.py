"""Where a unittest failure's traceback ends in a test command's output (autocode_test_setup).

AutoCode's verifier reads each failure's traceback from the command's output to tell a test that could
not prepare itself (a missing mock target, a test-origin import error) from a real failure. A runner that
prints several modules' reports, as tools/run_suite.py does, puts other modules' output after one module's
closing "Ran N tests" footer; none of it belongs to that module's last traceback (#545).
"""
from __future__ import annotations

import unittest

import autocode_test_setup as test_setup

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


if __name__ == "__main__":
    unittest.main()
