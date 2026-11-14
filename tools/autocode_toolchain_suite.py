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
    from . import autocode_project_kinds as project_kinds
    from . import autocode_python_tests as python_tests
    from .autocode_test_framework import Framework
except ImportError:
    import autocode_node_tests as node_tests
    import autocode_project_kinds as project_kinds
    import autocode_python_tests as python_tests
    from autocode_test_framework import Framework

# Native pytest policy names are configurations even when empty.
PYTEST_NATIVE_CONFIGS = frozenset(("pytest.ini", ".pytest.ini", "pytest.toml", ".pytest.toml"))
# A build tool whose one command builds the project and runs every test it has: Maven, Gradle, and CMake
# with CTest. Without these a Java or C++ project had no suite at all, and the approved plan's own command
# (`mvn test`) names no test file, so it could not be adopted either (autocode_plan_proof_command): real
# bug fixes in a Maven project and a CMake project delivered correct code and then asked the person to
# pass --test-command and --regression-command (2026-11-14, docs/bugs/2026-11-13-task-kind-survey.md).
# Each is judged by its exit code, as `make test` is. The project's own wrapper runs when it has one.
GRADLE_ROOTS = ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")
# CMake commands are case-insensitive; a project has tests for CTest when it turns testing on.
CMAKE_TESTS = re.compile(r"^[ \t]*(?:enable_testing[ \t]*\(|include[ \t]*\([ \t]*CTest[ \t]*\))", re.I | re.M)
CMAKE_SUITE = "cmake -S . -B build && cmake --build build && ctest --test-dir build --output-on-failure"
# Root build files that name a non-Python project's own toolchain.
TOOLCHAIN_ROOTS = frozenset(("go.mod", "Cargo.toml", "package.json", "pom.xml", "CMakeLists.txt", *GRADLE_ROOTS))
# Ruby's own test files (Minitest, test-unit) in the conventional root ``test/`` directory. A helper
# such as test_helper.rb is loaded by the tests that need it, never as a test file itself.
RUBY_TEST = re.compile(r"^test/(?:.*/)?(?:.*_test|test_.*)\.rb$")
RUBY_HELPER = "test_helper.rb"
# Loads every such file that exists when it runs, so a test file a change adds is part of the suite on
# the candidate and on the original code alike; each file's own `require "minitest/autorun"` runs it.
RUBY_SUITE = (
    "ruby -Ilib -Itest -e "
    """'Dir.glob("test/**/{*_test,test_*}.rb").sort.each { |f| require File.expand_path(f) unless File.basename(f) == "test_helper.rb" }'"""
)
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
    """The suite a Go, Node, Rust, Ruby, Make, Maven, Gradle or CMake project declares, or None.

    A Ruby project with no RSpec suite and no ``make test`` runs the test files of its ``test/``
    directory (``ruby_tests``): without it the proof had no command, and a live run asked the person
    to pass ``--test-command`` (2026-11-13), a question about AutoCode's own machinery.
    """
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
        if steps := node_tests.script_steps(script):
            # `BUILD && node --test ...`: a targeted run builds first, as the script does.
            return Framework(
                "node", suite, node_files=node_files, invocation=steps[1], setup=node_tests.build_command(steps[0])
            )
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
    if "Gemfile" in files and any(project_kinds.collected_by(p) == "ruby" for p in files):
        return Framework("rspec", "bundle exec rspec", runner="bundle exec rspec")
    if "Makefile" in files and re.search(r"^test\s*:", _read(root / "Makefile"), re.M):
        return Framework("make", "make test")
    if found := build_tool(root, files):
        return found
    if ruby_tests(files):
        # Through Bundler when the project pins its gems, as `bundle exec rspec` is above.
        return Framework("minitest", ("bundle exec " if "Gemfile" in files else "") + RUBY_SUITE)
    return None


def build_tool(root, files) -> Framework | None:
    """The suite of a Maven, Gradle or CMake project at ``root``, or None (see GRADLE_ROOTS)."""
    if "pom.xml" in files:
        return Framework("maven", ("./mvnw" if "mvnw" in files else "mvn") + " -B -q test")
    if any(name in files for name in GRADLE_ROOTS):
        # No daemon: a check's process ends with its command.
        return Framework("gradle", ("./gradlew" if "gradlew" in files else "gradle") + " --no-daemon -q test")
    if "CMakeLists.txt" in files and CMAKE_TESTS.search(_read(Path(root) / "CMakeLists.txt")):
        return Framework("cmake", CMAKE_SUITE)
    return None


def ruby_tests(files) -> list[str]:
    """The Ruby test files under the root ``test/`` directory, helpers left out."""
    return sorted(path for path in files if RUBY_TEST.match(path) and PurePosixPath(path).name != RUBY_HELPER)
