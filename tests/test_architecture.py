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
# 2026-10-16: which role and engine run a stage (autocode_stage_roles), the planning cycle
# (autocode_planning_cycle) and the model request type (units/model_request) moved out of the planning
# unit and units.common, which freed the last six: the goal lifecycle, workflow, the planning unit, its
# compatibility shim, units.common and the stage context (#695). No module is in an import cycle now.
# Taking a module out of the cycles is progress (remove it here); adding one fails.
TANGLED: frozenset[str] = frozenset()

# Line counts on 2026-09-28, after merging master at 24617cc and moving subcommand dispatch out of
# autocode.py. Lower these when a module shrinks.
# 2026-10-01: the durable-intervention application policy (metadata, consume,
# boundary effects) moved to autocode_stop, shrinking autocode.py further.
# 2026-10-04: the regression proof's prompt notes moved to autocode_regression, which owns the proof.
# 2026-10-06: the closed-terminal output wrappers moved to autocode_detached_output (#454).
# 2026-10-09: joint transport checks moved to autocode_joint_transport (#799).
# 2026-10-09: ruff import sorting expanded compact semicolon imports to individual lines (#803).
# 2026-10-09: ruff format pass expanded compact one-liners into multi-line style (~30% line growth).
# 2026-10-10: pure human-review flow readiness moved out of the goal contract.
# 2026-10-11: the CLI's action dispatch and parser joined, at their sizes after handle()'s steps moved
# to the autocode_action_* modules (#1052).
# 2026-10-11: --program-guidance and its --review-token checks raised autocode_args.py (#866).
# 2026-10-11: reading the project settings for a new run raised autocode_args.py (#707).
# 2026-10-12: autopilot.run, a drifted dead copy of the build loop, was deleted (#1071).
# 2026-10-13: the pure suite-definition scanner moved out of verification (#1125).
# 2026-10-14: Go and unittest result parsing moved to their existing test-protocol modules (#1125).
# 2026-10-14: refusing an answer that names none of a question's options (#1013) raised autocode.py
# by its --chat re-prompt; the rule is in autocode_answer_options.
# 2026-10-14: the report-only mutation check (#699) runs in autocode_regression; verify() keeps the candidate
# tree for it and diffs product files through the staged tree, which raised autocode_verify.py.
# 2026-10-15: the pinned pytest policy reader moved beside the current one in autocode_test_root, and a
# package's suite (#711) is detected and run through autocode_package_scope, which lowered autocode_verify.py.
# 2026-10-16: queuing a report-only repair moved to autocode_report_repair_queue (#1257).
# 2026-10-16: annotating commit_boundary_candidate's state as RunState (#695) pushed its signature past
# the line length, and ruff format wrapped it onto three lines: autocode.py +2, no new behavior.
# 2026-10-17: the --auto-select flag's parser entry raised autocode_args.py (#1426); its action lives in
# autocode_configure._auto_select.
# 2026-10-17: stamping open escalations on --accept-completion (#612) added route_ladder import and
# record_outcome call: autocode.py +3.
MAX_LINES = {
    "autocode.py": 2008,
    "autocode_goals.py": 1404,
    "autocode_support.py": 620,
    "autopilot.py": 1513,
    "autocode_run_actions.py": 366,
    "autocode_args.py": 1028,
    "autocode_verify.py": 3333,
}

# Line counts of the largest functions, (module, function): lines. handle() was 946 lines before its
# steps moved out (#1052): a new flag's action goes in its family's step, not in handle().
# 2026-10-11: --program-guidance raised build_parser() (#866); splitting it is the next step.
MAX_FUNCTION_LINES = {("autocode_run_actions.py", "handle"): 9, ("autocode_args.py", "build_parser"): 539}

