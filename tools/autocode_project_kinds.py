"""What each kind of project looks like: one table, read by every module that needs to know.

AutoCode decides which files are tests, which runner runs them, which files make a directory a
project and which files a toolchain writes on its own from what it knows about each language. That
knowledge was copied into several modules, each with its own hand-written list, and every language
or layout one copy missed was a separate bug found on a live run (a Ruby Minitest fix, a Rust
``Cargo.lock``; docs/bugs/2026-11-13-shared-root-causes.md). It lives here now, one row per
language, and the modules derive what they need from the rows:

* ``autocode_verify.TEST_NAME`` and ``CODE_SUFFIXES``: a file's role by its name and suffix;
* ``autocode_python_test_roles`` and ``autocode_verify``: the directories that hold tests;
* ``autocode_test_examples``: the test files shown to a stage as examples;
* ``autocode_plan_test_suite``: which runner runs which planned test files, and how they are named;
* ``autocode_test_framework.Framework``: which runners report per-test results, and which files a
  targeted run is given;
* ``autocode_package_scope.MANIFESTS``: the files that make a directory a project of its own;
* ``autocode_toolchain_byproducts.LOCKFILES``, ``RUN_FILES`` and ``BUILD_DIRS``: the lockfiles, the
  files a tool writes whenever it runs and the build directories a toolchain writes on its own.

Adding a language, or a test layout of one, is a change to its row here. Pure data and pure
functions; imports nothing from AutoCode, so any layer may read it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath


@dataclass(frozen=True)
class Language:
    """One kind of project. Every name pattern is a regular expression over a file's base name."""

    name: str
    # The source files of this language, by suffix: code, never a document or data file.
    suffixes: tuple[str, ...]
    # Files that belong to its test suite by their name. Case-sensitive: Latest.java is product code.
    test_names: tuple[str, ...] = ()
    # Files only tests use that are not tests themselves (conftest.py, a Jest snapshot).
    support_names: tuple[str, ...] = ()
    # The test files its targeted runners collect by default, when narrower than ``test_names``
    # (pytest's ``test_*.py`` and ``*_test.py``; RSpec's ``*_spec.rb``, not Minitest's files).
    collected: str | None = None
    # The test files a targeted command is given (``Framework.targeted``); ``collected`` when None.
    targets: str | None = None
    # How the plan check names the tests its runners run ("runs RSpec tests").
    shown: str = ""
    # Runners (``Framework.name``) that run ``collected`` files one by one.
    runners: tuple[str, ...] = ()
    # Runners whose results name each test, so a criterion can be matched to one by name; a runner of
    # the whole suite only (cargo) may be one without being in ``runners``.
    per_test: tuple[str, ...] = ()
    # Root files that make a directory a project of this language, with a suite detection finds there.
    manifests: tuple[str, ...] = ()
    # Lockfiles its toolchain writes on its own beside a manifest the first time it resolves the
    # project: the tool's by-product, not an edit by a stage (autocode_toolchain_byproducts).
    lockfiles: dict[str, str] = field(default_factory=dict)
    # Files one of its tools writes on its own whenever it runs, beside one of these files (PHPUnit's
    # result cache beside its configuration): a by-product too (autocode_toolchain_byproducts).
    run_files: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Tool cache directories generated at runtime (ignored by workspace inventory/snapshots).
    cache_dirs: tuple[str, ...] = ()
    # Compiled bytecode file suffixes generated at runtime.
    bytecode_suffixes: tuple[str, ...] = ()
    # Loading prerequisites beside a manifest that tests require and must be kept fixed (e.g. go.mod, go.sum).
    test_prerequisites: tuple[str, ...] = ()
    # Directories its build tool creates on its own beside one of these manifests and fills with
    # build output (Maven's and Cargo's target/, Gradle's build/ and .gradle/, the build/ a CMake
    # project is configured into): the tool's by-product too (autocode_toolchain_byproducts).
    build_dirs: dict[str, tuple[str, ...]] = field(default_factory=dict)


def _xunit(suffix: str) -> tuple[str, ...]:
    """JUnit-style test class names: FooTest, FooTests, TestFoo."""
    return (rf".*Tests?\.{suffix}", rf"Test[A-Z_]\w*\.{suffix}")


