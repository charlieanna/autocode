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
GRADLE_ROOTS = project_kinds.GRADLE_FILES
PERL_ROOTS = project_kinds.PERL_ROOTS
# CMake commands are case-insensitive; a project has tests for CTest when it turns testing on.
CMAKE_TESTS = re.compile(r"^[ \t]*(?:enable_testing[ \t]*\(|include[ \t]*\([ \t]*CTest[ \t]*\))", re.I | re.M)
CMAKE_SUITE = "cmake -S . -B build && cmake --build build && ctest --test-dir build --output-on-failure"
# Root build files that name a non-Python project's own toolchain.
TOOLCHAIN_ROOTS = frozenset(
    (
        "go.mod",
        "go.work",
        "Cargo.toml",
        "package.json",
        "pom.xml",
        "CMakeLists.txt",
        "composer.json",
        *PERL_ROOTS,
        *GRADLE_ROOTS,
    )
)
# A Composer project tested with PHPUnit: PHPUnit's configuration at the root, or PHPUnit among the
# project's packages. Composer installs its command as `vendor/bin/phpunit`. Without this a
# PHP bug fix had no suite, the approved plan's first command that names a test file was taken as the
# regression command, and that was a guard (`vendor/bin/phpunit --filter testAnExistingTest
# tests/PagerTest.php`), which passes on the original code: the proof failed twice and the person was
# asked to "correct the saved verification commands" (Claude models, 2026-11-14). Judged by its exit
# code, as Maven is.
PHPUNIT_CONFIGS = frozenset(project_kinds.PHPUNIT_CONFIGS)
PHPUNIT_PACKAGE = "phpunit/phpunit"
PHPUNIT_SUITE = "vendor/bin/phpunit"
# A Makefile's ``check`` rule runs the project's tests where it has no ``test`` rule: the name the GNU
# conventions give that target, and the one most C projects use. A C bug fix whose README said "run the
# tests with `make check`" had no suite: the proof ended UNVERIFIED and the person was asked to "set
# --test-command 'make check' and --regression-command 'make check'" (Claude models, 2026-11-14). In other
# projects ``check`` is often lint alone, so it is the suite only when nothing else is detected, while a
# ``test`` rule keeps its place before the build tools.
# A Perl distribution's tests are the ``.t`` files of its root ``t/`` directory, run with ``prove``, which
# comes with Perl: ``-l`` puts ``lib/`` on the module path and ``-r`` runs the ``.t`` files of every
# directory under ``t/``, so a test file a change adds anywhere there is part of the suite on the candidate
# and on the original code alike. Without this a Perl bug fix had no suite and no test file AutoCode
# knew (``t/pager.t`` was not a test by its name), the proof failed twice with a correct fix and test,
# and the person was asked to "correct the saved verification commands" (Claude models, 2026-11-14).
# Judged by its exit code, as PHPUnit is. A distribution with C code (``.xs``) must be built first, which
# ``prove -l`` does not do, so it is left undetected.
PERL_TEST = re.compile(r"^t/(?:.*/)?[^/]+\.t$")
PERL_MODULE = re.compile(r"^lib/(?:.*/)?[^/]+\.pm$")
PERL_SUITE = "prove -lr t"
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
# What a Ruby project's test suite says about itself, beside a root ``Gemfile``: RSpec's options file and
# the helper ``rspec --init`` writes, and the helper a Minitest suite loads. They declare the suite, as
# ``conftest.py`` and ``pytest.ini`` declare a Python one. A Rails backend with 483 specs also held eight
# Python test modules, one at its root and two under ``tests/``: it was read as a pytest project, so every
# plan that named a spec was refused as a test "the regression proof would never run", three Planner
# answers in a row, and the run paused for the person (GLM, 2026-11-14).
RUBY_SUITE_ROOTS = frozenset((".rspec", "spec/spec_helper.rb", "test/test_helper.rb"))