# Modules that still write run ``status`` directly instead of going through autocode_run_status
# (#1071). The paused phase is already owned outright (tests/test_run_status.py); this ratchet
# covers every other status write: 69 modules on 2026-10-12. Most are run-state writes to migrate;
# the dashboard/*, fixture (fake_*, autopilot_testkit, blackbox_build_provider, provider_conformance,
# live_diagnosis_trial) and program/task-record entries write their own records' status fields, not
# run status, and stay. Taking a module off the list is progress; a new writer outside it fails.
# 2026-10-16: autocode_planning_cycle holds the planning unit's start(), moved unchanged with its one status
# write to take the planning unit out of the import cycle (#695); units.autoplanner still writes status in
# rerun_requirements. No write was added; it is still one to migrate.
STATUS_WRITERS = frozenset(
    {
        "autocode",
        "autocode_action_authority",
        "autocode_bug_job",
        "autocode_builder_failure",
        "autocode_builder_policy",
        "autocode_builder_worker",
        "autocode_checkpoint_continuation",
        "autocode_classification_recovery",
        "autocode_dependency",
        "autocode_design_check_job",
        "autocode_design_intake",
        "autocode_design_job",
        "autocode_design_revision",
        "autocode_discuss_job",
        "autocode_dispatch",
        "autocode_efficiency",
        "autocode_finding_cause",
        "autocode_finding_close",
        "autocode_findings",
        "autocode_follow_up",
        "autocode_goal_lifecycle",
        "autocode_goals",
        "autocode_job_failure",
        "autocode_local_run",
        "autocode_milestones",
        "autocode_operational_information",
        "autocode_planning_clarification",
        "autocode_planning_cycle",
        "autocode_preflight_unittest",
        "autocode_recovery_inputs",
        "autocode_recovery_progress",
        "autocode_repair_handoff",
        "autocode_replay_checks",
        "autocode_resolver_human",
        "autocode_resolver_recovery",
        "autocode_resolver_runtime",
        "autocode_review_job",
        "autocode_rework_policy",
        "autocode_run_actions",
        "autocode_run_records",
        "autocode_run_setup",
        "autocode_stuck_job",
        "autocode_support",
        "autocode_tasks",
        "autocode_ui",
        "autocode_visual_check",
        "autocode_visual_diff",
        "autocode_workflows",
        "autopilot",
        "autopilot_testkit",
        "blackbox_build_provider",
        "dashboard.agent_console",
        "dashboard.conversation_recovery",
        "dashboard.dashboard_backend",
        "dashboard.dashboard_chat",
        "dashboard.dashboard_chat_intent",
        "dashboard.dashboard_continuous",
        "dashboard.dashboard_conversations",
        "dashboard.dashboard_delete",
        "dashboard.dashboard_monitor",
        "dashboard.dashboard_setup",
        "fake_codex",
        "live_diagnosis_trial",
        "provider_conformance",
        "units.autoplanner",
        "units.autoresolver",
    }
)


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


