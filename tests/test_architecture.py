"""Ratchets that stop the architecture getting worse. See AGENTS.md.

The recorded limits may only go down. If a change needs to raise one, the change
is in the wrong place: put the new behavior in its own module, below the
modules that use it.
"""
import ast
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"

# Modules caught in one import cycle with autocode.py on 2026-09-26, plus those master
# brought in with it on 2026-09-28 (d0b0ce9, 332c318). Taking a module out of the
# cycle is progress (remove it here); adding one fails.
TANGLED = frozenset({
    "autocode", "autocode_baseline", "autocode_builder_policy", "autocode_carryforward",
    "autocode_planning_graph", "autocode_program", "autocode_verify",
    "autocode_planning_artifacts", "autocode_regression", "autocode_resolver_human", "autocode_reviewer_fallback",
    "autocode_context", "autocode_dispatch", "autocode_failures", "autocode_figma",
    "autocode_findings", "autocode_goals", "autocode_interventions", "autocode_milestones",
    "autocode_orchestrator", "autocode_planning", "autocode_registry", "autocode_resolver_runtime",
    "autocode_status", "autocode_support", "autocode_tasks", "autocode_ui", "autocode_workflow",
    "autopilot", "units.autocode", "units.autoplanner", "units.autoresolver", "units.autoreview", "units.common",
})

# Line counts on 2026-09-28, after merging master at 24617cc and moving subcommand dispatch out of
# autocode.py. Lower these when a module shrinks.
MAX_LINES = {"autocode.py": 3820, "autocode_goals.py": 1903, "autocode_support.py": 1077, "autopilot.py": 1194}


def source_modules() -> dict[str, Path]:
    modules = {}
    for path in TOOLS.rglob("*.py"):
        relative = path.relative_to(TOOLS)
        if path.name.startswith("test_") or "tests" in relative.parts or "__pycache__" in relative.parts:
            continue
        modules[".".join(relative.with_suffix("").parts)] = path
    return modules


def import_graph() -> dict[str, set[str]]:
    """Internal imports of each module, including imports made inside functions."""
    modules = source_modules()
    graph = {}
    for name, path in modules.items():
        package = name.split(".")[:-1]
        targets = set()
        for node in ast.walk(ast.parse(path.read_text())):
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


def cycle_containing(graph: dict[str, set[str]], start: str) -> set[str]:
    def reachable(edges, origin):
        seen, todo = set(), [origin]
        while todo:
            node = todo.pop()
            if node not in seen:
                seen.add(node)
                todo.extend(edges.get(node, ()))
        return seen
    reverse = {}
    for node, targets in graph.items():
        for target in targets:
            reverse.setdefault(target, set()).add(node)
    return reachable(graph, start) & reachable(reverse, start)


class ArchitectureTests(unittest.TestCase):
    def test_no_module_joins_the_autocode_import_cycle(self):
        cycle = cycle_containing(import_graph(), "autocode")
        self.assertFalse(cycle - TANGLED, "these modules now import, directly or indirectly, a module that imports "
                         "them back through autocode.py; depend on lower-level modules instead: "
                         f"{sorted(cycle - TANGLED)}")

    def test_largest_modules_do_not_grow(self):
        for name, limit in MAX_LINES.items():
            with self.subTest(module=name):
                lines = len((TOOLS / name).read_text().splitlines())
                self.assertLessEqual(lines, limit, f"{name} grew to {lines} lines (limit {limit}); "
                                     "put new behavior in a focused module instead")


if __name__ == "__main__":
    unittest.main()
