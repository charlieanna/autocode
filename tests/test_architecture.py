"""Guard import cycles and shared-helper dependency isolation. See AGENTS.md."""
import ast
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"

# Modules in an import cycle: they import, directly or through other modules, a module that
# imports them back. On 2026-09-26 34 modules were caught in one cycle through autocode.py.
# 2026-09-29: the shared helpers moved to autocode_util, and autopilot stopped importing the CLI
# (only its script entry does), which freed autocode.py and the controller; the goal lifecycle
# moved out of autocode_goals, which freed goals and five modules that only read a contract; the
# completion gate and stage context moved out of autocode_support, which freed support and four more.
# Taking a module out of the cycles is progress (remove it here); adding one fails.
TANGLED = frozenset({
    "autocode_findings", "autocode_goal_lifecycle", "autocode_milestones", "autocode_planning",
    "autocode_stage_context", "autocode_workflow", "live_scenarios", "live_token_sampler",
    "score_autocode_run", "task_scenarios", "units.autoplanner", "units.common",
})

# Line counts on 2026-09-28, after merging master at 24617cc and moving subcommand dispatch out of
# autocode.py. Lower these when a module shrinks.
# 2026-10-01: the durable-intervention application policy (metadata, consume,
# boundary effects) moved to autocode_stop, shrinking autocode.py further.
MAX_LINES = {"autocode.py": 1491, "autocode_goals.py": 1375, "autocode_support.py": 517, "autopilot.py": 1176}


def source_modules() -> dict[str, Path]:
    modules = {}
    for path in TOOLS.rglob("*.py"):
        relative = path.relative_to(TOOLS)
        if path.name.startswith("test_") or "tests" in relative.parts or "__pycache__" in relative.parts:
            continue
        modules[".".join(relative.with_suffix("").parts)] = path
    return modules


def script_entry(node: ast.stmt) -> bool:
    """`if __name__ == "__main__":`, which runs only when the file is executed as a program."""
    test = node.test if isinstance(node, ast.If) else None
    return (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) and test.left.id == "__name__"
            and len(test.comparators) == 1 and getattr(test.comparators[0], "value", None) == "__main__")


def import_graph() -> dict[str, set[str]]:
    """Internal imports each module makes when it is imported, including inside functions.

    A script entry (`if __name__ == "__main__":`) is left out: it runs only when the file is executed,
    so what it imports is not a dependency of the module."""
    modules = source_modules()
    graph = {}
    for name, path in modules.items():
        package = name.split(".")[:-1]
        targets = set()
        tree = ast.parse(path.read_text())
        entry = {id(node) for block in tree.body if script_entry(block) for node in ast.walk(block)}
        for node in ast.walk(tree):
            if id(node) in entry:
                continue
            if isinstance(node, ast.Import):
                candidates = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = package[:len(package) - node.level + 1] if node.level else []
                module = ".".join(base + ([node.module.removeprefix("autocode_cli.")] if node.module else []))
                candidates = [module] + [f"{module}.{alias.name}".lstrip(".") for alias in node.names]
            else:
                continue
            targets.update(candidate for candidate in candidates if candidate in modules and candidate != name)
        graph[name] = targets
    return graph


def reachable(graph: dict[str, set[str]], origins) -> set[str]:
    seen, todo = set(), list(origins)
    while todo:
        node = todo.pop()
        if node not in seen:
            seen.add(node)
            todo.extend(graph.get(node, ()))
    return seen


def modules_in_cycles(graph: dict[str, set[str]]) -> set[str]:
    """Every module that imports itself back, directly or through other modules."""
    return {node for node, targets in graph.items() if node in reachable(graph, targets)}


class ArchitectureTests(unittest.TestCase):
    def test_no_module_joins_an_import_cycle(self):
        tangled = modules_in_cycles(import_graph())
        self.assertFalse(tangled - TANGLED, "these modules now import, directly or indirectly, a module that imports "
                         f"them back; depend on lower-level modules instead: {sorted(tangled - TANGLED)}")

    def test_a_module_that_left_the_cycles_is_taken_off_the_list(self):
        left = TANGLED - modules_in_cycles(import_graph())
        self.assertFalse(left, f"progress: these modules are no longer in an import cycle; remove them from TANGLED: "
                         f"{sorted(left)}")

    def test_largest_modules_do_not_grow(self):
        for name, limit in MAX_LINES.items():
            with self.subTest(module=name):
                lines = len((TOOLS / name).read_text().splitlines())
                self.assertLessEqual(lines, limit, f"{name} grew to {lines} lines (limit {limit}); "
                                     "move behavior into a lower-level module instead")

    def test_the_shared_helpers_import_nothing_from_autocode(self):
        # autocode_util is the bottom layer; one AutoCode import would drag its 18 users back into the cycle.
        self.assertEqual(set(), import_graph()["autocode_util"])


if __name__ == "__main__":
    unittest.main()
