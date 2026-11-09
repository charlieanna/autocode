"""Pinned Python product/test roles, independent of filename hints.

Only positive original runtime ownership removes a protected entry. Unknown
unchanged entries stay protected; an unknown changed entry grants no overlay.
"""

from __future__ import annotations

import ast
import hashlib
import posixpath
from dataclasses import dataclass
from pathlib import PurePosixPath

try:
    from . import autocode_python_packaging as packaging
    from . import autocode_python_tests as python_tests
except ImportError:
    import autocode_python_packaging as packaging
    import autocode_python_tests as python_tests

VERSION = 3
PRODUCT, TEST, AMBIGUOUS = "product", "test", "ambiguous"
TEST_DIRS = frozenset({"tests", "__tests__", "__snapshots__", "testdata", "test_data"})


def _test_tree(path):
    parts = PurePosixPath(path).parts
    return any(part in TEST_DIRS for part in parts[:-1]) or (len(parts) > 1 and parts[0] in ("test", "spec"))


def _declares(source):
    try:
        nodes = ast.parse(source).body
    except (SyntaxError, TypeError, ValueError):
        return False
    assertion_oracle = any(
        isinstance(node, ast.Import) and any(alias.name == "unittest" for alias in node.names) for node in nodes
    ) and any(isinstance(node, ast.Assert) for node in nodes)
    return (
        assertion_oracle
        or python_tests.unittest_package(source)
        or any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test")
            or isinstance(node, ast.ClassDef)
            and node.name.startswith("Test")
            and any(
                isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name.startswith("test")
                for child in node.body
            )
            for node in nodes
        )
    )