def python_is_secondary(files) -> bool:
    """True when the root is another toolchain's project and Python files are only incidental.

    Python stays first when the root declares a Python project. Otherwise a Ruby project that declares
    its own suite (``RUBY_SUITE_ROOTS``) is that project wherever Python test modules sit. For any other
    toolchain Python also stays first when Python test modules sit at the root or in a top-level
    ``tests/`` or ``test/`` directory, where the project's own suite lives.
    """
    names = set(files)
    root_files = {path for path in names if "/" not in path}
    if root_files & (PYTHON_ROOTS | PYTEST_NATIVE_CONFIGS) or any(map(PYTHON_REQUIREMENTS.match, root_files)):
        return False
    if "Gemfile" in root_files and names & RUBY_SUITE_ROOTS:
        return True
    if not root_files & TOOLCHAIN_ROOTS:
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
    """The suite a Go, Node, Rust, Ruby, Make, Maven, Gradle, CMake, PHPUnit or Perl project declares, or None.

    A Ruby project with no RSpec suite and no ``make test`` runs the test files of its ``test/``
    directory (``ruby_tests``): without it the proof had no command, and a live run asked the person
    to pass ``--test-command`` (2026-11-13), a question about AutoCode's own machinery.
    """
    root = Path(root)
    if "go.mod" in files:
        return Framework("go", "go test ./...")
    if modules := go_workspace(root, files):
        return Framework("go", "go test " + " ".join(modules))
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
    if make_target(root, files, ("test",)):
        return Framework("make", "make test")
    if found := build_tool(root, files):
        return found
    if found := phpunit(root, files):
        return found
    if perl_tests(files):
        return Framework("prove", PERL_SUITE)
    if ruby_tests(files):
        # Through Bundler when the project pins its gems, as `bundle exec rspec` is above.
        return Framework("minitest", ("bundle exec " if "Gemfile" in files else "") + RUBY_SUITE)
    if make_target(root, files, ("check",)):
        return Framework("make", "make check")
    return None


def go_workspace(root, files) -> list[str]:
    """The package patterns of the modules a root ``go.work`` uses, for a repository with no root ``go.mod``.

    ``go test ./...`` at the root of a workspace of several modules names no package ("directory prefix .
    does not contain modules listed in go.work"), so such a repository had no detected suite and its proof
    had no command (2026-11-14). Each ``use`` directory that holds a tracked ``go.mod`` is tested as
    ``./DIR/...``, in the order ``go.work`` lists them; one outside the repository is left out.
    """
    if "go.work" not in files:
        return []
    patterns: list[str] = []
    block = False
    for raw in _read(Path(root) / "go.work").splitlines():
        words = raw.split("//", 1)[0].split()
        if not words:
            continue
        if block and words[0] == ")":
            block = False
        elif words[:2] == ["use", "("]:
            block = True
        elif block or (words[0] == "use" and len(words) > 1):
            directory = PurePosixPath(words[0 if block else 1].strip('"'))
            if directory.is_absolute() or ".." in directory.parts:
                continue
            name = directory.as_posix()
            pattern = "./..." if name == "." else f"./{name}/..."
            if str(directory / "go.mod") in files and pattern not in patterns:
                patterns.append(pattern)
    return patterns


def make_target(root, files, names) -> str | None:
    """The first of ``names`` the root ``Makefile`` has a rule for, or None. A rule, never a variable
    assigned with ``:=``."""
    if "Makefile" not in files:
        return None
    text = _read(Path(root) / "Makefile")
    return next((name for name in names if re.search(rf"^{name}[ \t]*:(?!=)", text, re.M)), None)


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


def phpunit(root, files) -> Framework | None:
    """The suite of a Composer project at ``root`` that is tested with PHPUnit, or None."""
    if "composer.json" not in files:
        return None
    if not PHPUNIT_CONFIGS.intersection(files):
        try:
            package = json.loads(_read(Path(root) / "composer.json") or "{}")
        except ValueError:
            return None
        tables = [package.get(name) for name in ("require", "require-dev")] if isinstance(package, dict) else []
        if not any(isinstance(table, dict) and PHPUNIT_PACKAGE in table for table in tables):
            return None
    return Framework("phpunit", PHPUNIT_SUITE)


def perl_tests(files) -> list[str]:
    """The ``.t`` files under the root ``t/`` directory of a pure-Perl distribution, or [] for any other
    project: one with a Perl root file or a module under ``lib/``, and no ``.xs`` file."""
    names = set(files)
    if not (names & set(PERL_ROOTS) or any(map(PERL_MODULE.match, names))):
        return []
    if any(path.endswith(".xs") for path in names):
        return []
    return sorted(path for path in names if PERL_TEST.match(path))


def ruby_tests(files) -> list[str]:
    """The Ruby test files under the root ``test/`` directory, helpers left out."""
    return sorted(path for path in files if RUBY_TEST.match(path) and PurePosixPath(path).name != RUBY_HELPER)


def with_project_build(framework, detect, tree):
    """``framework``, a ``node --test`` command given as text, with the build steps its project's own test
    script runs before it (``tsc -p tsconfig.test.json && node --test ...``), read from the suite ``detect``
    finds in the project the command runs in. A detected suite carries them (``detect`` above); the same
    tests given as a command, as the original-test gate replays them, ran against a missing build and could
    not load (2026-11-21)."""
    if framework is None or framework.name != "node" or getattr(framework, "setup", None):
        return framework
    directory = getattr(framework, "directory", None)
    found = detect(Path(tree) / directory if directory else Path(tree))
    if found is not None and found.name == "node" and getattr(found, "setup", None):
        framework.setup = found.setup
    return framework