def status_writes(source: str) -> list[int]:
    """Lines that write run ``status`` directly: subscript assignment and ``update(status=...)``.

    Dict literals that merely carry a status value are data until something writes them, so only
    mutation counts. ``autocode_run_status`` itself is exempted by the caller."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign):
            if any(
                isinstance(target, ast.Subscript)
                and isinstance(target.slice, ast.Constant)
                and target.slice.value == "status"
                for target in node.targets
            ):
                lines.append(node.lineno)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "update":
            if any(keyword.arg == "status" for keyword in node.keywords):
                lines.append(node.lineno)
            for argument in node.args:
                if isinstance(argument, ast.Dict) and any(
                    isinstance(key, ast.Constant) and key.value == "status" for key in argument.keys
                ):
                    lines.append(node.lineno)
    return sorted(lines)


def modules_writing_status() -> dict[str, list[int]]:
    owner = "autocode_run_status"
    return {
        name: status_writes(path.read_text(encoding="utf-8"))
        for name, path in sorted(source_modules().items())
        if name != owner and status_writes(path.read_text(encoding="utf-8"))
    }


def _atomic_state_json_lines(source: str) -> list[int]:
    """Line numbers of atomic_json calls writing a path named exactly state.json."""

    def names_state_json(arg: ast.expr) -> bool:
        return any(isinstance(part, ast.Constant) and part.value == "state.json" for part in ast.walk(arg))

    lines = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if called == "atomic_json" and any(names_state_json(arg) for arg in node.args):
            lines.append(node.lineno)
    return sorted(lines)


def direct_state_json_writers() -> set[str]:
    """Modules that write a literal state.json with atomic_json instead of the status owner."""
    writers = set()
    for name, path in source_modules().items():
        if name == "autocode_status":
            continue
        if _atomic_state_json_lines(path.read_text(encoding="utf-8")):
            writers.add(name)
    return writers


def private_core_uses(source: str, core: set[str]) -> list[str]:
    """`line:name` for each underscore-private name the source takes from a core module.

    A core module is a top-level module of tools/; it is reached by `import`, `from .. import`
    (aliases included) or `from core import _name`."""
    tree = ast.parse(source)
    aliases, found = set(), []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.update(a.asname or a.name for a in node.names if a.name in core)
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").removeprefix("autocode_cli.")
            if module in core:
                found += [f"{node.lineno}:{module}.{a.name}" for a in node.names if a.name.startswith("_")]
            elif not module:
                aliases.update(a.asname or a.name for a in node.names if a.name in core)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in aliases
            and node.attr.startswith("_")
            and not node.attr.startswith("__")
        ):
            found.append(f"{node.lineno}:{node.value.id}.{node.attr}")
    return sorted(found)


def dashboard_private_core_uses() -> dict[str, list[str]]:
    modules = source_modules()
    core = {name for name in modules if "." not in name}
    return {
        name: uses
        for name, path in sorted(modules.items())
        if name.startswith("dashboard.") and (uses := private_core_uses(path.read_text(encoding="utf-8"), core))
    }


def state_json_names(source: str) -> list[int]:
    """Line numbers of each string literal naming exactly state.json."""
    return sorted(
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and node.value == "state.json"
    )


def dashboard_state_json_readers() -> set[str]:
    """Dashboard modules other than its one run-state reader that name state.json."""
    return {
        name
        for name, path in source_modules().items()
        if name.startswith("dashboard.")
        and name != "dashboard.dashboard_run_state"
        and state_json_names(path.read_text(encoding="utf-8"))
    }


class ArchitectureTests(unittest.TestCase):
    def test_source_inventory_includes_nested_runtime_and_excludes_fixtures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = (
                "controller.py",
                "units/planner.py",
                "contest.py",
                "tests/test_fixture.py",
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

    def test_no_module_outside_the_transition_owner_writes_status(self):
        writers = modules_writing_status()
        self.assertFalse(
            set(writers) - STATUS_WRITERS,
            "these modules write run status directly; transition through autocode_run_status instead "
            f"(then the paused-phase gate in tests/test_run_status.py keeps it owned): {sorted(set(writers) - STATUS_WRITERS)}",
        )

    def test_a_module_that_stopped_writing_status_leaves_the_list(self):
        writers = modules_writing_status()
        self.assertFalse(
            STATUS_WRITERS - set(writers),
            f"progress: these modules no longer write run status directly; remove them from STATUS_WRITERS: "
            f"{sorted(STATUS_WRITERS - set(writers))}",
        )

    def test_the_status_scan_sees_every_write_form(self):
        source = (
            'state["status"] = "RUNNING"\n'
            'other.update(status="PAUSED_TIMEOUT")\n'
            'third.update({"status": "TASK_COMPLETE"})\n'
            'fourth = {"status": "RUNNING"}\n'
        )
        self.assertEqual([1, 2, 3], status_writes(source))

    def test_every_state_json_write_goes_through_the_status_owner(self):
        # The run-state owner stamps the runner, checkpoints and activity
        # records (#985); a direct atomic_json write skips all of them.
        self.assertEqual(set(), direct_state_json_writers(), "write run state through autocode_status.persist instead")

    def test_the_state_json_scan_sees_the_literal_form(self):
        source = (
            'util.atomic_json(directory / "state.json", state)\n'
            'util.atomic_json(directory / "other.json", state)\n'
            'util.atomic_json(directory / "state.json.bak", state)\n'
        )
        self.assertEqual([1], _atomic_state_json_lines(source))

    def test_the_dashboard_uses_only_public_names_of_the_core(self):
        # The dashboard is a client of the core (#730): a private helper can change without notice.
        self.assertEqual(
            {}, dashboard_private_core_uses(), "give the helper a public name in its core module, then use that"
        )

    def test_the_private_name_scan_sees_every_import_form(self):
        source = (
            "from .. import autocode_conversation as protocol\n"
            "import autocode_util\n"
            "from autocode_cli.autocode_status import _persist, persist\n"
            "protocol._atomic_json(path, value)\n"
            "autocode_util.__name__\n"
            "local._helper()\n"
            "autocode_util._lock()\n"
        )
        core = {"autocode_conversation", "autocode_util", "autocode_status"}
        self.assertEqual(
            ["3:autocode_status._persist", "4:protocol._atomic_json", "7:autocode_util._lock"],
            private_core_uses(source, core),
        )

    def test_the_dashboard_reads_saved_run_state_in_one_module(self):
        # New dashboard reads go through the status view (status_view); the run list's raw reads
        # stay in dashboard_run_state until the view carries what it shows (#730).
        self.assertEqual(
            set(), dashboard_state_json_readers(), "read state.json through dashboard_run_state, or use status_view"
        )

    def test_the_state_json_name_scan_sees_the_literal(self):
        source = 'run / "state.json"\nrun / "state.json.bak"\nopen("state.json")\n'
        self.assertEqual([1, 3], state_json_names(source))

    def test_largest_modules_do_not_grow(self):
        for name, limit in MAX_LINES.items():
            with self.subTest(module=name):
                lines = len((TOOLS / name).read_text().splitlines())
                self.assertLessEqual(
                    lines,
                    limit,
                    f"{name} grew to {lines} lines (limit {limit}); move behavior into a lower-level module instead",
                )

    def test_largest_functions_do_not_grow(self):
        for (name, function), limit in MAX_FUNCTION_LINES.items():
            with self.subTest(module=name, function=function):
                tree = ast.parse((TOOLS / name).read_text())
                node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function)
                lines = node.end_lineno - node.lineno + 1
                self.assertLessEqual(
                    lines,
                    limit,
                    f"{name}:{function}() grew to {lines} lines (limit {limit}); move behavior into a function of its own",
                )

    def test_the_shared_helpers_import_nothing_from_autocode(self):
        # autocode_util is the bottom layer; one AutoCode import would drag its 18 users back into the cycle.
        self.assertEqual(set(), import_graph()["autocode_util"])

    def test_the_effort_fit_rule_lives_in_one_module(self):
        # #2057: every derived effort is fitted by one function, so a new tier is not a new place to forget.
        definitions = {
            name
            for name, path in source_modules().items()
            if any(
                isinstance(node, ast.FunctionDef) and node.name == "nearest"
                for node in ast.parse(path.read_text(encoding="utf-8")).body
            )
        }
        self.assertEqual({"autocode_route_fit"}, definitions)

    def test_every_stage_launch_fits_its_effort_at_the_one_boundary(self):
        # #2057: the fit is called in run_role, before the request is built, for every path.
        tree = ast.parse((TOOLS / "autocode.py").read_text(encoding="utf-8"))
        run_role = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run_role")
        calls = [
            node.func.attr
            for node in ast.walk(run_role)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "route_fit"
        ]
        self.assertIn("launch", calls, "run_role must fit the effort it launches")

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
