"""The Builder probe's requests and its mechanical grader (#3630 step 3).

Five small tasks on the bundled project (``fixtures/requirements_probe``), each with tests written in
advance that the run never sees. A Builder probe request is an ordinary task run to its end with
only the Builder set to the model being measured; the Planner, the Plan Reviewer, the Tester and the
Completion Reviewer are the routes the person names, the same for every candidate. When the run
stops, the probe runs the held-back tests and the project's own tests on what the run left, and
``grade`` reads those results. No model judges another:

- the run completed (``TASK_COMPLETE``);
- every held-back test passes;
- the project's own tests pass;
- no file the request forbids changed;
- the Builder's own time stayed within ``TIME_LIMIT_SECONDS``.

The number of Builder attempts (a send-back by the Tester or the Completion Reviewer is another
attempt) is recorded with the grade and shown, but is not a check: the checkers are the person's
routes, not the candidate.

B2 runs on the project as bundled, whose tax bug makes one test fail. The others run on a copy with
that bug fixed (``planner_probe.FIXED_TAX``), so the existing tests pass before the change.

Pure: no model call, no file access.
"""

from __future__ import annotations

from dataclasses import dataclass, field

try:
    from . import autocode_requirements_probe as requirements_probe
except ImportError:
    import autocode_requirements_probe as requirements_probe

# The Builder's own attempts, summed. A five-line fix by the slowest model the Planner probe measured
# took minutes, not tens of minutes; a Builder past this is a failure, not a slow pass.
TIME_LIMIT_SECONDS = 1800
PROJECT_TESTS = "test_totals.py"


@dataclass(frozen=True)
class Request:
    id: str
    text: str
    # Held-back tests: one unittest module, run with the project on the import path and the project
    # folder as the working directory.
    tests: str
    must_not_change: frozenset = field(default_factory=frozenset)
    # True: the request runs on the project as bundled, with the tax bug.
    broken_tax: bool = False
    why: str = ""


_REPORT = """
import os
import subprocess
import sys
import tempfile
import unittest


def report(*args, lines):
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as handle:
        handle.write(lines)
    try:
        return subprocess.run(
            [sys.executable, "report.py", *[handle.name if a == "ORDERS" else a for a in args]],
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        os.unlink(handle.name)
"""

