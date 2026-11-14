"""Per-test results from Jest's own JSON report, so a Jest suite names the tests it ran.

A Jest run gave the proof only an exit code. "pageCount(0, 5) should return 1. Update the tests to match"
(Claude models, 2026-11-13) changed one existing test with the code: the original tests, run against the
change, failed as a whole, nothing could say that the one failing test was the one the plan replaced, and the
Resolver asked the person to approve a hand-built contract revision. The same request in a Python or
node:test project completes, because the runner names each test (autocode_superseded_correction). A plan's
``test:`` and ``guard:`` criteria could not name a Jest test either.

Jest ships a JSON report. ``instrument`` adds ``--json --outputFile`` to a plain Jest command, or to an
``npm test`` whose script is one; the usual output stays on the terminal. ``results`` reads that file.
A test's id is ``FILE::describe > title``, FILE relative to where Jest ran, as a Vitest id is. Jest lets
two tests share a name (``test.each`` without a placeholder): they are one id, failed when any of them
failed. A file that could not be loaded runs no test and is reported as ``FILE::[collection]``, failed and
uncollected; a file whose tests ran and whose own setup or teardown failed is the same id, failed. Counts
that disagree with the report's own, an interrupted run, or a file outside the tree give no results at all.

AutoCode never installs a tool into a project, and this needs none. tests/test_jest_tests.py
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

try:
    from . import autocode_npm_scripts as npm_scripts
except ImportError:
    import autocode_npm_scripts as npm_scripts

COLLECTION = "::[collection]"
RUNTIME = "[jest runtime error]"
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
# Options that write the report elsewhere, never end, run no test, or choose tests from Git or a cache:
# such a command is left as written and proves by its exit code, as before.
_OWNED = frozenset(
    {
        "json",
        "outputfile",
        "watch",
        "watchall",
        "listtests",
        "showconfig",
        "init",
        "clearcache",
        "onlychanged",
        "changedsince",
        "lastcommit",
        "onlyfailures",
        "help",
        "version",
    }
)
_STATES = {
    "passed": "passed",
    "failed": "failed",
    "pending": "skipped",
    "skipped": "skipped",
    "todo": "skipped",
    "disabled": "skipped",
}


def _jest(words) -> bool:
    """Whether ``words`` are one Jest run whose report the runner can own."""
    words = list(words or [])
    if words and Path(words[0]).name == "cross-env":
        words = words[1:]
    while words and _ASSIGNMENT.match(words[0]):
        if shlex.quote(words[0]) != words[0]:
            return False  # a quoted assignment would not be rejoined as one
        words = words[1:]
    if not words:
        return False
    first = Path(words[0]).name
    if first in ("jest", "jest.cmd"):
        options = words[1:]
    elif first in ("node", "node.exe") and len(words) > 1 and Path(words[1]).name in ("jest", "jest.js"):
        options = words[2:]
    elif first in ("npx", "npx.cmd") and words[1:3] == ["--no-install", "jest"]:
        options = words[3:]
    else:
        return False
    return "--" not in options and not any(
        word in ("-o", "-f", "-h", "-v") or word.lstrip("-").split("=", 1)[0].lower().replace("-", "") in _OWNED
        for word in options
        if word.startswith("-")
    )


def command_words(command, tree=None):
    """The words of a plain Jest command, or of an ``npm test`` in ``tree`` whose script is one; else None."""
    words = npm_scripts.words(command)
    if _jest(words):
        return words
    found = npm_scripts.test_script(command, tree)
    script = npm_scripts.words(found[0].get("test")) if found else None
    if script is None or not _jest([*script, *found[1]]):
        return None
    return words


def instrument(command, result_path, tree=None):
    """``command`` with Jest's JSON report written to ``result_path``; another command is returned as written."""
    words = command_words(command, tree)
    if not words:
        return command
    if Path(words[0]).name in ("npm", "npm.cmd") and "--" not in words:
        words = [*words, "--"]
    return shlex.join([*words, "--json", "--outputFile=" + str(Path(result_path).resolve())])


def _relative(name, root):
    source = Path(name)
    if not isinstance(name, str) or not name or not source.is_absolute():
        return None
    source = source.resolve()
    return source.relative_to(root).as_posix() if source.is_relative_to(root) else None


def results(path, tree):
    """Complete per-test results from one Jest JSON report run in ``tree``, or None for absent or ambiguous
    evidence."""
    try:
        report = json.loads(Path(path).read_text())
        files = report["testResults"]
        if tree is None or report.get("wasInterrupted") or not isinstance(files, list):
            return None
        root = Path(tree).resolve()
        seen: dict[str, set[str]] = {}
        collection, uncollected, names = set(), set(), set()
        ran = failures = 0
        for file in files:
            relative, tests = _relative(file["name"], root), file["assertionResults"]
            if relative is None or relative in names or not isinstance(tests, list):
                return None
            if file["status"] not in ("passed", "failed"):
                return None
            names.add(relative)
            failed_here = False
            for test in tests:
                title, groups, state = test["title"], test["ancestorTitles"], _STATES.get(test["status"])
                if not isinstance(title, str) or not title or not isinstance(groups, list) or state is None:
                    return None
                if not all(isinstance(group, str) for group in groups):
                    return None
                seen.setdefault(relative + "::" + " > ".join([*groups, title]), set()).add(state)
                ran += 1
                failures += state == "failed"
                failed_here = failed_here or state == "failed"
            if file["status"] == "failed" and not failed_here:
                # The file itself failed: it did not load, or its own setup or teardown threw.
                collection.add(relative + COLLECTION)
                if not tests:
                    uncollected.add(relative + COLLECTION)
        if report["numTotalTests"] != ran or report["numFailedTests"] != failures:
            return None
        if report["numTotalTestSuites"] != len(files) or type(report["success"]) is not bool:
            return None
        failed = {identity for identity, states in seen.items() if "failed" in states}
        passed = {identity for identity, states in seen.items() if "passed" in states} - failed
        skipped = set(seen) - failed - passed
        if report["success"] and (failed or collection):
            return None
        if not report["success"] and not failed and not collection:
            collection.add(RUNTIME)  # a coverage threshold, a global teardown: no test to attribute it to
        if not seen and not collection:
            return None
        return {
            "passed": sorted(passed),
            "failed": sorted(failed | collection),
            "skipped": sorted(skipped),
            "collection_errors": sorted(collection),
            "uncollected": sorted(uncollected),
            "total": len(set(seen) | collection),
            "complete": True,
        }
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError):
        return None
