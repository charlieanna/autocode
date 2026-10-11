"""Guard import cycles and shared-helper dependency isolation. See AGENTS.md."""

import ast
import importlib
import tempfile
import unittest
from pathlib import Path

from tests.source_inventory import python_sources

TOOLS = Path(__file__).resolve().parents[1] / "tools"

# Modules in an import cycle: they import, directly or through other modules, a module that
# imports them back. On 2026-09-26 34 modules were caught in one cycle through autocode.py.
# 2026-09-29: the shared helpers moved to autocode_util, and autopilot stopped importing the CLI
# (only its script entry does), which freed autocode.py and the controller; the goal lifecycle
# moved out of autocode_goals, which freed goals and five modules that only read a contract; the
# completion gate and stage context moved out of autocode_support, which freed support and four more.
# 2026-10-04: the scenario verdicts (scenario_verdicts) and the token cost helpers (token_cost) moved
# out of the two live-trial pairs, which freed all four. The milestone scope (autocode_milestone_scope)
# moved out of autocode_milestones, which freed milestones and the findings ledger.
# Taking a module out of the cycles is progress (remove it here); adding one fails.
TANGLED = frozenset(
    {
        "autocode_goal_lifecycle",
        "autocode_planning",
        "autocode_stage_context",
        "autocode_workflow",
        "units.autoplanner",
        "units.common",
    }
)

# Line counts on 2026-09-28, after merging master at 24617cc and moving subcommand dispatch out of
# autocode.py. Lower these when a module shrinks.
# 2026-10-01: the durable-intervention application policy (metadata, consume,
# boundary effects) moved to autocode_stop, shrinking autocode.py further.
# 2026-10-04: the regression proof's prompt notes moved to autocode_regression, which owns the proof.
# 2026-10-06: the closed-terminal output wrappers moved to autocode_detached_output (#454).
# 2026-10-09: joint transport checks moved to autocode_joint_transport (#799).
# 2026-10-09: ruff import sorting expanded compact semicolon imports to individual lines (#803).
# 2026-10-09: ruff format pass expanded compact one-liners into multi-line style (~30% line growth).
MAX_LINES = {"autocode.py": 2050, "autocode_goals.py": 1595, "autocode_support.py": 620, "autopilot.py": 1660}


# Names used or patched through the compatibility shims by active consumers (#845).
# Keep these explicit: deriving them from current exports would hide a removed import.
COMPATIBILITY_EXPORTS = {
    "autocode_opencode": (
        "providers.opencode",
        (
            "DEFAULT_MODELS",
            "check_models",
            "check_subscription_routes",
            "final_report",
            "incomplete_response",
            "launch",
            "local_settings",
            "normalized_events",
            "os",
            "parse_opencode_version",
            "prompt_for_schema",
            "raw_events",
            "shutil",
            "subprocess",
            "time",
            "transport_drift",
        ),
    ),
    "autocode_planning": (
        "units.autoplanner",
        (
            "OBLIGATION_POLICY",
            "PINNED_REVIEWER_MODEL",
            "PROMPTS",
            "RESPONSE_EVIDENCE_RULE",
            "REVISION_CONFLICT_RULE",
            "STAGES",
            "V2_STAGES",
            "V2_STAGE_ROLES",
            "charge",
            "context",
            "enabled",
            "engine_for",
            "entry_stage",
            "fill_trace_id",
            "is_planning",
            "next_after",
            "obligation_policy",
            "prepare",
            "repair_rules",
            "review_call_limit",
            "role_for",
            "route_for",
            "s",
            "set_review_call_limit",
            "start",
            "trace_rows",
        ),
    ),
    "autocode_orchestrator": (
        "autopilot",
        (
            "SKIP",
            "drive",
        ),
    ),
}


def source_modules() -> dict[str, Path]:
    modules = {}
    for path in python_sources(TOOLS):
        relative = path.relative_to(TOOLS)
        modules[".".join(relative.with_suffix("").parts)] = path
    return modules


def script_entry(node: ast.stmt) -> bool:
    """`if __name__ == "__main__":`, which runs only when the file is executed as a program."""
    test = node.test if isinstance(node, ast.If) else None
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and len(test.comparators) == 1
        and getattr(test.comparators[0], "value", None) == "__main__"
    )


def import_graph() -> dict[str, set[str]]:
    """Internal imports each module makes when it is imported, including inside functions.

    A script entry (`if __name__ == "__main__":`) is left out: it runs only when the file is executed,
    so what it imports is not a dependency of the module."""
    modules = source_modules()
    graph = {}
    for name, path in modules.items():
        package = name.split(".")[:-1]
        targets = set()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        entry = {id(node) for block in tree.body if script_entry(block) for node in ast.walk(block)}
        for node in ast.walk(tree):
            if id(node) in entry:
                continue
            if isinstance(node, ast.Import):
                candidates = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = package[: len(package) - node.level + 1] if node.level else []
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


