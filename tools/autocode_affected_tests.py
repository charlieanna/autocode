"""Conservative affected-test selection for changed-code mutation checks (#2942).

When mutation observations check many proving tests across several files or functions, running
every selected test on every mutant scales poorly. This module inspects the candidate tree without
executing untrusted code or installing any tool into the project, and narrows each mutant to the
conservative subset of ``selected_tests`` that can reach it:

1. ``import_closure``: every test whose transitive workspace Python imports (plus ancestor
   ``__init__.py``, ``conftest.py``, and ``-p`` plugins) can reach the mutated file, plus any
   test that cannot be mapped cleanly to a file or passes through an opaque/dynamic import edge.
2. ``symbol_closure``: when the mutated line lies inside an ordinary function or method body of a
   parseable non-opaque module, narrows further within ``import_closure`` to tests (and their
   reachable helper/fixture code) that reference any symbol in the intra-module reverse closure of
   that definition. Any uncertainty falls back to ``import_closure`` or ``all_selected_tests``.

Pure static analysis plus runner command narrowing: never executes project code and never
under-selects.
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

try:
    from . import autocode_dependency_dirs as dependency_dirs
    from . import autocode_package_scope as package_scope
    from . import autocode_proof_stability as proof_stability
    from . import autocode_python_test_roles as role_policy
    from . import autocode_python_tests as python_tests
except ImportError:
    import autocode_dependency_dirs as dependency_dirs
    import autocode_package_scope as package_scope
    import autocode_proof_stability as proof_stability
    import autocode_python_test_roles as role_policy
    import autocode_python_tests as python_tests

POLICY = "conservative_ast_closure_v1"
RULE_ALL = "all_selected_tests"
RULE_IMPORT = "import_closure"
RULE_SYMBOL = "symbol_closure"

_SKIP_DIRS = frozenset(
    {
        ".git",
        ".autocode",
        ".autocode-ui",
        "__pycache__",
        ".tox",
        ".nox",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        *dependency_dirs.NAMES,
    }
)
_OPAQUE_MODULES = frozenset({"subprocess", "multiprocessing", "pty", "pexpect", "runpy", "imp"})
_OPAQUE_CALLS = frozenset(
    {
        "exec",
        "eval",
        "compile",
        "__import__",
        "import_module",
        "spec_from_file_location",
        "SourceFileLoader",
        "system",
        "popen",
        "walk",
        "glob",
        "rglob",
    }
)
_DYNAMIC_ATTR_CALLS = frozenset({"getattr", "setattr", "delattr", "globals", "vars", "locals"})
_FIXTURE_DECORATORS = frozenset({"fixture"})


@dataclass
class _ModuleInfo:
    path: str
    tree: ast.Module | None
    imports: set[str]
    star_imports: set[str]
    opaque: bool
    dynamic_attr: bool
    names: frozenset[str]
    string_literals: frozenset[str]
    conftest_plugins: set[str]
    conftest_star: bool


@dataclass
class _ResolvedTest:
    test_id: str
    paths: set[str]
    class_name: str | None
    func_name: str | None
    unmapped: bool


def _is_ignored_dir(name: str) -> bool:
    return name in _SKIP_DIRS or name.endswith(".egg-info")


def _scan_python_files(root: Path, overrides: dict[str, bytes] | None = None) -> dict[str, str]:
    """Read regular ``.py`` files inside ``root`` without following symlinks outside it."""
    canonical = root.resolve()
    files: dict[str, str] = {}
    for current, dirs, names in os.walk(canonical, followlinks=False):
        current_path = Path(current)
        dirs[:] = [d for d in sorted(dirs) if not _is_ignored_dir(d) and not (current_path / d).is_symlink()]
        for name in sorted(names):
            if not name.endswith(".py"):
                continue
            file_path = current_path / name
            if file_path.is_symlink():
                continue
            try:
                resolved = file_path.resolve()
                if not resolved.is_relative_to(canonical) or not file_path.is_file():
                    continue
                rel = file_path.relative_to(canonical).as_posix()
                if overrides and rel in overrides:
                    files[rel] = overrides[rel].decode("utf-8", errors="replace")
                else:
                    files[rel] = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    if overrides:
        for rel, raw in overrides.items():
            if rel.endswith(".py") and rel not in files:
                files[rel] = raw.decode("utf-8", errors="replace")
    return files


def _build_aliases(files: dict[str, str]) -> dict[str, set[str]]:
    """Every dotted name an import could use to reach each workspace file.

    A test reaches a module through whatever is on ``sys.path`` when it runs: the repository
    root, ``src/``, a package directory, a unittest start directory, a directory a test or
    ``conftest.py`` inserts, ``PYTHONPATH`` or a ``pythonpath`` setting. None of these can be
    read reliably without running the project, and a missed one under-selects. So every
    trailing part of a file's module path names it: ``components/svc/server.py`` is
    ``components.svc.server``, ``svc.server`` and ``server``. A name that also matches an
    installed or standard module only selects more tests, never fewer.
    """
    aliases: dict[str, set[str]] = {}
    for rel in files:
        parts = PurePosixPath(rel).with_suffix("").parts
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        for start in range(len(parts)):
            aliases.setdefault(".".join(parts[start:]), set()).add(rel)
    return aliases


def _dotted_name(node: ast.AST) -> str | None:
    parts = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return None


def _string_list_constant(node: ast.AST) -> list[str] | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        items = []
        for elt in node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                items.append(elt.value)
            else:
                return None
        return items
    return None


def _collect_names_and_flags(
    tree: ast.Module, path: str
) -> tuple[frozenset[str], frozenset[str], bool, bool, set[str], bool]:
    """Scan runtime AST nodes (skipping unshadowed TYPE_CHECKING) for names, literals, and opacity."""
    tc_names, tc_mods = role_policy._type_checking_guards(tree)
    names: set[str] = set()
    strings: set[str] = set()
    opaque = False
    dynamic_attr = False
    conftest_plugins: set[str] = set()
    conftest_star = False
    is_conftest = PurePosixPath(path).name == "conftest.py"

    for node in role_policy._runtime_nodes(tree, tc_names, tc_mods):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
            if node.attr == "modules" and isinstance(node.value, ast.Name) and node.value.id == "sys":
                dynamic_attr = True
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if len(node.value) <= 200:
                strings.add(node.value)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".", 1)[0]
                if top in _OPAQUE_MODULES:
                    opaque = True
                if alias.name == "inspect":
                    dynamic_attr = True
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".", 1)[0]
            if mod in _OPAQUE_MODULES:
                opaque = True
            if node.module == "inspect":
                dynamic_attr = True
            for alias in node.names:
                names.add(alias.name)
                if alias.asname:
                    names.add(alias.asname)
        elif isinstance(node, ast.Call):
            called = _dotted_name(node.func)
            leaf = called.rsplit(".", 1)[-1] if called else ""
            if leaf in _OPAQUE_CALLS:
                if leaf in ("__import__", "import_module") and (
                    node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
                ):
                    pass
                else:
                    opaque = True
            elif called in ("os.system", "os.popen") or (
                called
                and (called.startswith(("os.spawn", "os.exec", "importlib.machinery", "pathlib.Path")))
                and leaf in _OPAQUE_CALLS | {"spawnv", "spawnvp", "execv", "execve"}
            ):
                opaque = True
            elif leaf in _DYNAMIC_ATTR_CALLS:
                dynamic_attr = True
        elif is_conftest and isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "pytest_plugins":
                    declared = _string_list_constant(node.value)
                    if declared is None:
                        conftest_star = True
                    else:
                        conftest_plugins.update(declared)

    return (
        frozenset(names),
        frozenset(strings),
        opaque,
        dynamic_attr,
        conftest_plugins,
        conftest_star,
    )


def _analyze_modules(files: dict[str, str], target_files: set[str]) -> dict[str, _ModuleInfo]:
    infos: dict[str, _ModuleInfo] = {}
    for path, source in files.items():
        try:
            tree = ast.parse(source)
        except (SyntaxError, TypeError, ValueError):
            infos[path] = _ModuleInfo(
                path=path,
                tree=None,
                imports=set(),
                star_imports=set(),
                opaque=True,
                dynamic_attr=True,
                names=frozenset(),
                string_literals=frozenset(),
                conftest_plugins=set(),
                conftest_star=False,
            )
            continue
        imports, uncertain = role_policy._imports(path, source)
        star_imports: set[str] = set()
        tc_names, tc_mods = role_policy._type_checking_guards(tree)
        parent = list(PurePosixPath(path).parent.parts)
        for node in role_policy._runtime_nodes(tree, tc_names, tc_mods):
            if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
                if node.level > len(parent):
                    uncertain = True
                else:
                    prefix = parent[: len(parent) - node.level + 1] if node.level else []
                    prefix += (node.module or "").split(".") if node.module else []
                    mod = ".".join(prefix)
                    if mod:
                        star_imports.add(mod)
        names, strings, call_opaque, dynamic_attr, plugins, c_star = _collect_names_and_flags(tree, path)
        # String literal references matching target product module stem or path
        for target in target_files:
            stem = PurePosixPath(target).stem
            if target in strings or (len(stem) >= 3 and stem in strings):
                imports.add(target[:-3].replace("/", "."))
        infos[path] = _ModuleInfo(
            path=path,
            tree=tree,
            imports=imports,
            star_imports=star_imports,
            opaque=uncertain or call_opaque or c_star,
            dynamic_attr=dynamic_attr,
            names=names,
            string_literals=strings,
            conftest_plugins=plugins,
            conftest_star=c_star,
        )
    return infos


def _ancestor_inits(path: str, files: dict[str, str]) -> set[str]:
    result: set[str] = set()
    cur = PurePosixPath(path).parent
    while str(cur) not in (".", ""):
        init = f"{cur.as_posix()}/__init__.py"
        if init in files:
            result.add(init)
        cur = cur.parent
    return result


def _ancestor_conftests(path: str, files: dict[str, str]) -> set[str]:
    result: set[str] = set()
    cur = PurePosixPath(path).parent
    while True:
        cand = "conftest.py" if str(cur) in (".", "") else f"{cur.as_posix()}/conftest.py"
        if cand in files:
            result.add(cand)
        if str(cur) in (".", ""):
            break
        cur = cur.parent
    return result


def _build_edges(
    infos: dict[str, _ModuleInfo], aliases: dict[str, set[str]], files: dict[str, str]
) -> dict[str, set[str]]:
    """Forward module reachability edges: file -> direct imported/ancestor files."""
    edges: dict[str, set[str]] = {}
    for path, info in infos.items():
        targets: set[str] = set(_ancestor_inits(path, files))
        for mod in info.imports | info.star_imports | info.conftest_plugins:
            if mod in aliases:
                targets |= aliases[mod]
            elif "." in mod:
                top = mod.split(".", 1)[0]
                if top in aliases:
                    targets |= aliases[top]
        for t in list(targets):
            targets |= _ancestor_inits(t, files)
        edges[path] = targets
    return edges


def _pytest_cli_plugins(command: str, aliases: dict[str, set[str]]) -> tuple[set[str], bool]:
    """Resolve `-p` pytest plugins if passed on command line; unknown non-`no:` plugins mark opaque."""
    inner = package_scope.within(command) or command
    invocation = python_tests.parse(inner)
    if not invocation or invocation.kind != "pytest":
        return set(), False
    plugins: set[str] = set()
    opaque = False
    args = invocation.arguments
    i = 0
    while i < len(args):
        word = args[i]
        val = None
        if word == "-p" and i + 1 < len(args):
            val = args[i + 1]
            i += 2
        elif word.startswith("-p") and len(word) > 2:
            val = word[2:].lstrip("=")
            i += 1
        else:
            i += 1
            continue
        if not val or val.startswith("no:"):
            continue
        if val in aliases:
            plugins |= aliases[val]
        else:
            opaque = True
    return plugins, opaque


def _resolve_test_id(
    test_id: str,
    framework_name: str,
    files: dict[str, str],
    aliases: dict[str, set[str]],
    package_dir: str | None,
) -> _ResolvedTest:
    """Resolve a test ID to its file path(s), optional class name, and optional function name."""
    if framework_name == "pytest":
        if "::" in test_id:
            head, tail = test_id.split("::", 1)
            tail_parts = tail.split("::")
            func_raw = tail_parts[-1].split("[", 1)[0]
            class_name = tail_parts[-2] if len(tail_parts) >= 2 else None
            # Case A: head is a file path (tests/test_app.py)
            cand_paths = [head]
            if package_dir and not head.startswith(package_dir + "/"):
                cand_paths.append(f"{package_dir}/{head}")
            matched = {p for p in cand_paths if p in files}
            if matched:
                return _ResolvedTest(test_id, matched, class_name, func_raw, False)
            # Case B: head is dotted classname from junitxml (tests.test_app.TestApp or tests.test_app)
            dotted = head.split(".")
            for k in range(len(dotted), 0, -1):
                prefix = ".".join(dotted[:k])
                if prefix in aliases:
                    rest = dotted[k:]
                    if len(rest) == 1 and class_name is None:
                        class_name = rest[0]
                    elif rest:
                        # Ambiguous nested structure inside dotted classname
                        class_name = rest[-1]
                    return _ResolvedTest(test_id, set(aliases[prefix]), class_name, func_raw, False)
        return _ResolvedTest(test_id, set(), None, None, True)

    # unittest format: module.Class.method, module.Class::method, optional " (subtest)" or "[param]"
    cleaned = re.sub(r"\s+\(.*\)$", "", test_id)
    cleaned = re.sub(r"\[.*\]$", "", cleaned)
    parts = cleaned.replace("::", ".").split(".")
    for k in range(len(parts) - 1, 0, -1):
        prefix = ".".join(parts[:k])
        if prefix in aliases:
            rest = parts[k:]
            if len(rest) == 2:
                return _ResolvedTest(test_id, set(aliases[prefix]), rest[0], rest[1], False)
            if len(rest) == 1:
                return _ResolvedTest(test_id, set(aliases[prefix]), None, rest[0], False)
            return _ResolvedTest(test_id, set(aliases[prefix]), None, None, False)
    return _ResolvedTest(test_id, set(), None, None, True)


def _closure(
    seeds: set[str], edges: dict[str, set[str]], infos: dict[str, _ModuleInfo]
) -> tuple[set[str], bool, bool, bool]:
    """Return ``(reachable_files, any_opaque, any_dynamic_attr, any_star_import)``."""
    seen: set[str] = set()
    stack = list(seeds)
    any_opaque = False
    any_dynamic = False
    any_star = False
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        info = infos.get(node)
        if info is None:
            any_opaque = True
            continue
        any_opaque |= info.opaque
        any_dynamic |= info.dynamic_attr
        any_star |= bool(info.star_imports)
        for nxt in edges.get(node, ()):
            if nxt not in seen:
                stack.append(nxt)
    return seen, any_opaque, any_dynamic, any_star


def _stmt_references(node: ast.AST, tc_names: set[str], tc_mods: set[str]) -> set[str]:
    names: set[str] = set()
    if isinstance(node, ast.Module):
        fake = node
    elif isinstance(node, ast.stmt):
        fake = ast.Module(body=[node], type_ignores=[])
    elif isinstance(node, ast.expr):
        fake = ast.Module(body=[ast.Expr(value=node)], type_ignores=[])
    else:
        return names
    for sub in role_policy._runtime_nodes(fake, tc_names, tc_mods):
        if isinstance(sub, ast.Name):
            names.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            names.add(sub.attr)
    return names


def _enclosing_target_symbol(tree: ast.Module, line: int) -> tuple[str | None, bool]:
    """Identify the enclosing top-level function/class symbol for ``line``, if safely narrowable.

    Returns ``(symbol, unsafe_module_level)``: if ``line`` executes during import or lies on a
    decorator, signature default/annotation, or class body outside a method body, returns
    ``(None, True)`` so all importing tests stay selected.
    """
    tc_names, tc_mods = role_policy._type_checking_guards(tree)
    # Check if line falls in top-level executable statements outside FunctionDef/AsyncFunctionDef/ClassDef
    for node in tree.body:
        start = node.lineno
        end = getattr(node, "end_lineno", start) or start
        if not (start <= line <= end):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body_start = node.body[0].lineno if node.body else start + 1
            if line < body_start:
                # Decorator / signature default / annotation executes at import time
                return None, True
            return node.name, False
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                c_start = child.lineno
                c_end = getattr(child, "end_lineno", c_start) or c_start
                if c_start <= line <= c_end:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        c_body_start = child.body[0].lineno if child.body else c_start + 1
                        if line >= c_body_start:
                            return node.name, False
                    break
            return None, True
        return None, True
    # Also check whether any symbol closure should be invalidated by dynamic module-level code
    _ = (tc_names, tc_mods)
    return None, True


def _module_symbol_closure(tree: ast.Module, root_symbol: str) -> tuple[set[str], bool]:
    """Reverse symbol closure inside ``tree`` starting from ``root_symbol``.

    Returns ``(symbol_names, runs_on_import)`` where ``runs_on_import`` is True if any
    module-top-level non-def statement, decorator, default, or class body references a symbol
    in the closure.
    """
    tc_names, tc_mods = role_policy._type_checking_guards(tree)
    defs: dict[str, set[str]] = {}
    import_time_refs: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body_refs: set[str] = set()
            for stmt in node.body:
                body_refs |= _stmt_references(stmt, tc_names, tc_mods)
            defs.setdefault(node.name, set()).update(body_refs)
            for expr in [*node.decorator_list, *node.args.defaults, *(d for d in node.args.kw_defaults if d)]:
                import_time_refs |= _stmt_references(expr, tc_names, tc_mods)
        elif isinstance(node, ast.ClassDef):
            class_refs: set[str] = set()
            for expr in [*node.decorator_list, *node.bases, *(kw.value for kw in node.keywords)]:
                import_time_refs |= _stmt_references(expr, tc_names, tc_mods)
            for stmt in node.body:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for body_stmt in stmt.body:
                        class_refs |= _stmt_references(body_stmt, tc_names, tc_mods)
                    for expr in [*stmt.decorator_list, *stmt.args.defaults, *(d for d in stmt.args.kw_defaults if d)]:
                        import_time_refs |= _stmt_references(expr, tc_names, tc_mods)
                else:
                    import_time_refs |= _stmt_references(stmt, tc_names, tc_mods)
            defs.setdefault(node.name, set()).update(class_refs)
        elif not isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.If) and role_policy._is_type_checking_test(node.test, tc_names, tc_mods):
                continue
            import_time_refs |= _stmt_references(node, tc_names, tc_mods)

    closure = {root_symbol}
    changed = True
    while changed:
        changed = False
        for name, refs in defs.items():
            if name not in closure and (refs & closure):
                closure.add(name)
                changed = True
    return closure, bool(import_time_refs & closure)


def _is_fixture(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        dotted = _dotted_name(target)
        if dotted and dotted.rsplit(".", 1)[-1] in _FIXTURE_DECORATORS:
            return True
    return False


def _test_file_uses_symbols(
    tree: ast.Module,
    class_name: str | None,
    func_name: str | None,
    symbols: set[str],
) -> bool:
    """True unless the named test in ``tree`` provably cannot reference any name in ``symbols``.

    Conservatively returns True if:
    - ``func_name`` cannot be located in ``tree`` (e.g. inherited from another class, dynamically generated),
    - any module-level executable code, fixture, hook, or ``load_tests`` references a symbol,
    - the test class has setup/teardown/fixture methods referencing a symbol or bases that may run setup,
    - any local helper in ``tree`` that transitively reaches ``symbols`` is referenced by the test/setup.
    """
    tc_names, tc_mods = role_policy._type_checking_guards(tree)
    # 1. Build reverse closure over all non-test helper functions / fixtures / class helpers in tree
    local_defs: dict[str, set[str]] = {}
    always_active_refs: set[str] = set()
    target_class: ast.ClassDef | None = None
    target_func: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    alias_pairs = [
        (alias.name, alias.asname)
        for sub in role_policy._runtime_nodes(tree, tc_names, tc_mods)
        if isinstance(sub, ast.ImportFrom)
        for alias in sub.names
        if alias.asname
    ]

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            refs = _stmt_references(node, tc_names, tc_mods)
            if node.name == func_name and class_name is None:
                target_func = node
            elif _is_fixture(node) or node.name in (
                "setUpModule",
                "tearDownModule",
                "load_tests",
                "pytest_generate_tests",
            ):
                always_active_refs |= refs
            else:
                local_defs.setdefault(node.name, set()).update(refs)
        elif isinstance(node, ast.ClassDef):
            if class_name is not None and node.name == class_name:
                target_class = node
                for dec in node.decorator_list:
                    always_active_refs |= _stmt_references(dec, tc_names, tc_mods)
                if node.bases:
                    # Only simple unittest.TestCase / TestCase / object bases are free of hidden setup mixins
                    for base in node.bases:
                        bname = _dotted_name(base)
                        if bname not in ("unittest.TestCase", "TestCase", "object"):
                            return True
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        refs = _stmt_references(item, tc_names, tc_mods)
                        if item.name == func_name:
                            target_func = item
                        elif _is_fixture(item) or item.name in (
                            "setUp",
                            "tearDown",
                            "setUpClass",
                            "tearDownClass",
                            "shortDescription",
                        ):
                            always_active_refs |= refs
                        else:
                            local_defs.setdefault(item.name, set()).update(refs)
                    elif isinstance(item, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr)):
                        always_active_refs |= _stmt_references(item, tc_names, tc_mods)
            else:
                local_defs.setdefault(node.name, set()).update(_stmt_references(node, tc_names, tc_mods))
        elif not isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.If) and role_policy._is_type_checking_test(node.test, tc_names, tc_mods):
                continue
            always_active_refs |= _stmt_references(node, tc_names, tc_mods)

    if target_func is None or (class_name is not None and target_class is None):
        return True

    expanded = set(symbols)
    changed = True
    while changed:
        changed = False
        for orig, asname in alias_pairs:
            if orig in expanded and asname not in expanded:
                expanded.add(asname)
                changed = True
        for name, refs in local_defs.items():
            if name not in expanded and (refs & expanded):
                expanded.add(name)
                changed = True

    if always_active_refs & expanded:
        return True
    return bool(_stmt_references(target_func, tc_names, tc_mods) & expanded)


def _selector_close(framework_name: str, command: str, subset: list[str], all_tests: list[str]) -> list[str]:
    """Expand ``subset`` to include any test in ``all_tests`` that ``narrow`` would also select.

    ``pytest -k`` matches substrings case-insensitively and ``unittest discover -k`` matches
    substrings, so without closure a mutant command could execute an extra test and fail exact
    result matching in ``autocode_mutation_results.outcome``.
    """
    if not subset or set(subset) == set(all_tests):
        return list(all_tests)
    inner = package_scope.within(command) or command
    invocation = python_tests.parse(inner, include_owned=True)
    if invocation is None:
        return list(all_tests)
    if framework_name == "pytest":
        needles = {test.rsplit("::", 1)[-1].split("[", 1)[0].casefold() for test in subset}
        closed = {test for test in all_tests if any(needle and needle in test.casefold() for needle in needles)}
        return sorted(set(subset) | closed)
    if framework_name == "unittest":
        if invocation.discovers:
            selectors = [test.replace("::", ".") for test in subset]
            closed = {test for test in all_tests if any(sel in test.replace("::", ".") for sel in selectors)}
            return sorted(set(subset) | closed)
        if any(" (" in test for test in subset):
            return list(all_tests)
    return sorted(set(subset))


def select(
    framework_name: str,
    command: str,
    tests: list[str],
    tree: Path | str,
    files: list[str],
    *,
    sources: dict[str, bytes] | None = None,
) -> dict:
    """Compute conservative affected-test subsets per ``(path, line)`` across ``files``.

    Returns ``{"by_site": {(path, line): {"tests": [...], "rule": ..., "command": str | None}},
    "summary": {...}}``. Never raises on unexpected repo structures: falls back deterministically to
    all ``tests`` with the fallback reason recorded.
    """
    tests = sorted(dict.fromkeys(tests))
    tree_path = Path(tree)
    inner = package_scope.within(command) or command
    parsed = python_tests.parse(inner, include_owned=True)
    package_entry = package_scope.entered(command)
    package_dir = package_entry[0] if package_entry else None

    def full_result(reason: str) -> dict:
        return {
            "tests": list(tests),
            "fallback": reason,
            "by_site": {},
            "default": {"tests": list(tests), "rule": RULE_ALL, "command": None},
            "summary": {
                "policy": POLICY,
                "total_selected_tests": len(tests),
                "narrowed_mutants": 0,
                "unobserved_mutants": 0,
                "full_selection_mutants": 0,
                "rules_used": [RULE_ALL],
                "fallback": reason,
            },
        }

    if not tests:
        return full_result("no_selected_tests")
    if len(tests) == 1:
        return full_result("single_selected_test")
    if framework_name not in ("unittest", "pytest") or parsed is None or parsed.kind != framework_name:
        return full_result("unsupported_runner")

    target_files = {f for f in files if f.endswith(".py")}
    py_files = _scan_python_files(tree_path, overrides=sources)
    if not py_files:
        return full_result("no_python_files")

    aliases = _build_aliases(py_files)
    infos = _analyze_modules(py_files, target_files)
    edges = _build_edges(infos, aliases, py_files)
    cli_plugins, cli_opaque = _pytest_cli_plugins(command, aliases)

    resolved_tests = [_resolve_test_id(test_id, framework_name, py_files, aliases, package_dir) for test_id in tests]
    if all(item.unmapped for item in resolved_tests):
        return full_result("test_files_unmapped")

    # Compute file-level import closure for each test once.
    test_closures: dict[str, tuple[set[str], bool, bool, bool, _ResolvedTest]] = {}
    for item in resolved_tests:
        if item.unmapped or cli_opaque:
            test_closures[item.test_id] = (set(), True, True, True, item)
            continue
        seeds = set(item.paths) | cli_plugins
        for p in item.paths:
            seeds |= _ancestor_inits(p, py_files)
            if framework_name == "pytest":
                seeds |= _ancestor_conftests(p, py_files)
        reachable, opaque, dynamic, star = _closure(seeds, edges, infos)
        test_closures[item.test_id] = (reachable, opaque, dynamic, star, item)

    narrow_cache: dict[tuple[str, ...], str | None] = {}

    def make_command(subset: list[str]) -> tuple[list[str], str | None]:
        if not subset:
            return [], None
        closed = _selector_close(framework_name, command, subset, tests)
        if len(closed) >= len(tests):
            return list(tests), None
        key = tuple(closed)
        if key not in narrow_cache:
            narrowed = proof_stability.narrow(framework_name, command, closed, directory=package_dir)
            narrow_cache[key] = (
                narrowed
                if narrowed
                and python_tests.parse(package_scope.within(narrowed) or narrowed, include_owned=True) is not None
                else None
            )
        if narrow_cache[key] is None:
            return list(tests), None
        return closed, narrow_cache[key]

    def for_file(path: str) -> list[str]:
        selected = []
        for test_id in tests:
            reachable, opaque, _, _, item = test_closures[test_id]
            if item.unmapped or opaque or path in reachable:
                selected.append(test_id)
        return selected

    def for_line(path: str, line: int) -> tuple[list[str], str, str | None]:
        file_tests = for_file(path)
        if not file_tests:
            return [], RULE_IMPORT, None
        info = infos.get(path)
        if info is None or info.tree is None or info.opaque or info.dynamic_attr:
            final_tests, cmd = make_command(file_tests)
            rule = RULE_IMPORT if len(final_tests) < len(tests) else RULE_ALL
            return final_tests, rule, cmd

        root_sym, unsafe = _enclosing_target_symbol(info.tree, line)
        if unsafe or root_sym is None:
            final_tests, cmd = make_command(file_tests)
            rule = RULE_IMPORT if len(final_tests) < len(tests) else RULE_ALL
            return final_tests, rule, cmd

        symbols, runs_on_import = _module_symbol_closure(info.tree, root_sym)
        if runs_on_import:
            final_tests, cmd = make_command(file_tests)
            rule = RULE_IMPORT if len(final_tests) < len(tests) else RULE_ALL
            return final_tests, rule, cmd

        # Narrow within file_tests using symbol references in helper modules and test bodies
        symbol_tests: list[str] = []
        for test_id in file_tests:
            reachable, opaque, dynamic, star, item = test_closures[test_id]
            if item.unmapped or opaque or dynamic or star:
                symbol_tests.append(test_id)
                continue
            # Check non-test, non-target reachable helper/conftest/__init__ modules
            helpers = reachable - item.paths - {path}
            if any((infos[h].names & symbols) for h in helpers if h in infos):
                symbol_tests.append(test_id)
                continue
            # Check within the test file(s)
            needed = False
            for t_path in item.paths:
                t_info = infos.get(t_path)
                if t_info is None or t_info.tree is None:
                    needed = True
                    break
                if _test_file_uses_symbols(t_info.tree, item.class_name, item.func_name, symbols):
                    needed = True
                    break
            if needed:
                symbol_tests.append(test_id)

        final_tests, cmd = make_command(symbol_tests)
        if not final_tests:
            return [], RULE_SYMBOL, None
        if len(final_tests) < len(tests):
            return final_tests, RULE_SYMBOL, cmd
        return final_tests, RULE_ALL, None

    return {
        "tests": tests,
        "for_line": for_line,
        "policy": POLICY,
        "fallback": None,
    }


def plan_for_mutants(selection: dict, planned: list[tuple[str, int]]) -> tuple[dict[tuple[str, int], dict], dict]:
    """Materialize per-``(path, line)`` decisions and summary counts for a planned mutant list."""
    if selection.get("fallback") is not None:
        default_entry = selection["default"]
        by_site = {site: dict(default_entry) for site in dict.fromkeys(planned)}
        summary = dict(selection["summary"])
        summary["full_selection_mutants"] = len(planned)
        return by_site, summary

    total = len(selection["tests"])
    for_line = selection["for_line"]
    by_site: dict[tuple[str, int], dict] = {}
    narrowed_count = 0
    unobserved_count = 0
    full_count = 0
    rules: set[str] = set()
    for site in planned:
        if site not in by_site:
            path, line = site
            chosen, rule, cmd = for_line(path, line)
            by_site[site] = {"tests": chosen, "rule": rule, "command": cmd}
        entry = by_site[site]
        rules.add(entry["rule"])
        if not entry["tests"]:
            unobserved_count += 1
        elif len(entry["tests"]) < total and entry["command"]:
            narrowed_count += 1
        else:
            full_count += 1
    return by_site, {
        "policy": POLICY,
        "total_selected_tests": total,
        "narrowed_mutants": narrowed_count,
        "unobserved_mutants": unobserved_count,
        "full_selection_mutants": full_count,
        "rules_used": sorted(rules),
        "fallback": None,
    }