def _imports(path, source):
    """Literal ordinary imports only; computed imports do not prove exclusion."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, TypeError, ValueError):
        return set(), True
    modules, uncertain = set(), False
    parent = list(PurePosixPath(path).parent.parts)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level > len(parent):
                uncertain = True
                continue
            prefix = parent[: len(parent) - node.level + 1] if node.level else []
            prefix += (node.module or "").split(".") if node.module else []
            module = ".".join(prefix)
            if module:
                modules.add(module)
            modules.update(".".join([*prefix, alias.name]) for alias in node.names if alias.name != "*")
        elif isinstance(node, ast.Call) and (
            isinstance(node.func, ast.Name)
            and node.func.id in ("__import__", "import_module")
            or isinstance(node.func, ast.Attribute)
            and node.func.attr == "import_module"
        ):
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                modules.add(node.args[0].value)
            else:
                uncertain = True
    return modules, uncertain


def _runtime_reach(paths, read, hint, ownership, potential):
    if not potential:
        return set(), False
    aliases = {}
    for path in paths:
        if path.endswith(".py"):
            module = path[:-3].replace("/", ".").removesuffix(".__init__")
            aliases.setdefault(module, set()).add(path)
            aliases.setdefault(PurePosixPath(path).stem, set()).add(path)
            for package, directory in ownership.aliases:
                if str(PurePosixPath(path).parent) == directory:
                    leaf = PurePosixPath(path).stem
                    logical = package if leaf == "__init__" else package + "." + leaf
                    aliases.setdefault(logical, set()).add(path)
    queue = [path for path in paths if path.endswith(".py") and (not hint(path) or ownership.contains(path))]
    seen, reached, uncertain = set(), set(), False
    while queue:
        path = queue.pop()
        if path in seen:
            continue
        seen.add(path)
        modules, dynamic = _imports(path, read(path))
        uncertain |= dynamic
        for module in modules:
            targets = aliases.get(module, set())
            if len(targets) > 1 and targets & potential:
                uncertain = True
            if len(targets) == 1:
                target = next(iter(targets))
                reached.add(target)
                queue.append(target)
    return reached, uncertain


@dataclass(frozen=True)
class Roles:
    roles: dict[str, str]
    metadata: dict[str, str]
    unsupported: str
    original_products: frozenset[str]
    candidate_sources: dict[str, str | None]
    original_test_links: dict[str, str]
    eligible_test_links: frozenset[str]
    candidate_links: dict[str, str | None]

    def role(self, path):
        return self.roles.get(path, AMBIGUOUS)

    def protected(self, path):
        return path not in self.original_products

    def partition(self, paths):
        return tuple([path for path in paths if self.role(path) == role] for role in (TEST, PRODUCT, AMBIGUOUS))

    def is_test(self, path):
        role = self.role(path)
        if role == AMBIGUOUS:
            raise ValueError(f"Python product/test ownership is ambiguous for changed path {path}")
        return role == TEST

    def selectors(self, paths):
        """Keep original collector identities when their linked oracle bytes change."""
        selected = set(paths)
        selected.update(
            alias
            for alias, endpoint in self.original_test_links.items()
            if endpoint in selected
            and alias in self.eligible_test_links
            and self.role(alias) == self.role(endpoint) == TEST
        )
        return sorted(selected)


def _endpoint(path, paths, links):
    seen = set()
    while path in links:
        if path in seen:
            return None
        seen.add(path)
        target = links[path]
        if not isinstance(target, str) or PurePosixPath(target).is_absolute():
            return None
        path = posixpath.normpath(posixpath.join(str(PurePosixPath(path).parent), target))
        if path.startswith("../") or ".git" in PurePosixPath(path).parts or path not in paths:
            return None
    return path


def _classify(paths, read, hint, ownership, links, *, original_tests=None):
    def source(path):
        endpoint = _endpoint(path, paths, links)
        return read(endpoint) if endpoint is not None else ""

    potential = {p for p in paths if p.endswith(".py") and hint(p) and not _test_tree(p) and not ownership.contains(p)}
    reached, dynamic = _runtime_reach(paths, source, hint, ownership, potential)
    runtime_targets = {_endpoint(path, paths, links) for path in paths if ownership.contains(path) or path in reached}
    oracle_targets = {
        endpoint
        for path in links
        if hint(path)
        and (endpoint := _endpoint(path, paths, links)) is not None
        and endpoint not in runtime_targets
        and not dynamic
        and (original_tests is None or endpoint in original_tests)
        and _declares(source(endpoint))
    }
    result = {}
    for path in paths:
        if not path.endswith(".py") or not hint(path):
            result[path] = TEST if hint(path) else PRODUCT
            continue
        test_root = _test_tree(path) or PurePosixPath(path).name == "conftest.py"
        if test_root and not (ownership.contains(path) or path in reached):
            result[path] = TEST
            continue
        declared = _declares(source(path))
        if ownership.contains(path) or path in reached:
            result[path] = AMBIGUOUS if declared or test_root else PRODUCT
        elif test_root or declared and not dynamic:
            result[path] = TEST
        else:
            result[path] = AMBIGUOUS
    for path in links:
        endpoint = _endpoint(path, paths, links)
        if endpoint is None:
            result[path] = AMBIGUOUS
        elif endpoint in oracle_targets:
            result[path] = result[endpoint] = TEST
        elif result.get(endpoint) == PRODUCT:
            declared = _declares(source(endpoint))
            if declared or PurePosixPath(path).name == "conftest.py":
                result[path] = result[endpoint] = AMBIGUOUS
            else:
                result[path] = PRODUCT
        elif result.get(endpoint) == TEST:
            result[path] = TEST if hint(path) and not ownership.contains(path) else AMBIGUOUS
        else:
            result[path] = AMBIGUOUS
    for path in links:
        if result.get(_endpoint(path, paths, links)) == AMBIGUOUS:
            result[path] = AMBIGUOUS
    runtime = {path for path in paths if ownership.contains(path) or path in reached}
    runtime.update(path for path in links if _endpoint(path, paths, links) in runtime)
    return result, runtime


def build(paths, read, hint, *, candidates=None, links=None, candidate_links=None):
    """Freeze original roles before overlaying only added/changed test definitions.

    ``read`` reads the pinned original tree. Candidate metadata cannot relax
    original ownership, and existing ambiguous entries cannot become tests just
    because the candidate adds a declaration. Deleted paths retain original roles.
    """
    paths = frozenset(paths)
    links = links or {}
    manifest = read("pyproject.toml") if "pyproject.toml" in paths else None
    ownership = (
        packaging.Ownership(reason="executable or legacy setup configuration is not interpreted")
        if "setup.py" in paths or "setup.cfg" in paths
        else packaging.declared(manifest)
    )
    roles, runtime = _classify(paths, read, hint, ownership, links)
    original_products = frozenset(path for path in runtime if roles[path] == PRODUCT)
    original_test_links = {
        path: endpoint
        for path in links
        if hint(path)
        and roles.get(path) == TEST
        and (endpoint := _endpoint(path, paths, links)) is not None
        and roles.get(endpoint) == TEST
    }
    candidates = candidates or {}
    current_paths = (paths | candidates.keys()) - {path for path, source in candidates.items() if source is None}
    current_links = {
        path: target
        for path, target in {**links, **(candidate_links or {})}.items()
        if target is not None and path in current_paths
    }
    if candidates:
        current, runtime = _classify(
            current_paths,
            lambda p: candidates.get(p, read(p) if p in paths else ""),
            hint,
            ownership,
            current_links,
            original_tests={path for path, role in roles.items() if role == TEST},
        )
        for path, role in current.items():
            old = roles.get(path)
            if old is None:
                roles[path] = role
            elif old == TEST and path in candidates and path not in runtime:
                continue  # Removing declarations cannot erase an original oracle.
            elif path in candidates and (old == AMBIGUOUS or old != role):
                roles[path] = AMBIGUOUS
    metadata = {"pyproject.toml": hashlib.sha256(manifest.encode()).hexdigest()} if manifest is not None else {}
    eligible = frozenset(
        alias
        for alias, endpoint in original_test_links.items()
        if alias in current_links and _endpoint(alias, current_paths, current_links) == endpoint
    )
    return Roles(
        roles,
        metadata,
        ownership.reason,
        original_products,
        dict(candidates),
        original_test_links,
        eligible,
        dict(candidate_links or {}),
    )


def proof_current(proof, protected_binding):
    """Old classification cannot grant current proof or skip independent validation."""
    context = proof.get("execution_context") or {}
    return (
        isinstance(context, dict)
        and type(context.get("test_role_policy")) is int
        and context["test_role_policy"] == VERSION
        and context.get("protected_test_binding") == protected_binding
    )


def _text(value, limit):
    return value[:limit] if isinstance(value, str) else None


def _reference_text(value, limit=2048):
    # An overlong artifact locator is omitted, never shortened into another path.
    return value if isinstance(value, str) and len(value) <= limit else None


def _record_reference(value):
    record = value if isinstance(value, dict) else {}
    files = record.get("files")
    command = record.get("command")
    count = len(files) if isinstance(files, dict) else record.get("file_count")
    return {
        "binding_hash": _reference_text(record.get("binding_hash"), 128),
        "file_count": count if type(count) is int and 0 <= count <= 1_000_000_000 else None,
        "archive": _reference_text(record.get("root", record.get("archive"))),
        "inventory": _reference_text(record.get("inventory_path", record.get("inventory"))),
        "command_sha256": hashlib.sha256(command.encode()).hexdigest()
        if isinstance(command, str)
        else _reference_text(record.get("command_sha256"), 128),
    }


def correction_summary(event):
    """Bounded display references; never substitute for retained authority or evidence."""
    previous, current = _record_reference(event.get("previous")), _record_reference(event.get("current"))
    proof = event.get("previous_regression_proof", event.get("retired_proof")) or {}
    proof = proof if isinstance(proof, dict) else {}
    return {
        "kind": "protected_tests_roles_corrected",
        "actor": _text(event.get("actor"), 80),
        "at": _text(event.get("at"), 80),
        "reason": _text(event.get("reason"), 512),
        "base_commit": _text(event.get("base_commit"), 128),
        "previous": previous,
        "current": current,
        "same_command": previous["command_sha256"] == current["command_sha256"],
        "retired_proof": {
            key: _reference_text(proof.get(key), 2048 if key == "path" else 128)
            for key in ("path", "receipt_sha256", "source_revision", "verdict")
        },
        "meaning": "Historical inventory correction; fresh runner proof and independent verification remain required.",
    }


def latest_correction(events, binding_hash=None):
    for event in reversed(events or []):
        if (
            isinstance(event, dict)
            and event.get("kind") == "protected_tests_roles_corrected"
            and (binding_hash is None or (event.get("current") or {}).get("binding_hash") == binding_hash)
        ):
            return correction_summary(event)
    return None
