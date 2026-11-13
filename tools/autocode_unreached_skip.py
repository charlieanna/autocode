"""A failing acceptance test whose skip it never reached (#3333).

The runner accepts a ``test:`` case when its test fails on today's code. A live run on macOS, 2026-11-12,
had a test that failed with ``self.fail`` when the function it tested was missing and only then called
``self.skipTest`` when the host had no ``/proc``. It failed today, so it was accepted, but once the Builder
added the function it would skip on that host, and a skipped test cannot show the change works: the run
would reach a regression proof that cannot pass. The skip was below the line that failed, so today's run
never reached it.

``skip_below_failure`` names such a skip: a skip call in the test's own body (``self.skipTest``,
``pytest.skip``, ``raise SkipTest``, ``pytest.importorskip``) on a line after the one in that test
where today's failure was raised. A skip the test reached today, above its failure, did not skip here,
and a skip decorator was already evaluated here, so neither is named. Python tests only; anything this
cannot read (no Python source, no failure line in the runner's output) names nothing.

Pure, standard library only; it imports nothing from AutoCode.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SKIP_CALL = re.compile(
    r"\bskipTest\s*\(|\bpytest\s*\.\s*(?:skip|importorskip)\s*\(|\braise\s+(?:\w+\s*\.\s*)*SkipTest\b"
)
# unittest: `File ".../tests/test_x.py", line 12, in test_ac1`; pytest: `tests/test_x.py:12: AssertionError`.
_UNITTEST_FRAME = re.compile(r'^\s*File "([^"]+)", line (\d+), in (\w+)', re.M)
_PYTEST_FRAME = re.compile(r"^([^\s:]+\.py):(\d+): ", re.M)


def _function(source: str, test_name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == test_name:
            return node
    return None


def _failure_line(file: str, test_name: str, tail: str, start: int, end: int) -> int | None:
    """The last line inside the test's body that today's failure passed through, from the runner's output."""
    name = Path(file).name
    lines = [
        int(line)
        for path, line, function in _UNITTEST_FRAME.findall(tail)
        if Path(path).name == name and function == test_name
    ]
    lines += [int(line) for path, line in _PYTEST_FRAME.findall(tail) if Path(path).name == name]
    inside = [line for line in lines if start <= line <= end]
    return inside[-1] if inside else None


def skip_below_failure(source: str, file: str, test_name: str, tail: str) -> str:
    """The skip call today's failure kept the test from reaching, as ``line N: text``; "" when there is none."""
    if not str(file).endswith(".py"):
        return ""
    node = _function(source, test_name)
    if node is None or node.end_lineno is None:
        return ""
    body_start = node.body[0].lineno if node.body else node.lineno
    failed_at = _failure_line(file, test_name, tail, body_start, node.end_lineno)
    if failed_at is None:
        return ""
    lines = source.splitlines()
    for number in range(failed_at + 1, node.end_lineno + 1):
        text = lines[number - 1] if number - 1 < len(lines) else ""
        if SKIP_CALL.search(text.split("#", 1)[0]):
            return f"line {number}: {text.strip()}"
    return ""
