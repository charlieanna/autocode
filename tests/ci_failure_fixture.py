"""Unittest output used by CI failure-attribution regressions."""


def failure_output(root, message="old failure", method="test_value", line=12):
    return (
        "=" * 70
        + f"\nFAIL: {method} (tests.test_value.ValueTests)\n"
        + "-" * 70
        + f'\nTraceback (most recent call last):\n  File "{root}/tests/test_value.py", line {line}, in {method}\n'
        + f"    self.fail({message!r})\nAssertionError: {message}\n\n"
        + "-" * 70
        + "\nRan 1 test in 0.01s\n\nFAILED (failures=1)\n"
    )


def assertion_output(root, code, exception, *, kind="FAIL", line=12):
    """One failure of tests.test_runs.RunTests.test_status as unittest prints it, ending in `exception`."""
    return (
        "=" * 70
        + f"\n{kind}: test_status (tests.test_runs.RunTests)\n"
        + "-" * 70
        + f'\nTraceback (most recent call last):\n  File "{root}/tests/test_runs.py", line {line}, in test_status\n'
        + f"    {code}\n{exception}\n\n"
        + "-" * 70
        + "\nRan 1 test in 0.01s\n\n"
        + ("FAILED (failures=1)\n" if kind == "FAIL" else "FAILED (errors=1)\n")
    )


TEMP_HEAD = " : workspace /tmp/design-cli-viqcq92m/project"
TEMP_BASE = " : workspace /tmp/design-cli-k3x9p2ab/project"
RUN_HEAD = " : run 20261118-104611-build-a-cli"
RUN_BASE = " : run 20261119-091502-build-a-cli"
ID_HEAD = " : id 3f2b8c1e-9d4a-4e6f-8a7b-1c2d3e4f5a6b"
ID_BASE = " : id 9a8b7c6d-5e4f-4a3b-9c2d-1e0f2a3b4c5d"

STRING_CODE = 'self.assertEqual(status, "PAUSED_INTERVENTION", reason)'
STRING_DIFF = (
    "AssertionError: 'PAUSED_JOB_FAILURE' != 'PAUSED_INTERVENTION'\n- PAUSED_JOB_FAILURE\n+ PAUSED_INTERVENTION\n"
)
LIST_CODE = "self.assertEqual(names, expected, where)"
LIST_DIFF = "AssertionError: Lists differ: ['aaa'] != ['zzz']\n\nFirst differing element 0:\n'aaa'\n'zzz'\n\n- ['aaa']\n+ ['zzz']"
SET_CODE = "self.assertEqual(seen, expected, ident)"
SET_DIFF = (
    "AssertionError: Items in the first set but not the second:\n1\nItems in the second set but not the first:\n2"
)
COUNT_CODE = "self.assertCountEqual(seen, expected, ident)"
COUNT_DIFF = (
    "AssertionError: Element counts were not equal:\nFirst has 1, Second has 0:  1\nFirst has 0, Second has 1:  2"
)

# (code line, exception text read with root /head, exception text read with root /base): one failure each,
# differing only by the volatile message unittest appended to what the assertion compared.
VOLATILE_PAIRS = (
    (STRING_CODE, STRING_DIFF + TEMP_HEAD, STRING_DIFF + TEMP_BASE),
    ("self.assertEqual(exit_code, 2, name)", "AssertionError: 1 != 2" + RUN_HEAD, "AssertionError: 1 != 2" + RUN_BASE),
    (
        "self.assertTrue(found, ident)",
        "AssertionError: False is not true" + ID_HEAD,
        "AssertionError: False is not true" + ID_BASE,
    ),
    (
        "self.assertIn(part, text, name)",
        "AssertionError: 'a : b' not found in 'xyz'" + RUN_HEAD,
        "AssertionError: 'a : b' not found in 'xyz'" + RUN_BASE,
    ),
    (LIST_CODE, LIST_DIFF + TEMP_HEAD, LIST_DIFF + TEMP_BASE),
    (SET_CODE, SET_DIFF + ID_HEAD, SET_DIFF + ID_BASE),
    (COUNT_CODE, COUNT_DIFF + ID_HEAD, COUNT_DIFF + ID_BASE),
)


def ref_failure_output(root, reference, *, method="test_value", line=12):
    """A constructed ref appears in the diagnostic, not as a literal in the assertion's source."""
    message = f"False is not true : {reference}"
    return failure_output(root, message, method, line).replace(
        f"    self.fail({message!r})", "    self.assertTrue(allowed, ref)"
    )
