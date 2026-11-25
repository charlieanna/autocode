"""A scenario test's time budget leaves room for a slow CI runner (#4167's CI, 2026-11-25).

A ``Driver`` budget bounds a hang (``max_steps`` bounds a loop), so it decides nothing about what passes.
Sized from an idle machine, it measures the runner instead: the progressive CLI tests had 180 s, took
100-175 s on ordinary runners and failed three at a time on one that ran every test about 1.5x as long.
Each budget of a test that drives the real CLI must stay at least ``MARGIN`` times the slowest CI duration
recorded for a test in its module (``tests/suite_durations.json``, refreshed by ``tools/suite_durations.py``).
"""

import ast
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DURATIONS = REPO / "tests" / "suite_durations.json"
MARGIN = 2


def budgets(source: str) -> list[tuple[int, object]]:
    """(line, seconds) for each ``Driver(..., autocode=default_autocode(), timeout_seconds=...)`` call.

    The seconds are an int, resolved through a module-level ``NAME = <int>``, or the unresolved expression's
    source when neither applies (a budget computed at run time, which this check cannot read)."""
    tree = ast.parse(source)
    constants = {
        node.targets[0].id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, int)
    }
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Driver"):
            continue
        keywords = {keyword.arg: keyword.value for keyword in node.keywords}
        autocode = keywords.get("autocode")
        if not (
            isinstance(autocode, ast.Call)
            and isinstance(autocode.func, ast.Name)
            and autocode.func.id == "default_autocode"
        ):
            continue  # a stub CLI, whose budget times nothing real
        budget = keywords.get("timeout_seconds")
        if isinstance(budget, ast.Constant) and isinstance(budget.value, int):
            found.append((node.lineno, budget.value))
        elif isinstance(budget, ast.Name) and budget.id in constants:
            found.append((node.lineno, constants[budget.id]))
        elif budget is not None:
            found.append((node.lineno, ast.unparse(budget)))
    return found


def tight(module: str, source: str, durations: dict[str, float]) -> list[str]:
    """Each real-CLI budget in ``module`` below ``MARGIN`` times its slowest recorded test."""
    recorded = [seconds for test, seconds in durations.items() if test.startswith(module + ".")]
    if not recorded:
        return []
    slowest = max(recorded)
    return [
        f"{module}:{line}: budget {seconds} s, slowest recorded test {slowest:g} s"
        for line, seconds in budgets(source)
        if isinstance(seconds, int) and seconds < MARGIN * slowest
    ]


class ScenarioBudgetTests(unittest.TestCase):
    def test_real_cli_budgets_leave_room_for_a_slow_runner(self):
        durations = json.loads(DURATIONS.read_text())["seconds"]
        found, checked = [], []
        for path in sorted((REPO / "scenarios").rglob("test_*.py")):
            module = ".".join(path.relative_to(REPO).with_suffix("").parts)
            source = path.read_text()
            if budgets(source) and any(test.startswith(module + ".") for test in durations):
                checked.append(module)
            found += tight(module, source, durations)
        self.assertIn("scenarios.catalog.progressive-learning-journey.test_cli", checked)
        self.assertEqual([], found, f"give each Driver at least {MARGIN}x its module's slowest recorded test")

    def test_the_check_reads_literal_and_named_budgets_of_real_cli_drivers_only(self):
        source = (
            "BUDGET = 720\n"
            "def test(self):\n"
            "    Driver(p, r, f, e, autocode=default_autocode(), max_steps=60, timeout_seconds=180)\n"
            "    Driver(p, r, f, e, autocode=default_autocode(), max_steps=60, timeout_seconds=BUDGET)\n"
            "    Driver(p, r, f, e, autocode=['cli'], max_steps=5, timeout_seconds=60)\n"
        )
        self.assertEqual([(3, 180), (4, 720)], budgets(source))
        durations = {"scenarios.x.Tests.test_a": 130.7, "scenarios.xy.Tests.test_b": 900.0}
        self.assertEqual(
            ["scenarios.x:3: budget 180 s, slowest recorded test 130.7 s"], tight("scenarios.x", source, durations)
        )
        self.assertEqual([], tight("scenarios.unrecorded", source, durations))


if __name__ == "__main__":
    unittest.main()
