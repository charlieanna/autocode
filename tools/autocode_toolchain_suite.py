"""The suite a project's own non-Python toolchain declares, and when it outranks a Python suite.

``autocode_verify.detect_framework`` asks here. A Go, Rust or Node project that carries one Python
helper script, fixture or vendored tool is still that project: a stray ``.py`` file must not bind
the run to ``python -m unittest discover`` and hide ``go test ./...`` (#2604). Python keeps priority
whenever the repository declares itself a Python project at its root.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path, PurePosixPath

try:
    from . import autocode_node_tests as node_tests
    from . import autocode_python_tests as python_tests
    from .autocode_test_framework import Framework
except ImportError:
    import autocode_node_tests as node_tests
    import autocode_python_tests as python_tests
    from autocode_test_framework import Framework

# Native pytest policy names are configurations even when empty.
PYTEST_NATIVE_CONFIGS = frozenset(("pytest.ini", ".pytest.ini", "pytest.toml", ".pytest.toml"))
# Root build files that name a non-Python project's own toolchain.
TOOLCHAIN_ROOTS = frozenset(("go.mod", "Cargo.toml", "package.json"))
# Root files that declare a Python project; any one keeps Python first.
PYTHON_ROOTS = frozenset(("pyproject.toml", "setup.py", "setup.cfg", "tox.ini", "Pipfile", "conftest.py"))
PYTHON_REQUIREMENTS = re.compile(r"requirements.*\.(txt|in)$")


def python_is_secondary(files) -> bool:
    """True when the root is another toolchain's project and Python files are only incidental.

    Python stays first when the root declares a Python project, or when Python test modules sit at
    the root or in a top-level ``tests/`` or ``test/`` directory, where the project's own suite lives.
    """
    root_files = {path for path in files if "/" not in path}
    if not root_files & TOOLCHAIN_ROOTS:
        return False
    if root_files & (PYTHON_ROOTS | PYTEST_NATIVE_CONFIGS) or any(map(PYTHON_REQUIREMENTS.match, root_files)):
        return False
    return not any(
        python_tests.TEST_MODULE.match(PurePosixPath(path).name)
        and ("/" not in path or path.split("/", 1)[0] in ("tests", "test"))
        for path in files
    )


def _read(path) -> str:
    try:
        return Path(path).read_text(errors="replace")
    except OSError:
        return ""


def detect(root, files, is_test_path) -> Framework | None:
    """The suite a Go, Node, Rust, Ruby or Make project declares, or None."""
    root = Path(root)
    if "go.mod" in files:
        return Framework("go", "go test ./...")
    node_files = node_tests.test_files(root, [p for p in files if is_test_path(p)])
    if "package.json" in files:
        try:
            package = json.loads(_read(root / "package.json") or "{}")
        except ValueError:
            package = {}
        deps = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
        script = package.get("scripts", {}).get("test", "")
        suite = "npm test --silent" if script and "no test specified" not in script else None
        for name in ("vitest", "jest", "mocha"):
            if name in deps:
                return Framework(name, suite or f"npx --no-install {'vitest run' if name == 'vitest' else name}")
        if node_files or node_tests.command_words(script):
            return Framework(
                "node", suite or "node --test " + " ".join(map(shlex.quote, node_files)), node_files=node_files
            )
        if suite:
            return Framework("npm", suite)
    if node_files:
        return Framework("node", "node --test " + " ".join(map(shlex.quote, node_files)), node_files=node_files)
    if "Cargo.toml" in files:
        return Framework("cargo", "cargo test")
    if "Gemfile" in files and any(p.endswith("_spec.rb") for p in files):
        return Framework("rspec", "bundle exec rspec", runner="bundle exec rspec")
    if "Makefile" in files and re.search(r"^test\s*:", _read(root / "Makefile"), re.M):
        return Framework("make", "make test")
    return None