def _catches_import_error(handler: ast.excepthandler) -> bool:
    kind = handler.type
    if kind is None:
        return True
    if isinstance(kind, ast.Name):
        return kind.id in ("ImportError", "ModuleNotFoundError", "Exception", "BaseException")
    if isinstance(kind, ast.Tuple):
        return any(isinstance(e, ast.Name) and e.id == "ImportError" for e in kind.elts)
    return False


def _names_bound(body: list[ast.stmt]) -> set[str]:
    """Every name an import statement list binds, whether or not it is later referenced."""
    bound = set()
    for statement in body:
        for node in ast.walk(statement):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                bound.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
    return bound


def shim_bindings(source: str) -> tuple[set[str], set[str]]:
    """Names bound by each branch of a module's dual-mode import shim.

    ``tools/autocode.py`` imports its compatibility API twice: relative imports inside ``try``, and
    flat imports in the ``except ImportError`` fallback that runs when the file is executed as a
    script or when ``tools/`` is on ``sys.path`` instead of the installed package. A name bound only
    in the first branch silently does not exist in flat mode, and the failure surfaces as an
    AttributeError at a use site far from the import (#840, and #835 for the star-export variant)."""
    tree = ast.parse(source)
    package, flat = set(), set()
    for statement in tree.body:
        if not isinstance(statement, ast.Try):
            continue
        handlers = [handler for handler in statement.handlers if _catches_import_error(handler)]
        if not handlers:
            continue
        package |= _names_bound(statement.body)
        for handler in handlers:
            flat |= _names_bound(handler.body)
    return package, flat


class ArchitectureTests(unittest.TestCase):
    def test_source_inventory_includes_nested_runtime_and_excludes_fixtures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = (
                "controller.py",
                "units/planner.py",
                "contest.py",
                "test_fixture.py",
                "units/test_fixture.py",
                "units/tests/fixture.py",
                "__pycache__/cached.py",
            )
            for name in paths:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("")
            self.assertEqual(
                {"controller.py", "units/planner.py", "contest.py"},
                {str(path.relative_to(root)).replace("\\", "/") for path in python_sources(root)},
            )

    def test_no_module_joins_an_import_cycle(self):
        tangled = modules_in_cycles(import_graph())
        self.assertFalse(
            tangled - TANGLED,
            "these modules now import, directly or indirectly, a module that imports "
            f"them back; depend on lower-level modules instead: {sorted(tangled - TANGLED)}",
        )

    def test_a_module_that_left_the_cycles_is_taken_off_the_list(self):
        left = TANGLED - modules_in_cycles(import_graph())
        self.assertFalse(
            left, f"progress: these modules are no longer in an import cycle; remove them from TANGLED: {sorted(left)}"
        )

    def test_largest_modules_do_not_grow(self):
        for name, limit in MAX_LINES.items():
            with self.subTest(module=name):
                lines = len((TOOLS / name).read_text().splitlines())
                self.assertLessEqual(
                    lines,
                    limit,
                    f"{name} grew to {lines} lines (limit {limit}); move behavior into a lower-level module instead",
                )

    def test_the_shared_helpers_import_nothing_from_autocode(self):
        # autocode_util is the bottom layer; one AutoCode import would drag its 18 users back into the cycle.
        self.assertEqual(set(), import_graph()["autocode_util"])

    def test_star_import_shims_preserve_consumer_compatibility_exports(self):
        for prefix in ("", "autocode_cli."):
            for shim_name, (source_name, names) in COMPATIBILITY_EXPORTS.items():
                shim = importlib.import_module(prefix + shim_name)
                source = importlib.import_module(prefix + source_name)
                for name in names:
                    with self.subTest(shim=prefix + shim_name, name=name):
                        self.assertTrue(
                            hasattr(shim, name), f"{shim.__name__}.{name} is a required compatibility export"
                        )
                        self.assertIs(getattr(shim, name), getattr(source, name))

    def test_the_import_shim_fallback_binds_every_compatibility_name(self):
        # A name bound only by the relative branch does not exist when autocode.py runs as a script or
        # is imported flat, and the AttributeError surfaces far from the import that caused it (#840).
        package, flat = shim_bindings((TOOLS / "autocode.py").read_text(encoding="utf-8"))
        self.assertTrue(
            package and flat,
            "no dual-mode import shim was recognised in autocode.py; if its shape changed, update "
            "shim_bindings() rather than letting this check pass vacuously",
        )
        self.assertFalse(
            package - flat,
            "these compatibility names are bound when autocode is imported as a package but not by the "
            "except ImportError fallback, so they are missing at runtime in flat/script mode; bind them "
            f"there too: {sorted(package - flat)}",
        )


if __name__ == "__main__":
    unittest.main()
