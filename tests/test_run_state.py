"""Every run-state key tools/ touches is named in the typed RunState (#695)."""

from __future__ import annotations

import ast
import re
import sys
import tempfile
import unittest
from pathlib import Path

from tests.source_inventory import python_sources

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import autocode_run_state as run_state

# Named state dictionaries are checked through RunState; untyped parameters are
# a separate migration. A new plain-dict signature must not silently undo this.
MAX_DICT_TYPED_STATE_PARAMS = 0
# Unannotated ``state`` parameters still to move onto RunState, one module at a
# time (#695). Lower this when a module moves; it must never grow.
MAX_UNTYPED_STATE_PARAMS = 11


def _state_params(root: Path):
    for path in python_sources(root):
        for node in ast.walk(ast.parse(path.read_text(errors="ignore"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
                    if arg.arg == "state":
                        yield arg


def untyped_state_params(root: Path = TOOLS) -> int:
    return sum(1 for arg in _state_params(root) if arg.annotation is None)


def dict_typed_state_params(root: Path = TOOLS) -> int:
    def is_dict(annotation: ast.expr) -> bool:
        if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
            return is_dict(ast.parse(annotation.value, mode="eval").body)
        if isinstance(annotation, ast.Name):
            return annotation.id in {"dict", "Dict"}
        if isinstance(annotation, ast.Attribute):
            return (
                isinstance(annotation.value, ast.Name)
                and annotation.value.id in {"typing", "builtins"}
                and annotation.attr in {"dict", "Dict"}
            )
        if isinstance(annotation, ast.Subscript):
            wrapper = (
                annotation.value.id
                if isinstance(annotation.value, ast.Name)
                else annotation.value.attr
                if isinstance(annotation.value, ast.Attribute)
                and isinstance(annotation.value.value, ast.Name)
                and annotation.value.value.id == "typing"
                else None
            )
            if wrapper in {"Optional", "Union"}:
                members = annotation.slice.elts if isinstance(annotation.slice, ast.Tuple) else [annotation.slice]
                return any(is_dict(member) for member in members)
            return is_dict(annotation.value)
        if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
            return is_dict(annotation.left) or is_dict(annotation.right)
        return False

    return sum(1 for arg in _state_params(root) if arg.annotation is not None and is_dict(arg.annotation))


def touched_keys(root: Path = TOOLS) -> set[str]:
    """Every key application source reads or writes on a state dict."""
    keys = set()
    for path in python_sources(root):
        keys.update(_touched_keys_in(path.read_text(errors="ignore")))
    return keys


def _touched_keys_in(source: str) -> set[str]:
    tree = ast.parse(source)
    constants = _string_constants(tree)

    def literal(node: ast.expr | None) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return constants.get(node.id)
        return None

    keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and _is_state(node.value):
            keys.add(literal(node.slice))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and _is_state(node.func.value):
            if node.func.attr in ("get", "setdefault", "pop"):
                if node.args:
                    keys.add(literal(node.args[0]))
            elif node.func.attr == "update":
                for keyword in node.keywords:
                    keys.add(keyword.arg)
                if node.args and isinstance(node.args[0], ast.Dict):
                    for key in node.args[0].keys:
                        keys.add(literal(key))
    keys.discard(None)
    return keys


def _string_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = "text"`` and ``NAME: Final = "text"`` with an upper-case name.

    A module that keeps its state key in a constant (``state.setdefault(KEY, [])``) touches that
    key as surely as one that spells it out; without this the scan missed one such key (#695)."""
    constants = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue
        if (
            isinstance(target, ast.Name)
            and target.id.isupper()
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
        ):
            constants[target.id] = value.value
    return constants


def _is_state(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id == "state"


class RunStateTests(unittest.TestCase):
    def test_dict_typed_state_params_do_not_grow(self):
        self.assertLessEqual(dict_typed_state_params(), MAX_DICT_TYPED_STATE_PARAMS)

    def test_untyped_state_params_do_not_grow(self):
        count = untyped_state_params()
        self.assertLessEqual(count, MAX_UNTYPED_STATE_PARAMS, "annotate new state parameters as RunState")
        self.assertGreater(
            count + 20, MAX_UNTYPED_STATE_PARAMS, f"{count} untyped state parameters remain: lower the ratchet"
        )

    def test_untyped_ratchet_counts_only_unannotated_state_params(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime.py").write_text(
                "def plain(state, /): pass\n"
                "async def keyword(*, state=None): pass\n"
                "def typed(state: RunState): pass\n"
                "def other_name(config): pass\n"
            )
            (root / "tests").mkdir()
            (root / "tests" / "test_fixture.py").write_text("def fixture(state): pass\n")
            self.assertEqual(2, untyped_state_params(root))

    def test_state_annotation_ratchet_finds_plain_and_optional_dicts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime.py").write_text(
                "def positional(state: dict, /): pass\n"
                "def generic(state: dict[str, object]): pass\n"
                'async def optional(*, state: "dict | None"): pass\n'
                "def qualified(state: typing.Dict[str, object]): pass\n"
                "def legacy_optional(state: Optional[dict]): pass\n"
                "def legacy_union(state: typing.Union[None, dict]): pass\n"
                "def typed(state: RunState): pass\n"
                "def untyped(state): pass\n"
                "def other_name(config: dict): pass\n"
            )
            (root / "tests").mkdir()
            (root / "tests" / "test_fixture.py").write_text("def fixture(state: dict): pass\n")
            self.assertEqual(6, dict_typed_state_params(root))

    def test_every_touched_key_is_named(self):
        missing = sorted(touched_keys() - set(run_state.KEYS))
        self.assertEqual([], missing, f"run-state keys used in tools/ but not named in autocode_run_state: {missing}")

    def test_agents_md_key_count_tracks_the_contract(self):
        agents = (Path(__file__).resolve().parents[1] / "AGENTS.md").read_text()
        match = re.search(r"dict of about (\d+) named keys", agents)
        self.assertIsNotNone(match, "AGENTS.md rule 4 must state the run-state key count")
        documented, actual = int(match.group(1)), len(run_state.KEYS)
        self.assertLessEqual(
            abs(documented - actual), actual * 0.1, f"AGENTS.md says {documented} keys, the contract names {actual}"
        )

    def test_the_contract_names_the_core_keys_with_a_writer_or_reader(self):
        source = Path(TOOLS / "autocode_run_state.py").read_text()
        for key in (
            "status",
            "goal_contract",
            "validation",
            "settings",
            "stages",
            "current_task",
            "next_stage",
            "workspace",
        ):
            self.assertIn(key, run_state.KEYS)
            self.assertIn(f"    {key}:", source, f"{key} must be annotated in RunState")

    def test_a_new_key_fails_this_test_until_it_is_named(self):
        # Exercise the shared production boundary without duplicating its scanner.
        with tempfile.TemporaryDirectory(prefix="run-state-boundary-") as directory:
            root = Path(directory)
            files = {
                "runtime.py": "state['unlisted_production_key'] = 1\n",
                "units/planner.py": 'state["status"] = state.get("undeclared_runtime_key")\n',
                "test_support/runtime.py": "state.setdefault('unlisted_support_key', 0)\n",
                "tests/test_fixture.py": "state['fixture_only_key'] = 1\n",
                "units/tests/fixture.py": "state.get('nested_fixture_key')\n",
                "__pycache__/generated.py": "state.get('generated_fixture_key')\n",
            }
            for name, source in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source)
            unknown = {"unlisted_production_key", "undeclared_runtime_key", "unlisted_support_key"}
            keys = touched_keys(root)
            self.assertEqual({"status", *unknown}, keys)
            self.assertEqual(unknown, keys - run_state.KEYS)

    def test_test_only_keys_are_excluded_from_runtime_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime.py").write_text('state["status"]\n')
            (root / "tests").mkdir()
            (root / "tests" / "test_runtime.py").write_text('state["seen"]\n')
            nested = root / "tests" / "units"
            nested.mkdir()
            (nested / "test_nested.py").write_text('state.get("reasoning_efforts")\n')
            self.assertEqual({"status"}, touched_keys(root))

    def test_pop_and_update_writes_count_as_touched_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime.py").write_text(
                'state.pop("completion_actor", None)\n'
                "state.update(resolver_human_proposal={})\n"
                'state.update({"seen": 1})\n'
            )
            self.assertEqual({"completion_actor", "resolver_human_proposal", "seen"}, touched_keys(root))

    def test_keys_held_in_module_constants_count_as_touched_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime.py").write_text(
                'KEY = "rounds_kept_in_a_constant"\n'
                'STAGE: Final = "key_in_a_final_constant"\n'
                'label = "lower_case_name_is_not_a_key_constant"\n'
                "def touch(state, label):\n"
                "    state.setdefault(KEY, [])\n"
                "    state[STAGE]\n"
                "    state.get(label)\n"
                "    state.pop(IMPORTED_ELSEWHERE, None)\n"
            )
            (root / "other.py").write_text('state.get(KEY)\nstate["status"]\n')
            self.assertEqual({"rounds_kept_in_a_constant", "key_in_a_final_constant", "status"}, touched_keys(root))

    def test_unknown_runtime_keys_still_fail_the_contract_comparison(self):
        unknown = {"unknown_runtime_scan_fixture", "unknown_nested_runtime_scan_fixture"}
        self.assertTrue(unknown.isdisjoint(run_state.KEYS))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime.py").write_text('state["unknown_runtime_scan_fixture"]\n')
            nested = root / "units"
            nested.mkdir()
            (nested / "runtime.py").write_text('state.get("unknown_nested_runtime_scan_fixture")\n')
            missing = sorted(touched_keys(root) - set(run_state.KEYS))
            self.assertEqual(sorted(unknown), missing)

    def test_keys_are_stable_and_unique(self):
        self.assertIsInstance(run_state.KEYS, frozenset)
        self.assertEqual(len(run_state.KEYS), len(set(run_state.KEYS)))
        for key in run_state.KEYS:
            self.assertRegex(key, r"^[a-z_][a-z0-9_]*$", key)


if __name__ == "__main__":
    unittest.main()
