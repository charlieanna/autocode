"""Recognize test preparation failures that cannot prove an application bug.

Use the failing test's traceback, never the exception name alone. A product
AttributeError, a direct assertion about a public attribute, or an ambiguous
traceback stays eligible for ordinary proof/reviewer checks. This deliberately
recognizes only explicit missing mock targets and test-origin import errors.
It does not claim to classify every possible test framework or helper.
"""
from __future__ import annotations

import re
from pathlib import Path

UNITTEST_FAILURE = re.compile(r"^(?:FAIL|ERROR): (\S+) \(([\w.]+)\).*$", re.M)
PYTHON_FRAME = re.compile(r'^\s*File "([^"\n]+)", line \d+, in ([^\n]+)$', re.M)
PYTEST_FRAME = re.compile(r"^([^\n]+\.py):\d+:(?: in ([^\n]+))?[^\n]*$", re.M)
MISSING_MOCK = re.compile(r"^(?:E\s+)?AttributeError: .* does not have the attribute ('[^'\n]+')\s*$", re.M)
IMPORT_ERROR = re.compile(r"^(?:E\s+)?(?:ImportError|ModuleNotFoundError): ([^\n]+)$", re.M)


def failure_details(output):
    """Yield unittest failure owners and their separate traceback sections."""
    headers = list(UNITTEST_FAILURE.finditer(output))
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(output)
        yield (*header.groups(), output[header.end():end])


def setup_error(traceback, tree, is_test_path):
    """Explain a recognized setup-only failure; return None for anything ambiguous.

    ``tree`` is the scratch root and ``is_test_path`` is the caller's existing
    test-file classifier. Every frame must be a test or unittest.mock; unknown
    helpers and product frames are intentionally not classified as setup.
    """
    if tree is None or "During handling of the above exception" in traceback or "direct cause" in traceback:
        return None
    frames = PYTHON_FRAME.findall(traceback) or PYTEST_FRAME.findall(traceback)
    if not frames:
        return None
    root = Path(tree).resolve()
    kinds = []
    for filename, function in frames:
        path = Path(filename)
        path = (path if path.is_absolute() else root / path).resolve()
        if path.is_relative_to(root):
            if not is_test_path(path.relative_to(root).as_posix()):
                return None  # The exception passed through application code.
            kinds.append("test")
        elif path.as_posix().endswith("/unittest/mock.py"):
            kinds.append("mock")
        else:
            return None
    if "test" not in kinds:
        return None
    missing = MISSING_MOCK.findall(traceback)
    if kinds[-1] == "mock" and len(missing) == 1:
        return f"The test could not prepare its mock: target {missing[0]} is absent on base"
    imports = IMPORT_ERROR.findall(traceback)
    if kinds[-1] == "test" and len(imports) == 1:
        return "The test could not import its dependency on base: " + imports[0]
    return None


def proof_note(errors):
    """Describe exactly which named failures are excluded from bug-fix proof."""
    return ("Not counted as bug reproduction: " + "; ".join(f"{test}: {reason}" for test, reason in sorted(errors.items()))
            + ". These failures occur while preparing the test, before application code runs. "
            "Exercise the existing public API and assert the application's behavior. "
            "If that requires a new hook, request a separately approved instrumentation-only base patch.")