# The root files of a Gradle build.
GRADLE_FILES = ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")
_JVM_BUILD_DIRS: dict[str, tuple[str, ...]] = {"target": ("pom.xml",), "build": GRADLE_FILES, ".gradle": GRADLE_FILES}
_CMAKE_BUILD_DIRS: dict[str, tuple[str, ...]] = {"build": ("CMakeLists.txt",)}

# The root files of a PHP project tested with PHPUnit: its configuration, or Composer's manifest.
PHPUNIT_CONFIGS = ("phpunit.xml", "phpunit.xml.dist", "phpunit.dist.xml")
_PHPUNIT_ROOTS = (*PHPUNIT_CONFIGS, "composer.json")

# Directories that hold tests wherever they appear, and ones that do only at the repository root:
# numpy/testing/ and django/test/ are shipped product code, while a top-level test/ is not.
TEST_DIRS = frozenset({"tests", "__tests__", "__snapshots__", "testdata", "test_data"})
ROOT_TEST_DIRS = frozenset({"test", "spec"})

LANGUAGES: tuple[Language, ...] = (
    Language(
        "python",
        (".py", ".pyi"),
        test_names=(r"test_.*\.py", r".*_tests?\.py"),
        support_names=(r"conftest\.py",),
        collected=r"(test_.*|.*_test)\.py",
        targets=r"(tests|test_.*|.*_tests?)\.py",
        shown="Python",
        runners=("pytest", "unittest"),
        per_test=("pytest", "unittest"),
        manifests=("pyproject.toml", "setup.py", "setup.cfg"),
        lockfiles={"uv.lock": "pyproject.toml", "poetry.lock": "pyproject.toml", "pdm.lock": "pyproject.toml"},
        cache_dirs=("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"),
        bytecode_suffixes=(".pyc", ".pyo"),
    ),
    Language(
        "go",
        (".go",),
        test_names=(r".*_test\.go",),
        collected=r".*_test\.go",
        shown="Go",
        runners=("go",),
        per_test=("go",),
        manifests=("go.mod",),
        lockfiles={"go.sum": "go.mod"},
        test_prerequisites=("go.mod", "go.sum"),
    ),
    Language(
        "ruby",
        (".rb",),
        test_names=(r".*_(spec|test)\.rb", r"test_.*\.rb"),
        collected=r".*_spec\.rb",
        shown="RSpec",
        runners=("rspec",),
        per_test=("rspec", "minitest"),
        manifests=("Gemfile",),
        lockfiles={"Gemfile.lock": "Gemfile"},
    ),
    Language(
        "javascript",
        (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"),
        test_names=(r".*\.(test|spec)\.[cm]?[jt]sx?",),
        support_names=(r".*\.snap",),
        collected=r".*\.(test|spec)\.[cm]?[jt]sx?",
        shown="JavaScript or TypeScript",
        runners=("node", "jest", "vitest", "mocha"),
        per_test=("node", "vitest", "jest", "mocha"),
        manifests=("package.json",),
        lockfiles={
            "package-lock.json": "package.json",
            "npm-shrinkwrap.json": "package.json",
            "yarn.lock": "package.json",
            "pnpm-lock.yaml": "package.json",
            "bun.lock": "package.json",
            "bun.lockb": "package.json",
        },
    ),
    Language(
        "rust",
        (".rs",),
        # cargo test names every test it runs (autocode_cargo_tests), but builds every test target of the
        # package together, so it is never targeted file by file and no plan file kind is collected for it.
        per_test=("cargo",),
        manifests=("Cargo.toml",),
        lockfiles={"Cargo.lock": "Cargo.toml"},
        build_dirs={"target": ("Cargo.toml",)},
    ),
    Language("java", (".java",), test_names=_xunit("java"), build_dirs=_JVM_BUILD_DIRS),
    Language("kotlin", (".kt", ".kts"), test_names=_xunit("kt"), build_dirs=_JVM_BUILD_DIRS),
    Language("scala", (".scala",), test_names=_xunit("scala"), build_dirs=_JVM_BUILD_DIRS),
    Language("swift", (".swift",), test_names=_xunit("swift")),
    Language("csharp", (".cs",), test_names=_xunit("cs")),
    Language("shell", (".sh",), test_names=(r"test_.*\.sh",)),
    Language("c", (".c", ".h"), build_dirs=_CMAKE_BUILD_DIRS),
    Language("cpp", (".cc", ".cpp", ".hpp"), build_dirs=_CMAKE_BUILD_DIRS),
    Language(
        "php",
        (".php",),
        # PHPUnit runs the classes of files named FooTest.php. It names no test in a form AutoCode reads,
        # so its suite is judged by its exit code and no plan file kind is collected for it.
        test_names=(r".*Test\.php",),
        manifests=("composer.json",),
        lockfiles={"composer.lock": "composer.json"},
        # Where PHPUnit keeps the last run's results: a file in the project root, or the directory its
        # configuration names (`cacheDirectory=".phpunit.cache"`, what `phpunit --generate-configuration` writes).
        run_files={".phpunit.result.cache": _PHPUNIT_ROOTS},
        build_dirs={".phpunit.cache": _PHPUNIT_ROOTS},
    ),
    Language("objective-c", (".m", ".mm")),
    Language("elixir", (".ex", ".exs")),
    Language("erlang", (".erl",)),
    Language("haskell", (".hs",)),
    Language("ocaml", (".ml",)),
    Language("lua", (".lua",)),
    Language("perl", (".pl",)),
    Language("dart", (".dart",)),
    Language("zig", (".zig",)),
)
BY_NAME = {language.name: language for language in LANGUAGES}


def _whole(patterns) -> re.Pattern:
    return re.compile("^(" + "|".join(patterns) + ")$")


# Every source suffix any language has.
CODE_SUFFIXES = frozenset(suffix for language in LANGUAGES for suffix in language.suffixes)
# A test file by its name, in any language.
TEST_FILE = _whole(pattern for language in LANGUAGES for pattern in language.test_names)
# A test file or a file only tests use, by its name: the test role (autocode_verify.TEST_NAME).
TEST_ROLE = _whole(pattern for language in LANGUAGES for pattern in (*language.test_names, *language.support_names))
# The files that make a directory a project of its own.
MANIFESTS = tuple(manifest for language in LANGUAGES for manifest in language.manifests)
# Lockfiles a toolchain writes on its own, and the manifest it resolves beside it.
LOCKFILES = {name: manifest for language in LANGUAGES for name, manifest in language.lockfiles.items()}
# A file a tool writes on its own whenever it runs, and the files it is written beside.
RUN_FILES = {name: roots for language in LANGUAGES for name, roots in language.run_files.items()}
# Cache directories and bytecode suffixes generated at runtime by language toolchains.
CACHE_DIRS = frozenset(name for language in LANGUAGES for name in language.cache_dirs)
BYTECODE_DIRS = frozenset(name for language in LANGUAGES for name in language.cache_dirs if not name.startswith("."))
BYTECODE_SUFFIXES = tuple(dict.fromkeys(suffix for language in LANGUAGES for suffix in language.bytecode_suffixes))
# Loading prerequisite files beside manifests that tests depend on.
TEST_PREREQUISITES = {
    language.name: language.test_prerequisites for language in LANGUAGES if language.test_prerequisites
}
ALL_TEST_PREREQUISITES = frozenset(name for language in LANGUAGES for name in language.test_prerequisites)
# A directory a build tool creates on its own, and the manifests it is created beside.
BUILD_DIRS = {
    name: tuple(dict.fromkeys(m for language in LANGUAGES for m in language.build_dirs.get(name, ())))
    for name in dict.fromkeys(name for language in LANGUAGES for name in language.build_dirs)
}
# Runners whose results name each test.
PER_TEST_RUNNERS = frozenset(runner for language in LANGUAGES for runner in language.per_test)
# The language whose test files each targeted runner runs.
RUNNER_LANGUAGE = {runner: language.name for language in LANGUAGES for runner in language.runners}
# Each targeted language and the test files its runners collect.
COLLECTED = tuple(
    (language.name, re.compile(rf"^{language.collected}$")) for language in LANGUAGES if language.collected
)


def is_test_file(path) -> bool:
    """Whether ``path`` is a test file by its name alone, in any language (not a support file)."""
    return bool(TEST_FILE.match(PurePosixPath(str(path)).name))


def targets(runner: str) -> re.Pattern:
    """The base names of the test files a targeted ``runner`` command is given."""
    language = BY_NAME[RUNNER_LANGUAGE[runner]]
    return re.compile(rf"^{language.targets or language.collected}$")


def collected_by(path) -> str | None:
    """The language whose targeted runners collect ``path`` as a test file, or None."""
    name = PurePosixPath(str(path)).name
    return next((found for found, pattern in COLLECTED if pattern.match(name)), None)