REQUESTS = (
    Request(
        "B1",
        "average([]) in totals.py raises ZeroDivisionError; it should return 0.0",
        """
import unittest

import totals


class Average(unittest.TestCase):
    def test_empty_is_zero(self):
        self.assertEqual(totals.average([]), 0.0)

    def test_values_are_unchanged(self):
        self.assertEqual(totals.average([2, 4]), 3)
""",
        why="an exact bug fix",
    ),
    Request(
        "B2",
        "fix the failing test",
        """
import unittest

import totals


class Tax(unittest.TestCase):
    def test_tax_is_added_to_the_total(self):
        self.assertAlmostEqual(totals.total_with_tax([100]), 108)
        self.assertAlmostEqual(totals.total_with_tax([50, 50]), 108)

    def test_nothing_costs_nothing(self):
        self.assertEqual(totals.total_with_tax([]), 0)
""",
        must_not_change=frozenset({PROJECT_TESTS}),
        broken_tax=True,
        why="the bug is in total_with_tax, not in the test",
    ),
    Request(
        "B3",
        "add a --json option to the report command: `python report.py --json ORDERS` prints one JSON object "
        "with the keys total, average and with_tax, as numbers, instead of the three lines",
        _REPORT
        + """

class Json(unittest.TestCase):
    def test_json_prints_one_object(self):
        import json

        result = report("--json", "ORDERS", lines="1\\n2\\n3\\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        printed = json.loads(result.stdout)
        self.assertEqual(set(printed), {"total", "average", "with_tax"})
        self.assertAlmostEqual(printed["total"], 6)
        self.assertAlmostEqual(printed["average"], 2)
        self.assertAlmostEqual(printed["with_tax"], 6.48)

    def test_without_it_the_lines_are_unchanged(self):
        result = report("ORDERS", lines="1\\n2\\n3\\n")
        self.assertEqual(result.stdout, "total: 6.00\\naverage: 2.00\\nwith tax: 6.48\\n")
""",
        why="an exact feature in one command",
    ),
    Request(
        "B4",
        "orders files may contain comments (lines starting with #) and blank lines, which the report command "
        "skips; a line that is not a number makes it print `line N: not a number` to stderr, N being its "
        "line number counted from 1, and exit with status 1",
        _REPORT
        + """

class Lines(unittest.TestCase):
    def test_comments_and_blank_lines_are_skipped(self):
        result = report("ORDERS", lines="# orders\\n1\\n\\n  # more\\n2\\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("total: 3.00", result.stdout)

    def test_a_malformed_line_is_named(self):
        result = report("ORDERS", lines="1\\nabc\\n2\\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("line 2: not a number", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
""",
        why="edge cases and an exact error",
    ),
    Request(
        "B5",
        "make the report command pick up changes to the orders file instead of serving a cached result for "
        "up to an hour",
        """
import contextlib
import io
import os
import tempfile
import unittest

import report


class Fresh(unittest.TestCase):
    def test_a_changed_file_is_read_again(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "orders.txt")
            printed = []
            for amounts, stamp in (("1\\n2\\n", 1_000_000_000), ("10\\n20\\n", 1_000_000_100)):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(amounts)
                os.utime(path, (stamp, stamp))
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    report.main(["report.py", path])
                printed.append(out.getvalue())
        self.assertIn("total: 3.00", printed[0])
        self.assertIn("total: 30.00", printed[1])
""",
        why="a design choice: notice the change, not a shorter lifetime",
    ),
)
BY_ID = {request.id: request for request in REQUESTS}
# An exact fix and an exact feature: a Builder that cannot do these is not measured further.
QUICK = ("B1", "B3")

CHECKS = ("completed", "held_back_tests_pass", "project_tests_pass", "keeps_forbidden_files", "time")


def grade(
    request,
    *,
    run_status,
    held_back,
    project_tests,
    changed_files=(),
    builder_attempts=None,
    seconds=None,
    cost_usd=None,
):
    """``{"request", "checks": {name: bool}, "passed", "failed": [names], measurements}``.

    ``held_back`` and ``project_tests`` are the test results the probe read after the run: ``{"ok":
    bool, "ran": int, "output": str}``, or None when they could not run. ``changed_files`` are the
    project's files the run changed. ``seconds`` and ``cost_usd`` are the Builder's own.
    """
    if isinstance(request, str):
        request = BY_ID[request]
    changed = {str(name) for name in changed_files or ()}
    checks = {
        "completed": run_status == "TASK_COMPLETE",
        "held_back_tests_pass": _passed(held_back),
        "project_tests_pass": _passed(project_tests),
        "keeps_forbidden_files": not (request.must_not_change & changed),
        "time": isinstance(seconds, (int, float)) and seconds <= TIME_LIMIT_SECONDS,
    }
    failed = [name for name in CHECKS if not checks[name]]
    return {
        "request": request.id,
        "checks": checks,
        "passed": not failed,
        "failed": failed,
        "changed_files": sorted(changed),
        "builder_attempts": builder_attempts,
        "held_back_output": _tail(held_back),
        "seconds": seconds,
        "cost_usd": cost_usd,
    }


def summary(grades):
    """The same summary the Requirements probe gives, so one ranking reads both."""
    return requirements_probe.summary(grades)


def passes_quick(grades):
    passed = {g["request"] for g in grades if g.get("passed")}
    return all(request in passed for request in QUICK)


def _passed(result):
    return (
        isinstance(result, dict) and result.get("ok") is True and type(result.get("ran")) is int and result["ran"] > 0
    )


def _tail(result):
    output = result.get("output") if isinstance(result, dict) else None
    return output[-2000:] if isinstance(output, str) and not _passed(result) else None
