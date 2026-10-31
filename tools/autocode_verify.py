"""Model-free verification of a bug-fix candidate.

The runner, not a model, decides whether a fix is proven. Two properties are
checked by executing the project's own tests in scratch worktrees:

* Regression proof (fail-to-pass). The candidate's new or changed test files
  are run twice with identical test code: once over the base revision's source
  and once over the candidate's source. Because the only difference between the
  two trees is the non-test change, a flip is caused by the fix, not by the
  tests. With per-test results, a proof is a named test that ran and failed on
  base and ran and *passed* on the candidate; a module that merely fails to
  import on base, or a test that is skipped or deselected, proves nothing.
* No regressions (pass-to-pass). Every test that passed on base must still
  pass on the candidate: not fail, and not be skipped, deselected or missing.
  Without per-test results the exit code decides when base was green, and the
  outcome stays UNVERIFIED when it was not.

The builder's workspace is never executed in: runs happen in detached worktrees
assembled from the recorded diff, and the candidate snapshot is checked before
and after, so a verdict belongs to exactly one candidate. Commands that come
from the Builder's own report can inform its repair but never produce PASS.
Missing evidence is UNVERIFIED, never PASS.
"""

from __future__ import annotations

try:
    from . import autocode_joint_inputs as joint_inputs
    from . import autocode_native_check_execution as native_checks
    from . import autocode_source_snapshot as source_snapshot
except ImportError:
    import autocode_joint_inputs as joint_inputs
    import autocode_native_check_execution as native_checks
    import autocode_source_snapshot as source_snapshot

import contextlib
import hashlib
import json
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

try:
    from . import autocode_agent_env as agent_env
    from . import autocode_command_receipt as command_receipt
    from . import autocode_command_supervision as command_supervision
    from . import autocode_diff_coverage as diff_coverage
    from . import autocode_first_suite as first_suite
    from . import autocode_go_tests as go_tests
    from . import autocode_investigation_workspace as investigation_workspace
    from . import autocode_junit_first_suite as junit_first
    from . import autocode_junit_proof as junit_proof
    from . import autocode_junit_tests as junit_tests
    from . import autocode_node_tests as node_tests
    from . import autocode_package_scope as package_scope
    from . import autocode_project_inventory as project_inventory
    from . import autocode_prompts as prompts
    from . import autocode_proof_seam as proof_seam
    from . import autocode_proof_stability as proof_stability
    from . import autocode_python_tests as python_tests
    from . import autocode_review_mutation as review_mutations
    from . import autocode_review_proof as review_proof
    from . import autocode_scratch_overlay as scratch_overlay
    from . import autocode_scratch_preparation as scratch_preparation
    from . import autocode_suite_definition_inputs as definition_inputs
    from . import autocode_suite_definition_policy as definition_policy
    from . import autocode_suite_definition_scan as definition_scan
    from . import autocode_test_environment as test_env
    from . import autocode_test_root as test_roots
    from . import autocode_test_setup as test_setup
    from . import autocode_util as util
    from . import autocode_verification_preparation as preparation
    from . import autocode_verification_schedule as schedule
    from . import autocode_vitest_tests as vitest_tests
    from .autocode_suite_health import suite_health
    from .autocode_test_framework import Framework
    from .autocode_verification_commands import _go_test, command_framework, select_commands
    from .autocode_verification_commands import _mentions_tests as _mentions_tests
except ImportError:
    import autocode_agent_env as agent_env
    import autocode_command_receipt as command_receipt
    import autocode_command_supervision as command_supervision
    import autocode_diff_coverage as diff_coverage
    import autocode_first_suite as first_suite
    import autocode_go_tests as go_tests
    import autocode_investigation_workspace as investigation_workspace
    import autocode_junit_first_suite as junit_first
    import autocode_junit_proof as junit_proof
    import autocode_junit_tests as junit_tests
    import autocode_node_tests as node_tests
    import autocode_package_scope as package_scope
    import autocode_project_inventory as project_inventory
    import autocode_prompts as prompts
    import autocode_proof_seam as proof_seam
    import autocode_proof_stability as proof_stability
    import autocode_python_tests as python_tests
    import autocode_review_mutation as review_mutations
    import autocode_review_proof as review_proof
    import autocode_scratch_overlay as scratch_overlay
    import autocode_scratch_preparation as scratch_preparation
    import autocode_suite_definition_inputs as definition_inputs
    import autocode_suite_definition_policy as definition_policy
    import autocode_suite_definition_scan as definition_scan
    import autocode_test_environment as test_env
    import autocode_test_root as test_roots
    import autocode_test_setup as test_setup
    import autocode_util as util
    import autocode_verification_preparation as preparation
    import autocode_verification_schedule as schedule
    import autocode_vitest_tests as vitest_tests
    from autocode_suite_health import suite_health
    from autocode_test_framework import Framework
    from autocode_verification_commands import _go_test, command_framework, select_commands
    from autocode_verification_commands import _mentions_tests as _mentions_tests

PASS, FAIL, UNVERIFIED = "PASS", "FAIL", "UNVERIFIED"
# Directories that hold tests wherever they appear, and ones that do only at the repository root:
# numpy/testing/ and django/test/ are shipped product code, while a top-level test/ is not.
TEST_DIRS = frozenset({"tests", "__tests__", "__snapshots__", "testdata", "test_data"})
ROOT_TEST_DIRS = frozenset({"test", "spec"})
TEST_NAME = re.compile(  # case-sensitive: Latest.java and Contest.kt are product code
    r"^(test_.*\.py|.*_tests?\.py|conftest\.py|.*\.(test|spec)\.[cm]?[jt]sx?|.*\.snap|.*_test\.go"
    r"|.*_(spec|test)\.rb|.*Tests?\.(java|kt|cs|swift|scala)|Test[A-Z_]\w*\.(java|kt|cs|swift|scala)"
    r"|test_.*\.(rb|sh))$"
)
PYTHON_TEST_MODULE = python_tests.TEST_MODULE
CODE_SUFFIXES = frozenset(
    {
        ".py",
        ".pyi",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".go",
        ".rs",
        ".rb",
        ".java",
        ".kt",
        ".kts",
        ".scala",
        ".swift",
        ".c",
        ".h",
        ".cc",
        ".cpp",
        ".hpp",
        ".cs",
        ".php",
        ".m",
        ".mm",
        ".ex",
        ".exs",
        ".erl",
        ".hs",
        ".ml",
        ".lua",
        ".pl",
        ".sh",
        ".dart",
        ".zig",
    }
)
DEPENDENCY_DIRS = ("node_modules", ".venv", "venv")
COLLECTION_ERROR = python_tests.COLLECTION_ERROR
UNITTEST_HEADER = python_tests.UNITTEST_HEADER
UNITTEST_STATUS = python_tests.UNITTEST_STATUS
UNITTEST_BARE_STATUS = python_tests.UNITTEST_BARE_STATUS
UNITTEST_FIXTURES = python_tests.UNITTEST_FIXTURES
TAIL_CHARS = 4000
DEFAULT_TIMEOUT = 900


# --- classification --------------------------------------------------------


def is_test_path(path: str) -> bool:
    """True for files that belong to the test suite rather than the product."""
    parts = PurePosixPath(path).parts
    if not parts:
        return False
    directories = parts[:-1]
    if any(part in TEST_DIRS for part in directories) or (directories and directories[0] in ROOT_TEST_DIRS):
        return True
    if path.startswith("src/test/") or "/src/test/" in path:
        return True  # Maven and Gradle layouts
    return bool(TEST_NAME.match(parts[-1]))


def is_code_path(path: str) -> bool:
    return PurePosixPath(path).suffix in CODE_SUFFIXES


def _git(cwd, *args, check=True, env=None, input=None):
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, encoding="utf-8", errors="replace", env=env, input=input
    )
    if check and result.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _document_only_base(workspace, base, *, dependencies_from=None, independent=None, ignored_inputs=None):
    """True when the base the proof runs holds no behavior: every entry of the
    pinned tree is a regular non-executable blob that is the root README.md or,
    when the candidate cannot have hidden ignored code (below), a Markdown
    document (``.md``), a .gitignore, an empty file, or, if no nonempty file in
    the base is code or a test path, a project, dependency or license manifest
    (``first_suite.manifest``: a first project, #1783) or a plain text or data
    file the deliverable reads (``first_suite.data_file``, #2036; ``plain_note``, #2259); and
    make_tree copies no git-ignored code into the proof trees (``generated_sources`` of
    ``dependencies_from`` is empty, and so is the launch inventory of an
    in-place run, ``ignored_inputs``: autocode_launch_inputs.Supply).

    Source/test filename conventions cannot establish absence of existing
    behavior. Keep this positive inventory deliberately narrow: other non-empty
    files, executable files (even empty ones), links and submodules need
    preservation. A .gitignore and an empty file cannot hold behavior to
    preserve. A manifest names tools and versions, not behavior, and only
    counts when no nonempty file is code or a test path, so a base that holds
    source still needs passing-test evidence. An ignore rule (.gitignore, .git/info/exclude) keeps code out of
    the pinned tree but not out of the proof: copy_generated_sources puts
    ignored code that sits next to tracked files into the base and candidate
    trees, so any such file counts as existing behavior, whatever made it.
    Ignored files are not versioned, so that check is only as good as the
    checkout it reads. The original checkout of a task worktree is one the
    candidate does not edit. The workspace itself (an --in-place run, compared
    with both paths resolved, so a symlink to it is the workspace too) is not:
    the candidate can delete ignored code, or stop ignoring it, before the proof
    reads it. Nor is a checkout an earlier Builder of the run worked in, which
    the caller states with ``independent=False``: a continuation restored from a
    checkpoint of an --in-place run works in a new worktree whose original
    checkout is that run's workspace (autocode_regression.proof_dependencies).
    There (or with no ``dependencies_from``) only the root README.md is
    accepted, the rule this function had before .gitignore and empty files,
    unless the in-place run's launch record binds its ignored inputs
    (``ignored_inputs.recorded``, nothing ``unverified``): it was taken before
    any provider ran, and supply refuses a recorded file that changed or went
    missing, so the candidate cannot have hidden ignored code that existed then.
    ``independent=None`` (or True) leaves the decision to the path comparison
    or that record: no caller can make the workspace independent of itself. A
    Markdown document is read, not run: it holds no more behavior than the root
    README.md, but it can sit in any directory, beside ignored code. Third-party
    dependencies make_tree links or copies separately (node_modules, venvs, an
    ignored vendor/) count only where ``generated_sources`` lists a file, which
    it never does under node_modules or a venv
    (docs/bugs/2026-10-06-regression-proof-scaffold-base.md).
    """
    # An in-place run's launch record (autocode_launch_inputs.record, taken before any provider ran)
    # lists the ignored code the checkout held at launch, and supply refuses (unverified) once any of
    # it changed or went missing: the candidate cannot hide it, as in a separate checkout.
    launch_bound = bool(
        ignored_inputs is not None and getattr(ignored_inputs, "recorded", False) and not ignored_inputs.unverified
    )
    separate = independent is not False and (
        launch_bound or (bool(dependencies_from) and Path(dependencies_from).resolve() != Path(workspace).resolve())
    )
    regular = project_inventory.regular_entries(_git(workspace, "ls-tree", "-r", "-l", "-z", base))
    if regular is None:
        return False
    launched = ignored_inputs.generated if ignored_inputs is not None else {}
    # With no code or test file in the base, the suite had nothing to run, so a
    # manifest holds no behavior to preserve (#1783).
    source = any(size and (is_code_path(path) or is_test_path(path)) for path, size in regular)
    read = first_suite.blob_reader(workspace, base)
    accepted = [
        first_suite.inert_base_file(path, size, separate=separate, source=source, read=read) for path, size in regular
    ]
    return not generated_sources(dependencies_from) and not launched and all(accepted)


def _untested_base(workspace, base) -> bool:
    paths = project_inventory.regular_paths(_git(workspace, "ls-tree", "-r", "-l", "-z", base), executable=True)
    return paths is not None and not any(is_test_path(path) for path in paths)


def _no_go_project(workspace, base, copied) -> bool:
    """A link, submodule or copied Go source keeps ordinary preservation rules."""
    paths = project_inventory.regular_paths(_git(workspace, "ls-tree", "-r", "-l", "-z", base))
    return paths is not None and not first_suite.contains_go_project([*paths, *copied])


def _package_scripts(text) -> dict:
    try:
        package = json.loads(text or "{}")
    except ValueError:
        return {}
    scripts = package.get("scripts")
    return dict(scripts) if isinstance(scripts, dict) else {}


def _suite_package_script(command):
    """The package.json script name an npm/yarn/pnpm suite command runs, if any."""
    if not isinstance(command, str) or re.search(r"[;&|<>`$\n\r]", command):
        return None
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    if not words or PurePosixPath(words[0]).name not in ("npm", "npm.cmd", "yarn", "yarn.cmd", "pnpm", "pnpm.cmd"):
        return None
    positional = [word for word in words[1:] if not word.startswith("-")]
    if not positional:
        return None
    if positional[0] in ("run", "run-script"):
        return positional[1] if len(positional) > 1 else None
    return "test" if positional[0] in ("test", "t") else None


def _ignored(path: str) -> bool:
    # Top-level dependency links are runner-made (link_dependencies), never part of a fix.
    return (
        path.startswith((".autocode/", ".autocode-ui/"))
        or "/__pycache__/" in f"/{path}"
        or path.endswith(".pyc")
        or path in DEPENDENCY_DIRS
    )


def _reachable(workspace, path) -> bool:
    """``path`` exists and is reached through real directories only, so Git can stage what is there."""
    return os.path.lexists(Path(workspace, path)) and not any(
        Path(workspace, parent).is_symlink() for parent in list(PurePosixPath(path).parents)[:-1]
    )


@contextlib.contextmanager
def _staged(workspace, base=None, *, keep=False):
    """(Git's environment with a scratch index holding the working tree as `git add -A` stages it,
    the untracked Git repositories it cannot stage, as ``dir/``).

    The index starts as a copy of the real one, so tracked files keep their stat cache; the real
    index, HEAD and the files are untouched. Untracked files count, ignored ones do not, except a
    file ``base`` holds that is still there: ignoring it changes nothing in its content. Run state
    and bytecode (_ignored) keep the real index's entries. Objects go to a scratch store that is
    dropped afterwards unless ``keep`` (a commit that must last).

    Plumbing only, never `git add`: it refuses a pathspec that names an ignored path (a gitignored
    .autocode, AutoCode's own info/exclude rule) and `add -u -- .` an index with no files."""
    with tempfile.TemporaryDirectory(prefix="autocode-index-") as scratch:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(scratch) / "index")}
        if not keep:
            objects = Path(workspace, _git(workspace, "rev-parse", "--git-path", "objects").strip()).resolve()
            alternates = filter(None, (str(objects), os.environ.get("GIT_ALTERNATE_OBJECT_DIRECTORIES")))
            env.update(
                GIT_OBJECT_DIRECTORY=str(Path(scratch, "objects")),
                GIT_ALTERNATE_OBJECT_DIRECTORIES=os.pathsep.join(alternates),
            )
            Path(env["GIT_OBJECT_DIRECTORY"]).mkdir()
        real = Path(workspace, _git(workspace, "rev-parse", "--git-path", "index").strip())
        if real.is_file():
            shutil.copy2(real, env["GIT_INDEX_FILE"])  # with its timestamp: a racily clean entry stays racy
        dirty = [
            path
            for path in _git(workspace, "-c", "core.fsmonitor=false", "diff-files", "--name-only", "-z", env=env).split(
                "\0"
            )
            if path and not _ignored(path)
        ]
        others = [
            path
            for path in _git(workspace, "ls-files", "-z", "--others", "--exclude-standard", env=env).split("\0")
            if path and not _ignored(path)
        ]
        repositories = [path for path in others if path.endswith("/")]  # Git never stages inside one
        there = {path: _reachable(workspace, path) for path in dirty}
        gone = [path for path in dirty if not there[path]]
        add = [path for path in dirty if there[path]] + [path for path in others if not path.endswith("/")]
        if base:
            ignored = set(
                _git(
                    workspace,
                    "diff-index",
                    "--cached",
                    "--diff-filter=D",
                    "--name-only",
                    "-z",
                    "--no-renames",
                    base,
                    "--",
                    env=env,
                ).split("\0")
            ).difference(add, gone, [""])
            add += [
                path
                for path in sorted(ignored)
                if not _ignored(path)
                and _reachable(workspace, path)
                and (Path(workspace, path).is_symlink() or Path(workspace, path).is_file())
                and not path.startswith(tuple(repositories))
            ]
        for options, paths in ((("--force-remove",), gone), (("--add", "--remove", "--replace"), add)):
            if paths:
                _git(workspace, "update-index", *options, "-z", "--stdin", env=env, input="\0".join(paths) + "\0")
        yield env, repositories


def changed_files(workspace, base, *, source_paths=()) -> dict[str, str]:
    """Every path whose content differs from ``base``: committed, staged, dirty or untracked.

    Compared through a staged copy of the working tree, so a base that already holds untracked
    files (an in-place run's launch state, commit_worktree) counts only what changed since. An
    untracked Git repository is listed as ``dir/``, added."""
    with _staged(workspace, base) as (env, repositories):
        tokens = _git(
            workspace, "diff-index", "--cached", "--name-status", "-z", "--no-renames", base, "--", env=env
        ).split("\0")
        changes = {
            path: {"A": "added", "D": "deleted"}.get(status[:1], "modified")
            for status, path in zip(tokens[0::2], tokens[1::2], strict=False)
            if path
        }
        changes.update(dict.fromkeys(repositories, "added"))
        for path in _git(workspace, "diff-files", "--name-only", "-z", "--diff-filter=M", env=env).split("\0"):
            if path and Path(workspace, path).is_dir():  # a submodule with uncommitted work, as `git diff` shows it
                changes.setdefault(path, "modified")
        if source_paths:
            tracked = set(filter(None, _git(workspace, "ls-files", "-z", "--cached", env=env).split("\0")))
            selected = source_snapshot.snapshot(workspace, paths=source_paths)
            for path, identity in selected["files"].items():
                if identity.startswith(("submodule:", "uninitialized-submodule")):
                    changes.pop(path, None)
            for path, identity in source_snapshot.inventory(workspace, paths=source_paths).items():
                if path not in tracked and identity != "deleted":
                    changes[path] = "added"
    return {path: status for path, status in sorted(changes.items()) if not _ignored(path)}


def commit_worktree(workspace):
    """HEAD when the checkout matches it, else a new child commit of HEAD holding the working tree as
    changed_files sees it, uncommitted and untracked files included (an untracked Git repository
    cannot be). No ref, index or file changes; None without a HEAD."""
    head = _git(workspace, "rev-parse", "--verify", "-q", "HEAD", check=False).strip()
    if not head:
        return None
    with _staged(workspace, keep=True) as (env, _):
        tree = _git(workspace, "write-tree", env=env).strip()
    if tree == _git(workspace, "rev-parse", "HEAD^{tree}").strip():
        return head
    return _git(
        workspace,
        "-c",
        "user.name=AutoCode",
        "-c",
        "user.email=autocode@localhost",
        "commit-tree",
        "--no-gpg-sign",
        tree,
        "-p",
        head,
        "-m",
        "AutoCode: the checkout as a run started",
    ).strip()


BINARY_LINES = 1000  # a binary change is never "tiny"


def diff_stats(workspace, base, changes) -> dict:
    lines, binary = {}, []
    with _staged(workspace, base) as (env, _):
        numstat = _git(workspace, "diff-index", "--cached", "--numstat", "-z", "--no-renames", base, "--", env=env)
    for record in numstat.split("\0"):
        parts = record.split("\t", 2)
        if len(parts) != 3 or parts[2] not in changes:
            continue
        if parts[0] == "-":
            binary.append(parts[2])
            lines[parts[2]] = (BINARY_LINES, 0)
        else:
            lines[parts[2]] = (int(parts[0]), int(parts[1]))
    for path, status in changes.items():
        file = Path(workspace) / path
        if path not in lines and status == "added" and file.is_file():
            data = file.read_bytes()
            if b"\0" in data[:8000]:
                binary.append(path)
                lines[path] = (BINARY_LINES, 0)
            else:
                lines[path] = (len(data.splitlines()), 0)
    source = [p for p in changes if not is_test_path(p)]
    return {
        "files": len(changes),
        "source_files": len(source),
        "test_files": len(changes) - len(source),
        "lines_added": sum(a for a, _ in lines.values()),
        "lines_removed": sum(r for _, r in lines.values()),
        "source_lines_changed": sum(sum(lines.get(p, (0, 0))) for p in source),
        "binary_files": sorted(binary),
        "non_code_files": sorted(p for p in changes if not is_code_path(p)),
    }


def removed_python_tests(workspace, base, changes) -> list[str]:
    """Names of ``def test*`` functions deleted from modified Python test files."""
    removed = []
    pattern = re.compile(r"^\s*(?:async\s+)?def\s+(test\w*)\s*\(", re.M)
    for path, status in changes.items():
        if not (path.endswith(".py") and is_test_path(path)) or status == "added":
            continue
        before = set(pattern.findall(_git(workspace, "show", f"{base}:{path}", check=False)))
        after_file = Path(workspace) / path
        after = set(pattern.findall(after_file.read_text(errors="replace"))) if after_file.is_file() else set()
        removed += [f"{path}::{name}" for name in sorted(before - after)]
    return removed


# --- project test frameworks -----------------------------------------------


def python_for(project) -> str:
    return test_env.python_for(project)


def _read(path):
    try:
        return Path(path).read_text(errors="replace")
    except OSError:
        return ""


def _python_can_import(python, module):
    try:
        return subprocess.run([python, "-c", f"import {module}"], capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def detect_framework(root, *, python=None, test_root=None, base=None, package=None) -> Framework | None:
    """Detect the project suite, a caller-scoped Python suite with pinned base policy, or a package's suite."""
    if package is not None:  # its directory as the project root; its commands enter that directory
        return package_scope.detect(root, package, lambda directory: detect_framework(directory, python=python))
    root = Path(root)
    # Untracked files count: AutoCode never commits, so in a new project every file the Builder wrote,
    # tests included, is untracked (a live greenfield run found no test command and could not prove its
    # tests, 2026-09-29). Ignored files, such as .autocode/, do not.
    files = [
        p
        for p in _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard", check=False).split("\0")
        if p
    ]
    if test_root is not None:
        return _rooted_python(root, test_roots.normalize(test_root), files, python, base=base)
    has_python = any(p.endswith(".py") for p in files)
    if has_python:
        python = python or python_for(root)
        configured = _pytest_configured(root, files)
        style = python_tests.prefers_pytest_in(root, files, _read)
        if style or (configured and _python_can_import(python, "pytest")):
            return Framework(
                "pytest",
                f"{shlex.quote(python)} -m pytest -q -p no:cacheprovider --continue-on-collection-errors",
                python=python,
            )
        # One family inventory (autocode_python_tests.family_inventory) drives both nested
        # layouts: components/ and the joint repos/<member>/ workspace (#2414), where no
        # root discovery shape collects the members and a later markerless root tests/
        # directory must not drop them either.
        families = python_tests.family_inventory(files, _read, root)
        tests = families.tests
        if families.any_family():
            note = f"pytest is configured but {python} cannot import it; using unittest" if configured else ""
            plain_start = None
            if base:
                pinned_files = _git(root, "ls-tree", "-rz", "--name-only", base).split("\0")
                pinned_tests = [p for p in pinned_files if PYTHON_TEST_MODULE.match(PurePosixPath(p).name)]
                plain_start = test_roots.added_empty_marker_start(root, tests, pinned_tests, pinned_files)
            if plain_start:
                start = " -s " + plain_start
            elif (
                any("/" not in p for p in tests)
                or (root / "tests" / "__init__.py").is_file()
                or (root / "test" / "__init__.py").is_file()
            ):
                start = ""
            elif (root / "tests").is_dir():
                start = " -s tests"
            elif (root / "test").is_dir():
                start = " -s test"
            else:
                start = ""
            runner = str(Path(__file__).with_name("autocode_component_tests.py"))
            if families.collected_by_runner():
                # One command for the whole suite, with bounded derived relative member
                # paths per family: the collector runs the selected root suite once plus
                # each family through the one shared family algorithm (parent hooks own
                # collection; otherwise parent TestCases once and every member isolated),
                # so baseline originals are never hidden by later root joint tests (#2414).
                command = shlex.join(
                    [
                        python,
                        runner,
                        "-v",
                        "--root",
                        start.removeprefix(" -s ") or ".",
                        *(["--components", *families.components] if families.has_components() else []),
                        *(["--repos", *families.repo_members] if families.has_repos() else []),
                    ]
                )
                return Framework("unittest", command, python=python, note=note)
            return Framework(
                "unittest", f"{shlex.quote(python)} -m unittest discover -v{start}", python=python, note=note
            )
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


# Native pytest policy names are configurations even when empty. Other project
# configuration files require pytest sections or dependency declarations.
_PYTEST_NATIVE_CONFIGS = frozenset(("pytest.ini", ".pytest.ini", "pytest.toml", ".pytest.toml"))


def _pytest_configured(directory, files, *, read=None) -> bool:
    read = read or _read
    names = {PurePosixPath(p).name for p in files}
    pyproject, setup_cfg, tox = (
        read(directory / "pyproject.toml"),
        read(directory / "setup.cfg"),
        read(directory / "tox.ini"),
    )
    requirements = " ".join(read(directory / p) for p in files if re.match(r"(.*/)?requirements.*\.(txt|in)$", p))
    return bool(
        names.intersection(_PYTEST_NATIVE_CONFIGS)
        or "conftest.py" in names
        or "[tool.pytest" in pyproject
        or "[tool:pytest]" in setup_cfg
        or "[pytest]" in tox
        or re.search(r"\bpytest\b", requirements + pyproject)
    )


def _rooted_python(workspace, test_root, files, python, *, base=None) -> Framework | None:
    """Honor current/ancestor and pinned pytest policy without narrowing collection.

    Removing a policy or its dependency must never replace pytest with unittest.
    """
    directory = workspace / test_root
    entries = {}
    if base:
        for record in _git(workspace, "ls-tree", "-r", "-t", "-z", base).split("\0"):
            if record:
                metadata, path = record.split("\t", 1)
                entries[path] = metadata.split()
    base_files = [path for path, entry in entries.items() if entry[1] != "tree"]
    configured = False
    policies = [(files, lambda path: test_roots.read_policy(workspace, path))]
    if base:
        policies.append(
            (base_files, lambda path: test_roots.pinned_policy(workspace, path.relative_to(workspace), entries))
        )
    relative_root = PurePosixPath(test_root)
    for relative_dir in (relative_root, *relative_root.parents):
        policy_dir, prefix = workspace / relative_dir, relative_dir.as_posix()
        for inventory, read in policies:
            relative = inventory if prefix == "." else test_roots.inside(prefix, inventory)
            if policy_dir != directory:
                relative = [path for path in relative if "/" not in path]
            try:
                for path in relative:
                    if PurePosixPath(path).name in _PYTEST_NATIVE_CONFIGS or PurePosixPath(path).name == "conftest.py":
                        read(policy_dir / path)
                configured |= _pytest_configured(policy_dir, relative, read=read)
                if policy_dir == directory:
                    configured |= python_tests.prefers_pytest_in(policy_dir, relative, read)
            except (OSError, ValueError, RuntimeError):
                return None  # Unknown policy cannot authorize a smaller or first suite.
    files = test_roots.inside(test_root, files)
    if not any(p.endswith(".py") for p in files):
        return None
    python = python or python_for(workspace)
    if configured:
        return Framework(
            "pytest",
            f"{shlex.quote(python)} -m pytest -q -p no:cacheprovider "
            f"--continue-on-collection-errors {shlex.quote(test_root)}",
            python=python,
            test_root=test_root,
        )
    tests = [p for p in files if PYTHON_TEST_MODULE.match(PurePosixPath(p).name)]
    if not tests:
        return None
    package = (directory / "__init__.py").is_file() or f"{test_root}/__init__.py" in base_files
    # Explicit top level visits the start package's load_tests and preserves relative
    # imports even when components/ is a namespace rather than a regular package.
    start = test_root if package else test_roots.unittest_start(test_root, directory, tests)
    top = " -t ." if package else ""
    return Framework(
        "unittest",
        f"{shlex.quote(python)} -m unittest discover -v -s {shlex.quote(start)}{top}",
        python=python,
        test_root=test_root,
    )


# --- execution --------------------------------------------------------------


def _proof_pythonpath(tree, inherited):
    """Path entries inherited from the runner, minus a parent ``tests`` package.

    The tree is already first. A later regular ``tests`` package still captures
    a fixture ``tests/`` directory that has no ``__init__.py``, so the checkout
    that launched the proof must not stay on the path.
    """
    tree_root = Path(tree).resolve()
    tree_has_tests = (tree_root / "tests").is_dir()
    kept = []
    for entry in inherited.split(os.pathsep):
        if not entry:
            continue
        root = Path(entry)
        try:
            same_tree = root.resolve() == tree_root
        except OSError:
            same_tree = False
        if tree_has_tests and not same_tree and (root / "tests" / "__init__.py").is_file():
            continue
        kept.append(entry)
    return kept


def test_environment(tree, env=None):
    """Environment for a command run in ``tree``.

    The tree (and its ``src/``) goes first on PYTHONPATH so an editable install
    of the user's checkout (a ``.pth`` file in a linked venv) cannot shadow the
    code being tested. A parent checkout whose regular ``tests`` package would
    capture this tree's ``tests/`` directory is left off the path. Credential-like
    variables are withheld: the tests are model-written code (autocode_agent_env).
    """
    environment = dict(agent_env.scrubbed(os.environ if env is None else env), PYTHONDONTWRITEBYTECODE="1", CI="1")
    roots = [str(Path(tree) / "src")] if (Path(tree) / "src").is_dir() else []
    roots.append(str(tree))
    roots.extend(_proof_pythonpath(tree, environment.get("PYTHONPATH", "")))
    environment["PYTHONPATH"] = os.pathsep.join(roots)
    python = test_env.virtualenv_python(tree)
    if python:
        # Test fixtures often launch `python3` rather than sys.executable.
        # They must inherit the same dependencies as the parent test process.
        environment["PATH"] = str(Path(python).parent) + os.pathsep + environment.get("PATH", "")
        environment["VIRTUAL_ENV"] = str(Path(python).parent.parent)
    else:
        path_value = environment.get("PATH", "")
        if not shutil.which("python", path=path_value):
            resolved_python = shutil.which("python3", path=path_value) or test_env.python_for(tree)
            if resolved_python:
                shim_dir = test_env.ensure_python_shim(resolved_python)
                if shim_dir:
                    environment["PATH"] = f"{shim_dir}{os.pathsep}{path_value}" if path_value else shim_dir
    return environment


def run_command(
    command, cwd, log_path, *, timeout=DEFAULT_TIMEOUT, env=None, checkpoint=None, native_execution=None
) -> dict:
    """Run one owned shell command and return its collected execution receipt."""
    if native_execution is not None:
        if type(native_execution) is not native_checks.NativeCheckExecution:
            raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Original concrete native check owner required")
        return native_execution.run_command(command, cwd, log_path, timeout=timeout, env=env, checkpoint=checkpoint)
    return command_supervision.run(
        command, cwd, log_path, timeout=timeout, env=test_environment(cwd, env), checkpoint=checkpoint
    )


def _with_results(framework, command, xml_path, tree=None):
    if node_tests.command_words(command):
        return node_tests.instrument(command, xml_path)
    if vitest_tests.command_words(command, tree):
        return vitest_tests.instrument(command, xml_path, tree)
    if framework and framework.name == "pytest" and " -m pytest" in command:
        return f"{command} --junitxml={shlex.quote(str(xml_path))}"
    if framework and framework.name == "go" and (invocation := go_tests.parse(command)):
        # Go reports per-test results only as `go test -json` events (a live Go port could not be
        # proven without them, 2026-09-29). The flag goes before the packages, where go test reads it.
        return invocation.instrument()
    # Unittest names each test only at -v. A quiet or default command still runs
    # the same tests; the proof needs those names to tell a passing baseline
    # from a count with no identities.
    return python_tests.verbose_unittest(command)


def expects_results(framework, command, tree=None):
    """True when this command, run by the runner, must yield per-test results."""
    if framework is not None and framework.name == "junit":
        return bool(command)
    if node_tests.command_words(command):
        return True
    if vitest_tests.command_words(command, tree):
        return True
    if not framework or not framework.per_test or not command:
        return False
    if framework.name == "go":
        return _go_test(command)
    return (
        (" -m pytest" in command)
        if framework.name == "pytest"
        else ((" -m unittest" in command or "autocode_component_tests.py" in command) and " -v" in command)
    )


_go_results = go_tests.results


_unittest_id = python_tests._unittest_id
_unittest_results = python_tests.unittest_results


def per_test_results(framework, receipt, xml_path, *, tree=None) -> dict | None:
    """Passed, failed and skipped test ids, or None when the run produced no parseable results.

    ``collection_errors`` are failures of a module to import or collect; they are
    failures, but they never name a test that ran. ``uncollected`` is the subset
    that never imported, collected or built at all: a failed hook or fixture that
    Node and Vitest report as a collection error executed and is judged like any
    other failing test, so it is not in ``uncollected``.
    """
    if str(xml_path).endswith(".node.jsonl"):
        return node_tests.results(xml_path)
    if str(xml_path).endswith(".vitest.json"):
        return vitest_tests.results(xml_path, tree)
    if not framework or not framework.per_test or framework.name in ("node", "vitest"):
        return None
    if framework.name == "go":
        invocation = go_tests.parse(package_scope.split(framework, receipt.get("command") or framework.suite)[1])
        if invocation is None or invocation.json_disabled:
            return None
        output = receipt.get("output")
        return _go_results(Path(output).read_text(errors="replace")) if output and Path(output).is_file() else None
    passed, failed, skipped, collection = set(), set(), set(), set()
    setup_errors = {}
    if framework.name == "pytest":
        if not Path(xml_path).is_file():
            return None
        try:
            result_tree = ET.parse(xml_path)
        except ET.ParseError:
            return None
        for test, case in python_tests.pytest_case_names(result_tree.iter("testcase"), tree):
            problem = case.find("failure") if case.find("failure") is not None else case.find("error")
            if problem is not None:
                failed.add(test)
                reason = test_setup.setup_error(problem.text or "", tree, is_test_path)
                if reason:
                    setup_errors[test] = reason
                if not case.get("classname") or "collection failure" in (problem.get("message") or ""):
                    collection.add(test)
            elif case.find("skipped") is not None:
                skipped.add(test)
            else:
                passed.add(test)
        # A test that fails and then errors in teardown is two testcases of one id; a test reported
        # with two different outcomes stays a duplicate id, never a complete result.
        total = len(passed | failed | skipped)
        complete = True
    else:
        text = Path(receipt["output"]).read_text(errors="replace")
        return _unittest_results(
            text,
            component="autocode_component_tests.py" in framework.suite,
            classify_setup=lambda detail: test_setup.setup_error(detail, tree, is_test_path),
        )
    results = {
        "passed": sorted(passed),
        "failed": sorted(failed),
        "skipped": sorted(skipped),
        "collection_errors": sorted(collection),
        "uncollected": sorted(collection),
        "total": total,
        "complete": complete,
    }
    if setup_errors:
        results["setup_errors"] = setup_errors
    return results


# --- scratch trees ----------------------------------------------------------


def vendored_files(source_root):
    """The ignored, untracked files under ``vendor/`` a scratch tree receives, relative to ``source_root``.

    Only those files: a new tracked vendor tree must not bring candidate code into the base.
    A symlink is followed to the file it names inside ``source_root``; a symlinked root or
    directory, or one that leaves the checkout, raises ValueError."""
    source_root = Path(source_root).resolve()
    source = source_root / "vendor"
    if source.is_symlink():
        raise ValueError("Vendored dependencies must not use a symlinked root")
    if not source.is_dir():
        return []
    included = set()
    for name in filter(
        None,
        _git(source_root, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--", "vendor").split("\0"),
    ):
        path = Path(name)
        included.update((path, *path.parents))
    files = []
    for directory, dirs, names in os.walk(source):
        relative = Path(directory).relative_to(source_root)
        omitted = {name for name in dirs + names if relative / name not in included}
        omitted |= investigation_workspace.ignored_entries(source_root, directory, set(dirs + names) - omitted)
        dirs[:] = sorted(name for name in dirs if name not in omitted)
        files += [str(relative / name) for name in sorted(names) if name not in omitted]
    return files


def copy_vendored_dependencies(source_root, tree):
    """Make ignored vendored dependencies available without sharing writable source files."""
    if not source_root:
        return
    source_root = Path(source_root).resolve()
    target = Path(tree) / "vendor"
    if (source_root / "vendor").is_symlink() or target.is_symlink():
        raise ValueError("Vendored dependencies must not use a symlinked root")
    if target.exists():
        return  # Tracked dependencies already come from the selected Git base and overlay.
    for relative in vendored_files(source_root):
        destination = Path(tree) / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_root / relative, destination)


def link_dependencies(source_root, tree):
    """Expose ignored dependency directories (node_modules, venvs) to a scratch tree."""
    if not source_root:
        return
    roots = test_env.dependency_roots(source_root)
    for name in DEPENDENCY_DIRS:
        target = Path(tree) / name
        # Sharing a virtualenv is supported; other dependencies keep their
        # existing task-local lookup to avoid changing package-manager behavior.
        sources = roots if name in (".venv", "venv") else roots[:1]
        for root in sources:
            source = root / name
            if source.is_dir():
                if not target.exists() and not target.is_symlink():
                    target.symlink_to(source.resolve(), target_is_directory=True)
                break


GENERATED_SOURCE_LIMIT = 1_000_000


def generated_sources(source_root):
    """Eligible ignored build inputs, shared by scratch copies, receipt identity and
    autocode_launch_inputs."""
    if not source_root:
        return []
    source_root = Path(source_root)
    ignored = _git(
        source_root, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory", check=False
    ).split("\0")
    tracked_dirs = {
        str(PurePosixPath(p).parent) for p in _git(source_root, "ls-files", "-z", check=False).split("\0") if p
    }
    selected = []
    for relative in ignored:
        path = PurePosixPath(relative)
        if (
            not relative
            or relative.endswith("/")
            or path.suffix not in CODE_SUFFIXES
            or str(path.parent) not in tracked_dirs
            or any(part in DEPENDENCY_DIRS for part in path.parts)
        ):
            continue
        source = source_root / relative
        if source.is_file() and not source.is_symlink() and source.stat().st_size <= GENERATED_SOURCE_LIMIT:
            selected.append(relative)
    return selected


def generated_source_record(source_root):
    """Byte identity of the ignored generated sources ``source_root`` holds right now.

    The optional record-based copy policy admits a file only while its bytes
    match. In-place launch-bound proofs use ``autocode_launch_inputs`` instead.
    """
    if not source_root:
        return {}
    root = Path(source_root)
    return {path: util.file_hash(root / path) for path in generated_sources(root)}


def classify_generated_sources(source_root, record=None, *, unrecorded=False):
    """Trusted copy paths, proof notes, and paths left out.

    ``record is None`` without ``unrecorded`` trusts every eligible file: a
    separate project checkout, or a direct call. A dict trusts only paths whose
    bytes still match. ``unrecorded`` trusts nothing, because a saved run has no
    start record (#529). The omitted list is None when the filter is off, so
    receipt identity stays as it was.
    """
    current = generated_source_record(source_root)
    if record is None and not unrecorded:
        return list(current), [], None
    saved = record if isinstance(record, dict) else {}
    trusted, added, changed = [], [], []
    for path, digest in current.items():
        if unrecorded or path not in saved:
            added.append(path)
        elif saved[path] == digest:
            trusted.append(path)
        else:
            changed.append(path)
    notes = []
    if added and unrecorded:
        notes.append(
            "Ignored generated sources were left out of the proof because this run has no record "
            "of them from when it started: " + ", ".join(added)
        )
    elif added:
        notes.append("Ignored generated sources added during the run were left out of the proof: " + ", ".join(added))
    if changed:
        notes.append(
            "Ignored generated sources changed during the run were left out of the proof: " + ", ".join(changed)
        )
    return trusted, notes, sorted(set(current) - set(trusted))


def _copied_generated(source_root, record, unrecorded, ignored_inputs):
    """The ignored generated sources make_tree copies into every proof tree."""
    if ignored_inputs is not None:
        return sorted(ignored_inputs.generated)
    return classify_generated_sources(source_root, record, unrecorded=unrecorded)[0]


def copy_generated_sources(source_root, tree, *, record=None, unrecorded=False):
    """Copy build-generated source files (git-ignored code next to tracked code,
    such as a setuptools-scm or hatch-vcs ``_version.py``) into a scratch tree.
    A fresh worktree lacks them, so the package would not import there. Base and
    candidate trees receive the same files, so the comparison stays fair.

    ``record`` and ``unrecorded`` limit that copy to sources the run already
    held; see ``classify_generated_sources``.
    """
    copied = []
    for relative in classify_generated_sources(source_root, record, unrecorded=unrecorded)[0]:
        source, target = Path(source_root) / relative, Path(tree) / relative
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied.append(relative)
    return copied


def _clear(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def make_tree(
    repo,
    base,
    destination,
    overlay_root,
    changes,
    *,
    dependencies_from=None,
    patch=None,
    generated_record=None,
    generated_unrecorded=False,
    ignored_inputs=None,
):
    """A detached worktree of ``base`` with ``changes`` copied from ``overlay_root``.

    ``patch`` (a patch file) is applied to ``base`` before the changes are copied in: the
    base a review follow-up is proven against is the change the review judged.
    ``ignored_inputs`` (autocode_launch_inputs.Supply), when given, supplies the ignored
    vendored and generated files instead of copies from ``dependencies_from``.
    Otherwise ``generated_record`` and ``generated_unrecorded`` retain the optional
    generated-file filter used by direct callers."""
    destination = Path(destination)
    if destination.exists():
        remove_tree(repo, destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "--detach", str(destination), base)
    try:
        if patch:
            applied = subprocess.run(
                ["git", "-C", str(destination), "apply", str(patch)], capture_output=True, text=True
            )
            if applied.returncode:
                raise ValueError(f"git apply {patch} failed: {(applied.stderr or applied.stdout).strip()[-300:]}")
        ordered = sorted(changes.items(), key=lambda item: item[1] != "deleted")  # deletions first
        for path, status in ordered:
            target = destination / path
            if status == "deleted":
                if target.is_symlink() or target.is_file():
                    target.unlink()
                continue
            source = Path(overlay_root) / path
            for parent in reversed(target.relative_to(destination).parents[:-1]):
                if (destination / parent).is_symlink() or (destination / parent).is_file():
                    (destination / parent).unlink()  # a file that became a directory
            target.parent.mkdir(parents=True, exist_ok=True)
            _clear(target)  # includes a directory that became a file
            if source.is_symlink():
                target.symlink_to(os.readlink(source))
            elif source.is_file():
                shutil.copy2(source, target)
        link_dependencies(dependencies_from, destination)
        if ignored_inputs is None:
            copy_vendored_dependencies(dependencies_from, destination)
            copy_generated_sources(
                dependencies_from, destination, record=generated_record, unrecorded=generated_unrecorded
            )
        else:
            ignored_inputs.copy_into(destination)
    except BaseException:
        remove_tree(repo, destination)
        raise
    return destination


def patch_applies(repo, base, patch) -> str:
    """ "" when ``patch`` applies cleanly to ``base``, else why not. Uses a scratch index, no worktree."""
    with tempfile.TemporaryDirectory() as scratch:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(scratch) / "index")}
        read = subprocess.run(["git", "-C", str(repo), "read-tree", base], capture_output=True, text=True, env=env)
        check = (
            read
            if read.returncode
            else subprocess.run(
                ["git", "-C", str(repo), "apply", "--check", "--cached", str(patch)],
                capture_output=True,
                text=True,
                env=env,
            )
        )
    return "" if check.returncode == 0 else (check.stderr or check.stdout).strip()[-300:] or "git apply --check failed"


def remove_tree(repo, destination):
    """Remove a scratch worktree, including read-only caches created by probes."""
    destination = Path(destination)
    if destination.is_symlink():
        destination.unlink()
    else:
        _git(repo, "worktree", "remove", "--force", str(destination), check=False)
        if destination.exists():
            # Go's module cache makes directories read-only. Restore owner
            # access only inside this scratch tree; linked dependencies stay intact.
            destination.chmod(destination.stat().st_mode | 0o700)
            for directory, children, _ in os.walk(destination, followlinks=False):
                for name in children:
                    child = Path(directory) / name
                    if not child.is_symlink():
                        child.chmod(child.stat().st_mode | 0o700)
            shutil.rmtree(destination)
    _git(repo, "worktree", "prune", check=False)


# --- the base suite definition (#587) ----------------------------------------

# Compatibility names and the controller's patchable lookups stay here.
# Helpers' own globals belong to definition_scan.
_MANIFEST_BASENAMES = definition_scan._MANIFEST_BASENAMES
_RUNNER_CONFIG_BASENAMES = definition_scan._RUNNER_CONFIG_BASENAMES
DEFINITION_FILE_BASENAMES = definition_scan.DEFINITION_FILE_BASENAMES
_JS_MODULE_CALL = definition_scan._JS_MODULE_CALL
_JS_EXEC_CALL = definition_scan._JS_EXEC_CALL
_JS_READ_CALL = definition_scan._JS_READ_CALL
_SHELL_INTERPRETERS = definition_scan._SHELL_INTERPRETERS
_definition_file = definition_scan._definition_file
_CODE_TOKEN = definition_scan._CODE_TOKEN
_QUOTED = definition_scan._QUOTED
_TEMPLATE_CHUNK = definition_scan._TEMPLATE_CHUNK
_ESCAPE = definition_scan._ESCAPE
_js_scan = definition_scan._js_scan
_argument_span = definition_scan._argument_span
_import_sites = definition_scan._import_sites
_shell_script = definition_scan._shell_script
_first_argument_end = definition_scan._first_argument_end
_static_path = definition_scan._static_path
_specifier_between = definition_scan._specifier_between
_read_paths = definition_scan._read_paths
_exec_command_literals = definition_scan._exec_command_literals


@contextlib.contextmanager
def _effective_base(workspace, base, patch=None):
    """(entries, read, patched paths) for ``base`` with ``patch`` applied.

    This is the tree the baseline executed — the base revision with the review's
    base_patch, never the raw base alone — so a runner or selector the patch
    introduces is part of the definition closure. The index and object store live
    in a scratch directory that vanishes afterwards; ``read`` is valid only inside
    the context. Tracked files only, never node_modules or other ignored trees.
    """
    workspace = Path(workspace)
    with tempfile.TemporaryDirectory(prefix="autocode-base-definition-") as scratch:
        objects = Path(workspace, _git(workspace, "rev-parse", "--git-path", "objects").strip()).resolve()
        env = {
            **os.environ,
            "GIT_INDEX_FILE": str(Path(scratch, "index")),
            "GIT_OBJECT_DIRECTORY": str(Path(scratch, "objects")),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": os.pathsep.join(
                filter(None, (str(objects), os.environ.get("GIT_ALTERNATE_OBJECT_DIRECTORIES")))
            ),
        }
        Path(scratch, "objects").mkdir()
        if subprocess.run(
            ["git", "-C", str(workspace), "read-tree", str(base)], capture_output=True, env=env
        ).returncode:
            raise RuntimeError(f"git read-tree {base} failed")
        if (
            patch
            and subprocess.run(
                ["git", "-C", str(workspace), "apply", "--cached", str(patch)], capture_output=True, text=True, env=env
            ).returncode
        ):
            raise ValueError(f"git apply --cached {patch} failed")
        patched = set()
        if patch:
            patched = set(
                filter(
                    None,
                    _git(workspace, "diff-index", "--cached", "--name-only", "-z", "--no-renames", base, env=env).split(
                        "\0"
                    ),
                )
            )
        entries = {}
        listing = subprocess.run(
            ["git", "-C", str(workspace), "ls-files", "-s", "-z"], capture_output=True, env=env
        ).stdout.decode("utf-8", "replace")
        for entry in listing.split("\0"):
            fields = entry.split("\t", 1)
            metadata = fields[0].split()  # <mode> <object> <stage>
            if len(fields) == 2 and len(metadata) == 3:
                entries[fields[1]] = (metadata[0], metadata[1])

        with definition_inputs.blob_reader(workspace, entries, env) as read:
            yield entries, read, patched


def _base_suite_definition(workspace, base, suite_command, *, base_patch=None, changes=None, junit=False):
    """Retained inputs, boundary status and candidate manifest overlays for the suite.

    Pinned by rule: manifests, lockfiles, pnpm-workspace.yaml, .npmrc and runner-config
    basenames (mocha, jest, vitest) at any depth; every path is_test_path classifies as a
    test; and the transitive closure of path literals reachable from the manifests' script
    tokens selected by the suite (including hooks and nested script calls), from suite-command tokens, from shell scripts those commands run, from literal
    commands passed to exec/spawn/fork, and from literal fs reads (a quoted path or
    ``path.join(__dirname, ...)``). Files base tests import through literals are product
    code and never pinned, so the candidate's version enters the definition tree; a
    non-test reach is pinned only when its literals flow into the executed test command;
    any other import, or a computed import path, leaves the boundary unestablished.
    """
    with _effective_base(workspace, base, base_patch) as (entries, read, patched):
        if junit:
            return junit_proof.definition(
                workspace, suite_command, entries, read, patched, changes, is_test=is_test_path
            )
        tracked = set(entries)
        found = definition_policy.Definition(set(), "", patched)
        manifests = {}
        for path in sorted(tracked):
            if PurePosixPath(path).name != "package.json":
                continue
            target, seen = path, set()
            while entries.get(target, (None,))[0] == "120000":
                if target in seen:
                    raise ValueError(f"cyclic package manifest at {path}")
                seen.add(target)
                target = posixpath.normpath(posixpath.join(posixpath.dirname(target), read(target)))
            if target in entries:
                manifests[path] = read(target)
        tests = {path for path in tracked if is_test_path(path)}
        definition = {path for path in tracked if _definition_file(path)} | tests
        definition = definition_inputs.linked_inputs(entries, read, definition)

        def resolve(literal, directory):
            for root in (directory, ".") if directory != "." else (".",):
                candidate = posixpath.normpath(posixpath.join(root, literal))
                if not candidate.startswith("../") and candidate in tracked:
                    return candidate
            return None

        # Product code: what base tests import transitively through relative literals.
        # The candidate's version must enter the definition tree, or a runner importing
        # the module under test would pin it (#587's product carve-out).
        product, queue, seen = set(), sorted(tests), set(tests)
        while queue:
            path = queue.pop()
            for specifier, _ in _import_sites(read(path)):
                if not specifier or not specifier.startswith(("./", "../")):
                    continue
                target = resolve(specifier, posixpath.dirname(path) or ".")
                if target and target not in seen:
                    seen.add(target)
                    product.add(target)
                    queue.append(target)

        def shell_bindings(command, directory):
            # shlex keeps an ordinary quoted filename as one operand. A shell's
            # -c operand is itself a command line, expanded in the directory
            # active where that shell appears, so `sh -c 'node run-tests.js'`
            # pins the base runner. A literal `cd <dir> &&` or `cd <dir>;`
            # moves that directory for what follows: `sh -c 'cd lib && node
            # run-tests.js'` must pin lib/run-tests.js, not a root file of the
            # same name. A computed cd target cannot establish the boundary.
            try:
                parsed = shlex.split(command)
            except ValueError:
                return [], "a shell command could not be parsed, so the suite definition boundary is unestablished"
            bindings, segment, current = [], [], directory

            def finish(keep_directory):
                nonlocal current
                if segment[:1] == ["cd"] and keep_directory:
                    target = segment[1] if len(segment) == 2 else ""
                    if (
                        len(segment) != 2
                        or not target
                        or target.startswith(("-", "/", "~"))
                        or any(mark in target for mark in ("$", "`", "*", "?"))
                    ):
                        return "a cd target is computed, so the suite definition boundary is unestablished"
                    landed = posixpath.normpath(posixpath.join(current, target))
                    inside = landed in ("", ".") or (
                        not landed.startswith("../")
                        and any(path == landed or path.startswith(landed + "/") for path in tracked)
                    )
                    if not inside:
                        return f"cd {target} does not name a directory in the base tree"
                    current = "." if landed in ("", ".") else landed
                else:
                    bindings.extend((word, current) for word in segment)
                segment.clear()
                return ""

            index = 0
            while index < len(parsed):
                word = parsed[index]
                if (
                    posixpath.basename(word) in {"sh", "bash", "dash", "zsh"}
                    and index + 2 < len(parsed)
                    and parsed[index + 1] == "-c"
                ):
                    if segment:
                        reason = finish(False)
                        if reason:
                            return [], reason
                    nested, reason = shell_bindings(parsed[index + 2], current)
                    if reason:
                        return [], reason
                    bindings.extend(nested)
                    index += 3
                    continue
                if word in {"&&", ";"}:
                    reason = finish(True)
                    if reason:
                        return [], reason
                    bindings.append((word, current))
                    index += 1
                    continue
                if word in {"||", "|", "&"}:
                    reason = finish(False)
                    if reason:
                        return [], reason
                    bindings.append((word, current))
                    index += 1
                    continue
                segment.append(word)
                index += 1
            reason = finish(False)
            if reason:
                return [], reason
            return bindings, ""

        cwd_of = {}
        pinned, queue, queued, selected_scripts = set(), [], set(), set()
        loading_readers = set()
        package_names = set()
        for text in manifests.values():
            try:
                value = json.loads(text)
            except ValueError:
                continue
            if isinstance(value, dict) and isinstance(value.get("name"), str):
                package_names.add(value["name"])

        def loading_reference(text):
            # The same fields may route a runner's selector as well as product
            # code. Such a dual use cannot safely take the candidate's mapping.
            for specifier, _ in _import_sites(text):
                if (
                    specifier is None
                    or specifier in {"module", "node:module"}
                    or (
                        specifier.startswith("#")
                        or any(specifier == name or specifier.startswith(name + "/") for name in package_names)
                    )
                ):
                    return True
            # Aliased loaders and resolution-only calls are not ordinary import
            # sites. Their target can still depend on a candidate loading map.
            mask, _ = _js_scan(text)
            return bool(re.search(r"\brequire\b(?!\s*\()|\bimport\s*\.\s*meta\s*\.\s*resolve\s*\(", mask))

        for path in definition | product:
            if path in entries and (
                path in tests or path in product or PurePosixPath(path).name in _RUNNER_CONFIG_BASENAMES
            ):
                text = read(path)
                if loading_reference(text) or "package.json" in text:
                    loading_readers.add(path)

        def consider(candidate, directory):
            """Queue a tracked file the suite command reaches. Tests are scanned only when they
            are shell scripts; product code and manifests are never queued from here."""
            if (
                not candidate
                or candidate.startswith("../")
                or candidate not in tracked
                or candidate in product
                or _definition_file(candidate)
                or candidate in queued
            ):
                return
            cwd_of.setdefault(candidate, directory)
            queued.add(candidate)
            if not is_test_path(candidate):
                pinned.add(candidate)
            queue.append(candidate)

        def follow_shell(command, directory):
            if any("npm_package_" + field in command for field in definition_policy.LOADING_FIELDS):
                loading_readers.add("package script environment")
            bindings, reason = shell_bindings(command, directory)
            if reason:
                return reason
            for word, effective in bindings:
                # Inline JavaScript (node -e/--eval) is a runner input too. shlex
                # has removed its enclosing shell quotes from this operand.
                if loading_reference(word):
                    loading_readers.add("suite command")
                consider(posixpath.normpath(posixpath.join(effective, word)), effective)
            try:
                roots = list(definition_policy.script_roots(bindings, manifests))
            except ValueError as error:
                return str(error)
            for manifest, name, script in roots:
                key = (manifest, name)
                if key in selected_scripts:
                    continue
                selected_scripts.add(key)
                reason = follow_shell(script, posixpath.dirname(manifest) or ".")
                if reason:
                    return f"{manifest} script {name}: {reason}"
            return ""

        unestablished = follow_shell(suite_command or "", ".")
        scanned = set()
        fallback_scanned = False
        while not unestablished:
            if not queue:
                if selected_scripts or fallback_scanned or not manifests:
                    break
                # An opaque task runner (for example make) can invoke package
                # scripts without a literal manager call. Keep the conservative
                # roots there rather than silently dropping its runner inputs.
                fallback_scanned = True
                for manifest, text in manifests.items():
                    for name, script in _package_scripts(text).items():
                        selected_scripts.add((manifest, name))
                        unestablished = follow_shell(str(script), posixpath.dirname(manifest) or ".")
                        if unestablished:
                            break
                    if unestablished:
                        break
                if unestablished or not queue:
                    break
            path = queue.pop()
            if path in scanned:
                continue
            scanned.add(path)
            text = read(path)
            if loading_reference(text) or "package.json" in text:
                loading_readers.add(path)
            file_dir = posixpath.dirname(path) or "."
            cwd = cwd_of.get(path, ".")
            if _shell_script(path, text):
                # A test-directory script is pinned wholesale and would otherwise not be read,
                # which hides `sh test/run.sh` → `node list.js` (#662).
                unestablished = follow_shell(text, cwd)
                continue
            if is_test_path(path):
                continue  # JS tests are pinned wholesale, never scanned
            for specifier, inside_exec in _import_sites(text):
                if specifier is None:
                    unestablished = f"{path} selects its suite inputs through a computed path"
                    break
                if not specifier.startswith(("./", "../")):
                    continue  # a package or core module, resolved by the runtime
                target = resolve(specifier, file_dir)
                if not target or target in product:
                    continue
                if is_test_path(target) or _definition_file(target):
                    continue
                if inside_exec:
                    consider(target, file_dir)
                else:
                    unestablished = (
                        f"{path} reaches {target}, which is neither a test, a manifest "
                        "nor an input of the executed test command"
                    )
                    break
            if unestablished:
                break
            for kind, specifier in _read_paths(text):
                # ``fs`` paths are cwd-relative; ``path.join(__dirname, ...)`` is file-relative.
                directory = cwd if kind == "cwd" else file_dir
                target = resolve(specifier, directory)
                if target and target not in product:
                    consider(target, directory)
            for command in _exec_command_literals(text):
                unestablished = follow_shell(command, cwd)
                if unestablished:
                    break
        found.paths = definition_inputs.linked_inputs(entries, read, definition | pinned)
        found.boundary = unestablished
        if changes is not None:
            measured = definition_inputs.reviewed_changes(workspace, changes, patched)
            # An incomplete runner closure need not refuse a change confined to
            # known product code and retained test policy. Everything whose role
            # is unknown stays byte-for-byte on base in that case. A changed
            # unknown selector still makes preservation unproven (T15).
            unclassified = {
                path
                for path in measured
                if path not in found.paths
                and path not in product
                and not _definition_file(path)
                and not is_test_path(path)
            }
            if unestablished and not unclassified:
                found.boundary = ""
            found.changed = any(
                path in found.paths or _definition_file(path) or is_test_path(path) for path in measured
            )
            for path, status in measured.items():
                if (
                    PurePosixPath(path).name != "package.json"
                    or is_test_path(path)
                    or status == "deleted"
                    or (Path(workspace) / path).is_symlink()
                    or (path in entries and entries[path][0] != "100644" and entries[path][0] != "100755")
                ):
                    continue
                original = manifests.get(path)
                merged = definition_policy.merged_manifest(original, (Path(workspace) / path).read_text())
                if merged is not None and merged != original:
                    found.manifests[path] = merged
            if found.manifests and loading_readers:
                found.boundary = "candidate package loading fields may also select runner inputs in " + ", ".join(
                    sorted(loading_readers)
                )
        return found


# --- verification -----------------------------------------------------------


def run_suite(framework, command, tree, evidence_dir, label, *, timeout, native_execution=None):
    if framework is not None and framework.name == "junit":
        if native_execution is not None:
            raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Native JUnit report capture is not admitted")
        Path(evidence_dir).mkdir(parents=True, exist_ok=True)
        return junit_tests.run_suite(
            framework,
            command,
            tree,
            evidence_dir,
            label,
            timeout=timeout,
            execute=run_command,
            is_tracked=lambda path: bool(_git(tree, "--literal-pathspecs", "ls-files", "-z", "--", path)),
        )
    package, command = package_scope.split(framework, command)  # a package's suite runs from its directory
    root = Path(tree) / package if package else tree
    extension = (
        "node.jsonl"
        if node_tests.command_words(command)
        else "vitest.json"
        if vitest_tests.command_words(command, root)
        else "junit.xml"
    )
    xml = Path(evidence_dir) / f"{label}.{extension}"
    if native_execution is not None:
        if type(native_execution) is not native_checks.NativeCheckExecution:
            raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Original concrete native check owner required")
        xml = native_execution.result_path(tree, xml)
    xml.unlink(missing_ok=True)  # never parse a previous run's results
    actual = _with_results(framework, command, xml, root)
    if native_execution is not None:
        planned = native_execution.instrument(framework.name if framework else None, command, xml, tree)
        if planned != actual:
            raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Native suite instrumentation differs from the runner")
    actual = package_scope.command(package, actual) if package else actual
    receipt = run_command(
        actual, tree, Path(evidence_dir) / f"{label}.log", timeout=timeout, native_execution=native_execution
    )
    receipt["results"] = per_test_results(framework, receipt, xml, tree=root)
    receipt["results_expected"] = expects_results(framework, command, root)
    if native_execution is not None:
        native_execution.retain_results(receipt)
    return receipt


def execution_identity(
    workspace,
    *,
    command=None,
    dependencies_from=None,
    full=True,
    source_paths=(),
    generated_record=None,
    generated_unrecorded=False,
    ignored_inputs=None,
):
    """Conservative observable source/runtime/environment identity for receipts.

    Full dependency bytes are included, not only manifests or changed paths.
    Unknown runtimes cannot reuse clean replay evidence. This does not attest a
    remote service, wall clock or provider sandbox; those remain fresh checks.
    ``ignored_inputs`` is make_tree's: what it supplies is bound in place of the
    generated sources and ``vendor`` tree of ``dependencies_from``.
    """
    workspace = Path(workspace)
    source_snapshot_value = source_snapshot.snapshot(workspace, paths=source_paths)
    source_symlinks = [
        name
        for name, value in source_snapshot_value.get("files", {}).items()
        if value.startswith("symlink:") and name not in DEPENDENCY_DIRS
    ]
    source_metadata = source_snapshot.metadata(workspace, source_snapshot_value.get("files", {}))
    environment = test_environment(workspace)
    invocation = python_tests.parse(command)
    if invocation and invocation.prefix:
        environment.update(word.split("=", 1) for word in invocation.prefix[1:])
    relative_pythonpath = [
        p
        for p in environment.get("PYTHONPATH", "").split(os.pathsep)
        if p and not Path(p).is_absolute() and not (workspace / p).resolve().is_relative_to(workspace.resolve())
    ]
    try:
        words = shlex.split(command or "")
    except ValueError:
        words = []
    python_command = bool(
        words
        and re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", Path(words[0]).name)
        and not re.search(r"[;&|<>`$\n]", command)
    )
    python = (
        invocation.python if invocation else words[0] if python_command else python_for(dependencies_from or workspace)
    )
    node_invocation = node_tests.parse(command)
    runtime = node_invocation.node if node_invocation else python
    search_path = environment.get("PATH", "")
    if node_invocation:
        command_root = workspace.resolve()
        if os.path.dirname(runtime) and not Path(runtime).is_absolute():
            runtime = str(command_root / runtime)
        # The shell executes in the proof workspace. Relative and empty PATH
        # entries must select its Node, never a binary in the controller cwd.
        search_path = os.pathsep.join(
            str(Path(entry) if Path(entry).is_absolute() else command_root / entry)
            for entry in search_path.split(os.pathsep)
        )
    executable = shutil.which(runtime, path=search_path)
    venv_config = Path(executable).parent.parent / "pyvenv.cfg" if executable else None
    try:
        isolated_runtime = (
            venv_config is not None
            and venv_config.is_file()
            and not re.search(r"^\s*include-system-site-packages\s*=\s*true\s*$", venv_config.read_text(), re.I | re.M)
        )
    except (OSError, UnicodeError):
        isolated_runtime = False
    # An unrestricted global interpreter has mutable, unrelated packages and
    # potentially enormous inventories. Fresh checks remain valid; they are
    # deliberately not cross-invocation cache candidates. No partial hash is
    # advertised as a complete dependency identity.
    full = bool(full and python_command and isolated_runtime and not source_symlinks and not relative_pythonpath)
    # Identity collection must not import a candidate's sitecustomize from the
    # workspace. Explicit PYTHONPATH roots are hashed below, not executed here.
    paths = []
    if full:
        probe = subprocess.run(
            [executable, "-I", "-B", "-c", "import json,sys; print(json.dumps(sys.path))"],
            cwd=workspace,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if probe.returncode:
            raise ValueError("Verification runtime identity could not be collected")
        paths = json.loads(probe.stdout) + environment.get("PYTHONPATH", "").split(os.pathsep)
    paths = [(Path(p) if Path(p).is_absolute() else workspace / p).resolve() for p in paths if p]
    roots = {p for p in paths if not p.is_relative_to(workspace.resolve())}
    editable_sources, unbound_editables = {}, []
    for site in set(paths):
        for metadata in site.glob("*.dist-info/direct_url.json"):
            try:
                direct = json.loads(metadata.read_text())
                if not direct.get("dir_info", {}).get("editable"):
                    continue
                url = urlparse(direct["url"])
                target = Path(unquote(url.path)).resolve()
                # Our own editable controller is bound in addition to installed
                # dependency bytes. Other editable checkouts are not isolated
                # fixtures and must execute fresh, never use an incomplete key.
                if (
                    url.scheme != "file"
                    or url.netloc not in ("", "localhost")
                    or target not in (workspace.resolve(), Path(__file__).resolve().parent.parent)
                ):
                    unbound_editables.append(str(metadata))
                    continue
                if target != workspace.resolve():
                    # The install exposes only the package directory (pyproject maps autocode_cli to tools/). The
                    # rest of the checkout (tests, docs, run output) is not importable through it, and binding it
                    # let a file another process wrote there change the identity mid-proof (#665).
                    package = Path(__file__).resolve().parent.relative_to(target).as_posix()
                    if (target / ".git").exists():
                        # Scope Git's inventory before reading files: an unrelated test can create an
                        # empty nested repository or remove a fixture while a whole-checkout scan runs.
                        files = util.snapshot(target / package, unborn_ok=False)["files"]
                        editable_sources[str(target)] = util.digest(
                            {package + "/" + name: value for name, value in files.items()}
                        )
                    else:
                        editable_sources[str(target)] = schedule.tree_identity(
                            target / package,
                            excluded={
                                ".git",
                                ".autocode",
                                ".autocode-ui",
                                ".scenario-runs",
                                ".venv",
                                "venv",
                                "node_modules",
                                "__pycache__",
                                ".pytest_cache",
                                ".mypy_cache",
                                ".ruff_cache",
                                ".DS_Store",
                            },
                        )
            except Exception as error:
                if not schedule.optional_editable_unavailable(error):
                    raise
                unbound_editables.append(str(metadata))
    full = full and not unbound_editables
    dependency_roots = test_env.dependency_roots(dependencies_from or workspace) if full else []
    for name in DEPENDENCY_DIRS if full else ():
        candidates = dependency_roots if name in (".venv", "venv") else dependency_roots[:1]
        source = next((root / name for root in candidates if (root / name).exists()), None)
        if source:
            roots.add(source.resolve())
    if dependency_roots and (dependency_roots[0] / "vendor").exists() and ignored_inputs is None:
        roots.add((dependency_roots[0] / "vendor").resolve())
    roots = {root for root in roots if not any(parent in roots for parent in root.parents)}
    runtime_root = Path(__file__).parent
    runtime = {
        str(p.relative_to(runtime_root)): util.file_hash(p)
        for pattern in ("*.py", "*.json")
        for p in runtime_root.rglob(pattern)
        if not _ignored(str(p.relative_to(runtime_root))) and "node_modules" not in p.parts
    }
    runtime.update({"prompts/" + name: hashlib.sha256(data).hexdigest() for name, data in prompts.CONTENTS.items()})
    generated = {
        p: schedule.tree_identity(workspace / p)
        for p in _git(workspace, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory").split(
            "\0"
        )
        if p
        and not p.endswith("/")
        and PurePosixPath(p).suffix in CODE_SUFFIXES
        and not _ignored(p)
        and not any(part in DEPENDENCY_DIRS for part in PurePosixPath(p).parts)
    }
    if ignored_inputs is None:
        trusted, _notes, omitted_generated = classify_generated_sources(
            dependencies_from or workspace, generated_record, unrecorded=generated_unrecorded
        )
        copied_inputs = {p: schedule.tree_identity(Path(dependencies_from or workspace) / p) for p in trusted}
    else:
        copied_inputs, omitted_generated = ignored_inputs.identity, None
    identity = {
        "joint_inputs": joint_inputs.inventory(workspace),
        "source_revision": source_snapshot_value["revision"],
        "source_metadata": util.digest(source_metadata),
        "unbound_source_symlinks": source_symlinks,
        "reuse_supported": python_command and full,
        "cache_binding_complete": full,
        "cache_policy": "isolated_python_full_contents" if full else "fresh_execution_only",
        "environment_hash": util.digest(environment),
        "runtime_sources": util.digest(runtime),
        "interpreter": schedule.tree_identity(executable) if executable else None,
        "shell": schedule.tree_identity(command_supervision.shell()),
        "dependencies": [schedule.tree_identity(p) for p in sorted(roots)] if full else None,
        "editable_sources": editable_sources,
        "unbound_editables": unbound_editables,
        "unbound_relative_pythonpath": relative_pythonpath,
        "generated_sources": generated,
        # make_tree receives these from the dependency checkout, which may
        # differ from the candidate. They also affect the base suite: keep
        # this binding when baseline_identity drops candidate source fields.
        "generated_dependency_sources": copied_inputs,
        "platform": [sys.platform, os.uname().release, os.uname().machine],
    }
    if omitted_generated is not None:
        # The optional filter changes receipt identity even when it omits no files.
        identity["omitted_generated_sources"] = omitted_generated
    return identity


# Candidate-tree fields: they change with every Builder edit and therefore must
# not key a cache of the base suite, which always runs on the base commit (#426).
_SOURCE_IDENTITY_KEYS = ("source_revision", "source_metadata", "generated_sources", "unbound_source_symlinks")


def baseline_identity(
    workspace,
    *,
    command=None,
    dependencies_from=None,
    generated_record=None,
    generated_unrecorded=False,
    ignored_inputs=None,
):
    """Runtime/dependency identity of a base-suite run, never the candidate tree.

    The base suite is executed on a scratch tree of the base commit (plus an
    optional base patch). Builder edits to the candidate workspace cannot change
    its result, so this binding omits source revision, per-file metadata and
    generated sources. Generated inputs copied from the dependency checkout,
    dependency trees, interpreter, environment and platform stay: they do
    determine the base result.

    Reuse is offered when the remaining binding is complete enough to notice a
    runtime change (no unbound editables or relative PYTHONPATH, and dependency
    roots hashed or none present). Unlike ``execution_identity``, an isolated
    Python virtualenv is not required: npm, Go and a global interpreter still
    get a stable cache key. With ``ignored_inputs`` (make_tree's), the ignored
    vendored files are bound by what it supplies, not the whole ``vendor`` tree.
    Direct Node tests still execute fresh: external preloads, loaders and module
    resolution are not completely bound by these dependency roots.
    """
    identity = execution_identity(
        workspace,
        command=command,
        dependencies_from=dependencies_from,
        generated_record=generated_record,
        generated_unrecorded=generated_unrecorded,
        ignored_inputs=ignored_inputs,
    )
    environment = test_environment(workspace)
    roots = test_env.dependency_roots(dependencies_from or workspace)
    dependencies = []
    for name in DEPENDENCY_DIRS:
        source = next((root / name for root in roots if (root / name).exists()), None)
        if source:
            dependencies.append(schedule.tree_identity(source.resolve()))
    if roots and (roots[0] / "vendor").exists() and ignored_inputs is None:
        dependencies.append(schedule.tree_identity((roots[0] / "vendor").resolve()))
    bound = {k: v for k, v in identity.items() if k not in _SOURCE_IDENTITY_KEYS}
    bound["dependencies"] = sorted(dependencies) or identity.get("dependencies")
    bound["environment_hash"] = util.digest(environment)
    node_command = node_tests.parse(package_scope.within(command)) is not None  # a package's runs fresh too
    bound["cache_policy"] = "fresh_execution_only" if node_command else "baseline_runtime_identity"
    bound["cache_binding_complete"] = not (
        node_command or identity.get("unbound_editables") or identity.get("unbound_relative_pythonpath")
    )
    bound["reuse_supported"] = bool(
        bound["cache_binding_complete"]
        and (
            bound["dependencies"] is not None
            or not any((root / name).exists() for name in DEPENDENCY_DIRS for root in roots)
        )
    )
    return bound


ScratchPatchError = scratch_preparation.ScratchPatchError


def prepare_scratch_tree(workspace, run_dir, **parameters):
    return scratch_preparation.prepare(
        workspace,
        run_dir,
        **parameters,
        make_tree=make_tree,
        changed_files=changed_files,
        remove_tree=remove_tree,
        git=_git,
    )


def scratch_run(
    workspace,
    run_dir,
    *,
    patch=None,
    tests=(),
    command=None,
    timeout=DEFAULT_TIMEOUT,
    files=None,
    links=None,
    source_paths=(),
    dependencies_from=None,
    generated_record=None,
    generated_unrecorded=False,
    ignored_inputs=None,
    native_execution=None,
    review_mutation=False,
    review_mutation_deadline=None,
    review_source=None,
) -> dict:
    """Execute in a copy of HEAD plus current changes and an optional reviewed patch.
    File/link overlays preserve cited inputs. Review mutation returns a typed
    observation; it never fabricates a receipt for an absent project command.
    """
    workspace, run_dir = Path(workspace), Path(run_dir)
    native_scope = None
    ownership_uncertain = False
    if native_execution is not None:
        if review_mutation or review_mutation_deadline is not None or review_source is not None:
            raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Native readonly checks cannot run review mutations")
        if type(native_execution) is not native_checks.NativeCheckExecution:
            raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Original concrete native check owner required")
        if preparation.active():
            raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Native checks cannot share scheduled preparation ownership")
        native_scope = native_execution.copy(
            workspace,
            run_dir,
            command=command,
            patch=patch,
            tests=tests,
            files=files,
            links=links,
            source_paths=source_paths,
            dependencies_from=dependencies_from,
            generated_record=generated_record,
            generated_unrecorded=generated_unrecorded,
            ignored_inputs=ignored_inputs,
        )
        # All unsupported native inputs are checked before the ordinary Git,
        # dependency, patch or framework probing path can run.
        tree = native_scope.__enter__()
    else:
        parameters = preparation.scratch_parameters(
            patch, files, links, source_paths, dependencies_from, generated_record, generated_unrecorded, ignored_inputs
        )
        if review_source is not None:
            parameters["review_source"] = review_source
        if preparation.active():
            answer = preparation.execute(
                workspace,
                run_dir,
                parameters,
                tests=tests,
                command=command,
                timeout=timeout,
                review_mutation=review_mutation,
                review_mutation_deadline=review_mutation_deadline,
            )
            if review_mutation:
                review_mutations.validate(answer.get("review_mutation"), run_dir)
            return answer
        if review_mutation:
            if review_mutation is not True or tests or command is not None or files or links:
                raise ValueError("Review mutation uses only the explicit patch and its selected tests")
            envelope = review_mutations.run_scratch(
                workspace,
                run_dir,
                patch=patch,
                deadline=review_mutation_deadline,
                prepare=lambda captured, before: prepare_scratch_tree(
                    workspace, run_dir, **{**parameters, "patch": captured}, _before_patch=before
                ),
                detect=lambda tree: detect_framework(tree, python=python_for(workspace)),
                execute=run_suite,
                remove=remove_tree,
                is_test=is_test_path,
            )
            return {"review_mutation": envelope}
        try:
            tree = prepare_scratch_tree(workspace, run_dir, **parameters)
        except ScratchPatchError as error:
            return {"error": str(error), "results": None}
    review_snapshot = None
    try:
        review_snapshot = source_snapshot.snapshot(tree, paths=source_paths) if review_source else None
        review_locations = review_proof.source_locations(tree, review_source) if review_source else None
        if command is None:
            python = python_for(workspace)
            framework = detect_framework(tree, python=python)
            if framework is None and all(str(test).endswith(".py") for test in tests):
                # A project with no suite of its own: standard unittest files still run.
                framework = Framework("unittest", f"{shlex.quote(python)} -m unittest discover -v", python=python)
            command = framework.targeted(list(tests)) if framework else None
            if not command:
                return {"error": "no test command runs these files: " + ", ".join(tests), "results": None}
            receipt = run_suite(
                framework, command, tree, run_dir, "scratch-tests", timeout=timeout, native_execution=native_execution
            )
        else:
            # A command naming the workspace's absolute path runs against the copy, never the workspace.
            for root in dict.fromkeys((str(workspace.resolve()), str(workspace))):
                command = command.replace(root, str(tree))
            framework = command_framework(command) or package_scope.recognize(command, command_framework)
            if framework:
                receipt = run_suite(
                    framework,
                    command,
                    tree,
                    run_dir,
                    "scratch-command",
                    timeout=timeout,
                    native_execution=native_execution,
                )
                if receipt["exit_code"] == 0 and not receipt["timed_out"] and not schedule.complete_results(receipt):
                    return {**receipt, "error": "Test command reported zero tests or incomplete per-test results"}
                if (
                    receipt["exit_code"] == 0
                    and not receipt["timed_out"]
                    and not (receipt["results"]["passed"] or receipt["results"]["failed"])
                ):
                    return {**receipt, "error": "Test command reported only skipped tests"}
            else:
                receipt = run_command(
                    command, tree, run_dir / "scratch-command.log", timeout=timeout, native_execution=native_execution
                )
                receipt["results"] = None
                try:
                    words = shlex.split(command)
                except ValueError:
                    words = []
                if (
                    words[1:3] == ["-m", "unittest"]
                    and receipt["exit_code"] == 0
                    and not re.search(r"[;&|<>`$\n]", command)
                ):
                    text = Path(receipt["output"]).read_text(errors="replace")
                    ran = re.findall(r"^Ran (\d+) tests? in ", text, re.M)
                    if not ran or int(ran[-1]) == 0 or not re.search(r"^OK(?:\s|$)", text, re.M):
                        return {**receipt, "error": "Test command reported zero tests or incomplete output"}
        return {
            **receipt,
            "error": receipt.get("error") or "",
            **({"review_locations": review_locations} if review_source else {}),
        }
    except command_receipt.OwnershipUncertain:
        ownership_uncertain = True
        raise
    finally:
        if native_scope is not None:
            native_scope.__exit__(*sys.exc_info())
        elif not ownership_uncertain:
            try:
                changed_review_source = (
                    review_snapshot is not None
                    and source_snapshot.snapshot(tree, paths=source_paths)["files"] != review_snapshot["files"]
                )
            finally:
                remove_tree(workspace, tree)
            if changed_review_source:
                raise ValueError("Review probes changed their source or delivered tests during execution")


def baseline(
    workspace,
    base,
    run_dir,
    *,
    framework,
    suite_command,
    timeout=DEFAULT_TIMEOUT,
    dependencies_from=None,
    base_patch=None,
    generated_record=None,
    generated_unrecorded=False,
    ignored_inputs=None,
) -> dict:
    """Run the suite once on the pristine base revision, with ``base_patch`` applied (cached by the caller)."""
    evidence = Path(run_dir) / "baseline"
    tree = make_tree(
        workspace,
        base,
        Path(run_dir) / "scratch" / "baseline",
        workspace,
        {},
        dependencies_from=dependencies_from,
        patch=base_patch,
        generated_record=generated_record,
        generated_unrecorded=generated_unrecorded,
        ignored_inputs=ignored_inputs,
    )
    try:
        receipt = run_suite(framework, suite_command, tree, evidence, "suite-on-base", timeout=timeout)
    finally:
        remove_tree(workspace, tree)
    return {"base": base, "command": suite_command, "receipt": receipt, "health": suite_health(receipt)}


def _run_suite_base_definition(
    framework,
    suite_command,
    workspace,
    base,
    changes,
    run_dir,
    checks,
    *,
    timeout,
    dependencies_from=None,
    base_patch=None,
    generated_record=None,
    generated_unrecorded=False,
    ignored_inputs=None,
):
    """Execute the base suite definition over the candidate's product code (#587).

    The tree is the effective base the baseline executed — ``base`` with ``base_patch``,
    through make_tree with the other proof trees' parameters — overlaid with only the
    candidate changes that are not suite-definition paths by rule: product code stays the
    candidate's, definition files stay the base's, a deleted definition file stays, and a
    candidate-added definition file never enters. Returns {"run", "boundary"}: the receipt
    when the definition ran, or why the boundary could not be established (never a PASS).
    """
    try:
        found = _base_suite_definition(
            workspace,
            base,
            suite_command,
            base_patch=base_patch,
            changes=changes,
            junit=framework is not None and framework.name == "junit",
        )
    except (OSError, RuntimeError, ValueError) as error:
        return {"run": None, "boundary": f"the effective base could not be read ({error})"}
    if found.boundary:
        return {"run": None, "boundary": found.boundary}
    definition, patched = found.paths, found.patched
    overlay = {
        path: status
        for path, status in definition_inputs.reviewed_changes(workspace, changes, patched).items()
        if path not in definition
        and not _definition_file(path)
        and not is_test_path(path)
        and not (
            framework is not None
            and framework.name == "junit"
            and junit_first.definition_file(path, junit_first.family(suite_command))
        )
    }
    try:
        definition_inputs.check_parents(overlay, definition)
        if not found.changed:
            return {"run": None, "boundary": ""}
        with definition_inputs.isolated_folder(workspace) as holder:
            tree = make_tree(
                workspace,
                base,
                holder / "base-definition",
                workspace,
                overlay,
                dependencies_from=dependencies_from,
                patch=base_patch,
                generated_record=generated_record,
                generated_unrecorded=generated_unrecorded,
                ignored_inputs=ignored_inputs,
            )
            try:
                staged_manifests = {}
                for path, text in found.manifests.items():
                    staged = holder / "manifests" / path
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    staged.write_text(text)
                    staged_manifests[path] = staged
                scratch_overlay.apply(tree, staged_manifests, None)
                receipt = run_suite(framework, suite_command, tree, run_dir, "suite-base-definition", timeout=timeout)
            finally:
                remove_tree(workspace, tree)
    except ValueError as error:
        return {"run": None, "boundary": str(error)}
    checks["suite_base_definition_on_candidate"] = receipt
    return {"run": receipt, "boundary": ""}


def verify(
    workspace,
    base,
    run_dir,
    *,
    framework=None,
    suite_command=None,
    regression_command=None,
    reported=None,
    base_suite=None,
    timeout=DEFAULT_TIMEOUT,
    dependencies_from=None,
    independent_dependencies=None,
    allow_no_test=False,
    new_behavior=False,
    preserve_only=False,
    base_patch=None,
    source_paths=(),
    test_only_allowed=False,
    generated_record=None,
    generated_unrecorded=False,
    ignored_inputs=None,
    guards=(),
    test_root=None,
    cases=(),
    candidate_tree=None,
    package=None,
    package_exempt=(),
) -> dict:
    """Verify the candidate in ``workspace`` against ``base``; see module docstring.

    ``cases`` are the due test cases (autocode_test_cases). Their proving tests run three times in
    all on the candidate, and a ``test:`` case's also on the original code; a test whose runs disagree
    is ``unstable`` and fails the proof (autocode_proof_stability). The same proving-test command
    runs once more under the project's own coverage tool, if it has one, to report the changed
    product hunks no proving test executed (autocode_diff_coverage); that never changes the verdict.
    A ``candidate_tree`` (where the candidate is built) is left for the caller's mutation check (#699).

    ``base_patch`` is a patch file applied to ``base`` wherever the proof runs "the original
    code": a follow-up that fixes a reviewed change is proven against that change, where the
    review's findings exist, not against the code before it.

    ``new_behavior`` is for a feature rather than a bug fix: a new test proves the change
    when it passes on the candidate and did not pass on base, which includes failing to
    import there because the code it tests does not exist yet. A bug fix's test must run
    and fail on base (an import error is not a reproduction).

    ``preserve_only`` is coverage of behavior the product already implements: the diff may
    be test files alone, and each new test must pass on the base and on the candidate. It
    may also change no test at all, when the tests the cases name already exist; the diff may
    then be empty (a validation-only re-check). The suite runs on the
    candidate (first_suite.suite_guards).

    ``guards`` are the exact test names the plan's guards give. A guard may name a test the
    project already had in a file the change leaves alone, which the targeted run never sees: the
    whole suite then also runs on the base with the change's test files (base_with_tests), so each
    test runs with the content the candidate has, and its tests that pass there and on the candidate
    are kept as ``suite_pass_to_pass``, with the candidate's failures as ``failed_on_candidate``
    (autocode_regression.check_cases decides which guard may use them).

    ``dependencies_from`` is the checkout make_tree copies dependencies and ignored code
    from. ``independent_dependencies=False`` says an earlier Builder of this run worked in
    it, so it may no longer show the ignored code the base had; None compares it with
    ``workspace`` (see _document_only_base, the only reader).

    ``test_only_allowed`` covers a contract whose criteria are all test criteria without
    saying whether product code must change (a coverage or characterization build): when
    the diff turns out to be test files alone, the proof runs in that same preserve mode
    instead of failing "a fix must change product code". A test that does not pass on the
    base then still blocks it, as a mis-tagged case or a failing regression.

    ``ignored_inputs`` is make_tree's, for every scratch tree; its launch record also tells
    _document_only_base what ignored code an in-place checkout held at launch.

    ``test_root`` is caller-selected at launch. Changes outside it remain unverified.
    Only its independently canonical detected suite may establish a first suite.
    So do changes outside a run's ``package`` (autocode_package_scope): its suite does not run them.
    ``package_exempt`` names paths outside it that are not the change (autocode_package_writes.exempt).
    """
    workspace, run_dir = Path(workspace), Path(run_dir)
    test_root = test_roots.normalize(test_root) if test_root is not None else None
    run_dir.mkdir(parents=True, exist_ok=True)
    configured = node_tests.parse(suite_command)
    if configured and not (framework is not None and framework.name == "junit"):
        framework = Framework("node", suite_command, invocation=configured)
    before = source_snapshot.snapshot(workspace, paths=source_paths)["revision"]
    changes = changed_files(workspace, base, source_paths=source_paths)
    tests = [p for p in changes if is_test_path(p)]
    sources = [p for p in changes if not is_test_path(p)]
    preserve = preserve_only or (test_only_allowed and not sources)
    test_changes = {p: changes[p] for p in tests}
    fail: list[Any] = []
    unverified: list[Any] = []
    notes: list[Any] = []
    review_reasons: list[Any] = []
    checks: dict[str, dict] = {}  # command receipts only
    proof: dict = {}
    commands = select_commands(
        framework,
        [p for p in tests if changes[p] != "deleted"],
        suite_command=suite_command,
        regression_command=regression_command,
        reported=reported,
    )
    notes += commands["notes"]
    if ignored_inputs is None:
        notes += classify_generated_sources(
            dependencies_from or workspace, generated_record, unrecorded=generated_unrecorded
        )[1]
    runnable_tests = [p for p in tests if changes[p] != "deleted"]
    # Coverage the source already has: no test changed, so nothing can flip; the guards must hold.
    existing_guards = preserve_only and not runnable_tests
    # A repair whose regression test the project already had (first_suite.repairable_failures): the
    # suite on the candidate decides below whether the base's failing tests pass now.
    existing_failures = (
        first_suite.repairable_failures(base_suite, commands["suite"])
        if not runnable_tests and sources and not preserve
        else []
    )
    if not changes and not preserve_only:
        fail.append("No change: the candidate is identical to the base revision")
    elif not sources and not preserve:
        fail.append("Only test files changed; a fix must change product code")
    deleted = python_tests.deleted_test_files(
        (p for p in tests if changes[p] == "deleted"), lambda p: _git(workspace, "show", f"{base}:{p}")
    )
    if deleted:
        fail.append("Existing test files were deleted: " + ", ".join(deleted))
    removed = removed_python_tests(workspace, base, changes)
    if removed:
        fail.append("Existing tests were removed: " + ", ".join(removed[:20]))
    if not runnable_tests and not existing_guards and not existing_failures:
        (unverified if allow_no_test else fail).append(
            "No regression test was added or changed, so the bug is not shown to be reproduced"
        )
    stray = test_roots.outside(test_root, changes) if test_root is not None else []
    if stray:
        unverified.append(
            f"Files outside the test root {test_root}/ changed, and no test there covers them: " + ", ".join(stray[:10])
        )
    if package is not None and (uncovered := package_scope.uncovered(package, changes, package_exempt)):
        unverified.append(uncovered)
    for kind in ("regression", "suite"):
        if commands[f"{kind}_source"] == "builder":
            unverified.append(
                f"The {kind} command came from the Builder's own report; pass "
                f"--{'regression' if kind == 'regression' else 'test'}-command to verify with "
                "a command you trust"
            )

    trees, hidden = {}, []
    try:
        if (changes or existing_guards) and (commands["regression"] or commands["suite"]):
            trees["candidate"] = make_tree(
                workspace,
                base,
                candidate_tree or run_dir / "scratch" / "candidate",
                workspace,
                changes,
                dependencies_from=dependencies_from,
                generated_record=generated_record,
                generated_unrecorded=generated_unrecorded,
                ignored_inputs=ignored_inputs,
            )
        if trees and runnable_tests and (sources or preserve):
            trees["base_with_tests"] = make_tree(
                workspace,
                base,
                run_dir / "scratch" / "base-with-tests",
                workspace,
                test_changes,
                dependencies_from=dependencies_from,
                patch=base_patch,
                generated_record=generated_record,
                generated_unrecorded=generated_unrecorded,
                ignored_inputs=ignored_inputs,
            )
        # A base with no Go project cannot run `go test` (no module, or no packages).
        # The same fact lets the suite comparison and, when the base run reports nothing,
        # the regression comparison treat that as absence rather than a broken suite (#685).
        no_go_project = bool(
            test_root is None
            and new_behavior
            and not preserve
            and not base_patch
            and framework is not None
            and framework.name == "go"
            and _no_go_project(
                workspace,
                base,
                _copied_generated(dependencies_from, generated_record, generated_unrecorded, ignored_inputs),
            )
        )
        no_junit_project = bool(
            framework is not None
            and framework.name == "junit"
            and new_behavior
            and not preserve
            and not base_patch
            and junit_proof.no_project(
                commands["suite"],
                _git(workspace, "ls-tree", "-r", "-l", "-z", base),
                _copied_generated(dependencies_from, generated_record, generated_unrecorded, ignored_inputs),
            )
        )
        # Regression proof: identical tests, base source versus candidate source.
        if trees and runnable_tests and commands["regression"]:
            on_candidate = run_suite(
                framework,
                commands["regression"],
                trees["candidate"],
                run_dir,
                "regression-on-candidate",
                timeout=timeout,
            )
            checks["regression_on_candidate"] = on_candidate
            on_base = None
            if "base_with_tests" in trees:
                on_base = run_suite(
                    framework,
                    commands["regression"],
                    trees["base_with_tests"],
                    run_dir,
                    "regression-on-base",
                    timeout=timeout,
                )
                checks["regression_on_base"] = on_base

            def repeat(command, side, label):
                tree = trees["candidate" if side == "candidate" else "base_with_tests"]
                checks[label] = run_suite(framework, command, tree, run_dir, label, timeout=timeout)
                return checks[label]

            unstable, stability = proof_stability.repeat(
                framework.name if framework else None,
                commands["regression"],
                cases,
                on_candidate,
                on_base,
                repeat,
                base_tree="base_with_tests" in trees,
                directory=getattr(framework, "directory", None),
            )
            if unstable:
                fail.append(proof_stability.failure_text(unstable))
            proof["proof_stability"], proof["unstable"] = stability, unstable or None
            proof["diff_coverage"] = diff_coverage.collect(
                framework,
                commands["regression"],
                trees["candidate"],
                Path(run_dir) / "diff-coverage",
                lambda: product_diff(workspace, base, product_files(changes)),
                lambda command, log: run_command(command, trees["candidate"], log, timeout=timeout),
            )
            _judge_regression(
                proof_stability.without(on_candidate, unstable),
                proof_stability.without(on_base, unstable),
                fail,
                unverified,
                notes,
                proof,
                review_reasons,
                new_behavior=new_behavior,
                preserve_only=preserve,
                absent_go_base=no_go_project and _go_test(package_scope.split(framework, commands["regression"])[1]),
                absent_junit_base=no_junit_project
                and junit_first.reported_absence(on_base or {}, junit_first.family(commands["suite"])),
                known_failures=lambda: _pre_existing(
                    framework,
                    commands,
                    changes,
                    runnable_tests,
                    workspace,
                    base,
                    run_dir,
                    checks,
                    timeout=timeout,
                    dependencies_from=dependencies_from,
                    base_patch=base_patch,
                    generated_record=generated_record,
                    generated_unrecorded=generated_unrecorded,
                    ignored_inputs=ignored_inputs,
                ),
                seam_names=lambda receipt: _seam_names(workspace, base, changes, receipt),
            )
        elif "base_with_tests" in trees and commands["suite"]:
            # No targeted command: the whole suite proves the flip when base was green.
            if base_suite is None or base_suite["health"] != "passing":
                unverified.append(
                    "No targeted regression command, and the base suite is not green, "
                    "so a fail-to-pass flip cannot be attributed to the new tests"
                )
            else:
                on_base = run_suite(
                    framework,
                    commands["suite"],
                    trees["base_with_tests"],
                    run_dir,
                    "suite-on-base-with-tests",
                    timeout=timeout,
                )
                checks["regression_on_base"] = on_base
                review_reasons.append("the regression proof rests on the whole suite's exit code")
                if on_base["exit_code"] == 0 and not preserve:
                    fail.append("The new tests pass on the unfixed base code, so they do not reproduce the bug")
        elif tests and sources:
            unverified.append("No command to run the regression tests; pass --regression-command")

        # No regressions: the suite on the candidate, compared with base.
        if "candidate" in trees and (sources or preserve) and commands["suite"]:
            reuse = checks.get("regression_on_candidate") if commands["suite"] == commands["regression"] else None
            on_candidate = reuse or run_suite(
                framework, commands["suite"], trees["candidate"], run_dir, "suite-on-candidate", timeout=timeout
            )
            checks["suite_on_candidate"] = on_candidate
            comparable = base_suite if base_suite and base_suite.get("command") == commands["suite"] else None
            if existing_failures:
                first_suite.existing_flip(
                    on_candidate, base_suite, existing_failures, proof, fail, unverified, notes, allow_no_test
                )
            if framework is not None and framework.name == "junit" and comparable:
                checks["suite_on_base"] = comparable["receipt"]
            # A positively identified document-only project (a root README.md,
            # plus .gitignore files and empty files when the ignored code is read
            # from a checkout the candidate does not edit, all regular and
            # non-executable, and no ignored code copied into the trees) may
            # introduce its first source and suite. Filename heuristics cannot
            # rule out old behavior: empty collection can hide a filtered
            # existing program, so any other base file or copied ignored code
            # still counts.
            allow_empty_base = bool(
                test_root is None
                and new_behavior
                and not preserve
                and not base_patch
                and comparable
                and comparable.get("base") == base
                and _document_only_base(
                    workspace,
                    base,
                    dependencies_from=dependencies_from,
                    independent=independent_dependencies,
                    ignored_inputs=ignored_inputs,
                )
            )
            # A first Go project on a base that has code in another language (a C#
            # port, for example) has no Go suite to preserve. `go test` then reports
            # no packages — inside a parent module that the candidate just added,
            # "matched no packages" — and that is not a broken base suite. A base
            # that already has a Go module or Go source keeps the ordinary rule. A package's
            # suite command is read inside its `cd PKG && `, as per_test_results reads it.
            suite_go = _go_test(package_scope.split(framework, commands["suite"] or "")[1])
            allow_absent_go = bool(no_go_project and comparable and comparable.get("base") == base and suite_go)
            # The base's own suite definition, executed over the candidate's product code,
            # decides preservation for an exit-code-only script-driven suite (#587). The
            # trigger is evidential: passing base, completed exit-0 candidate suite,
            # no per-test results, and no already-established regression failure.
            # The definition planner skips the extra run only when its retained
            # inputs did not change. Opaque commands need the proof regardless
            # of their language; a custom Python command can hide edited tests.
            base_definition = None
            if (
                comparable is not None
                and comparable.get("health") == "passing"
                and not fail
                and not allow_empty_base
                and command_receipt.completed(on_candidate)
                and not on_candidate["timed_out"]
                and on_candidate["exit_code"] == 0
                and on_candidate.get("results") is None
            ):
                base_definition = _run_suite_base_definition(
                    framework,
                    commands["suite"],
                    workspace,
                    base,
                    changes,
                    run_dir,
                    checks,
                    timeout=timeout,
                    dependencies_from=dependencies_from,
                    base_patch=base_patch,
                    generated_record=generated_record,
                    generated_unrecorded=generated_unrecorded,
                    ignored_inputs=ignored_inputs,
                )
            elif (
                comparable is not None
                and comparable.get("health") != "passing"
                and not allow_empty_base
                and command_receipt.completed(on_candidate)
                and not on_candidate["timed_out"]
                and on_candidate["exit_code"] == 0
                and on_candidate.get("results") is None
            ):
                # G1 (#652): a base suite that is not green cannot anchor preservation.
                # Its own definition fails, so a candidate that narrows the script to
                # hide a broken function shows exit 0 with nothing contradicted. A
                # passing exit-code-only candidate suite over a red base is missing
                # evidence, and missing evidence is UNVERIFIED, never PASS.
                unverified.append(
                    "The base suite is not green, so the candidate's exit-code-only suite "
                    "cannot prove the base definition still passes over the new code"
                )
            empty_root = None
            if (
                test_root is not None
                and new_behavior
                and not preserve
                and not base_patch
                and suite_command is None
                and regression_command is None
                and framework is not None
                and comparable
                and comparable.get("base") == base
                and test_roots.empty_on_base(workspace, base, test_root)
            ):
                # Public Framework fields and detected:* labels are not provenance.
                canonical = detect_framework(workspace, python=framework.python, test_root=test_root, base=base)
                if (
                    canonical is not None
                    and canonical.test_root == test_root
                    and (framework.name, framework.python, commands["suite"])
                    == (canonical.name, canonical.python, canonical.suite)
                ):
                    empty_root = test_root
            if test_root is not None and not (
                empty_root is not None
                and test_roots.first_suite((comparable or {}).get("receipt") or {}, on_candidate)
                and schedule.complete_results(on_candidate)
            ):
                receipt = (comparable or {}).get("receipt") or {}
                results = receipt.get("results")
                collector = schedule.collection_kind(commands["suite"])
                typed = (
                    schedule.complete_results(receipt)
                    and bool((results or {}).get("passed"))
                    and (not collector or schedule.complete_results(on_candidate))
                )
                # A known collector without parsed results is not an untyped
                # script merely because the supplied Framework mislabels it.
                untyped = (
                    results is None
                    and not receipt.get("results_expected")
                    and not collector
                    and receipt.get("exit_code") == 0
                )
                baseline_evidence = (
                    comparable is not None
                    and comparable.get("base") == base
                    and command_receipt.completed(receipt)
                    and receipt.get("timed_out") is False
                    and (typed or untyped)
                )
                if not baseline_evidence:
                    unverified.append(
                        "The scoped suite has no comparable executed baseline evidence or canonical "
                        "first-root authority; preservation of existing behavior is unproven"
                    )
            if framework is not None and framework.name == "junit" and comparable and not no_junit_project:
                retained = _run_suite_base_definition(
                    framework,
                    commands["suite"],
                    workspace,
                    base,
                    changes,
                    run_dir,
                    checks,
                    timeout=timeout,
                    dependencies_from=dependencies_from,
                    base_patch=base_patch,
                    generated_record=generated_record,
                    generated_unrecorded=generated_unrecorded,
                    ignored_inputs=ignored_inputs,
                )
                junit_proof.judge_definition(retained, comparable, fail, unverified, notes, judge=_judge_suite)
            _judge_suite(
                on_candidate,
                comparable,
                fail,
                unverified,
                notes,
                allow_empty_base=allow_empty_base,
                allow_absent_go=allow_absent_go,
                allow_absent_junit=no_junit_project and comparable is not None and comparable.get("base") == base,
                base_definition=base_definition,
                empty_root=empty_root,
                kept_tests=proof.get("pass_to_pass") or (),
                untested=bool(base) and _untested_base(workspace, base),
                repaired=proof.get("fail_to_pass")
                if existing_failures
                else first_suite.repaired_by_flip(comparable, checks.get("regression_on_base"), on_candidate),
            )
            # make_tree copies ignored test files into both trees, so a guard could rest on a test base never held.
            hidden = [
                path
                for path in _copied_generated(dependencies_from, generated_record, generated_unrecorded, ignored_inputs)
                if is_test_path(path)
            ]
            if hidden and existing_guards:
                unverified.append(
                    "Ignored test files are copied into the proof trees, so the guards cannot "
                    "be shown to rest on tests the base revision holds: " + ", ".join(hidden[:5])
                )
            with_tests = None
            if guards and "base_with_tests" in trees and not hidden:
                # Without a targeted command the whole suite already ran there (regression_on_base).
                with_tests = (checks.get("regression_on_base") if not commands["regression"] else None) or run_suite(
                    framework,
                    commands["suite"],
                    trees["base_with_tests"],
                    run_dir,
                    "suite-on-base-with-tests",
                    timeout=timeout,
                )
                checks.setdefault(
                    "regression_on_base" if not commands["regression"] else "suite_on_base_with_tests", with_tests
                )
            first_suite.suite_guards(on_candidate, comparable, proof, unchanged=existing_guards, with_tests=with_tests)
        elif sources or preserve:
            unverified.append(
                "No project test command was found; existing behavior was not checked (pass --test-command)"
            )
    finally:
        for tree in [tree for name, tree in trees.items() if not (candidate_tree and name == "candidate")]:
            remove_tree(workspace, tree)
    after = source_snapshot.snapshot(workspace, paths=source_paths)["revision"]
    if after != before:
        unverified.append("The candidate changed while it was being verified; verify again")
    for label, receipt in checks.items():
        if not command_receipt.completed(receipt):
            unverified.append(f"{label} has no complete owned-command evidence")
        if receipt.get("results_format") == "junit" and not schedule.intact(receipt):
            unverified.append(
                f"{label}: {receipt.get('report_error') or 'JUnit report capture is missing, invalid or changed'}"
            )
    stats = diff_stats(workspace, base, changes)
    if stats["binary_files"]:
        review_reasons.append("binary files changed: " + ", ".join(stats["binary_files"][:5]))
    if stats["non_code_files"]:
        review_reasons.append("non-code files changed: " + ", ".join(stats["non_code_files"][:5]))
    verdict = FAIL if fail else UNVERIFIED if unverified else PASS
    return {
        "verdict": verdict,
        "failures": fail,
        "unverified": unverified,
        "notes": notes,
        "review_reasons": review_reasons,
        "base": base,
        "base_patch": str(base_patch) if base_patch else None,
        "source_revision": before,
        "changes": changes,
        "test_files": tests,
        "source_files": sources,
        "stats": stats,
        "commands": {k: commands[k] for k in ("suite", "suite_source", "regression", "regression_source")},
        "framework": framework.to_dict() if framework else None,
        "baseline": (
            {
                "health": base_suite["health"],
                "exit_code": base_suite["receipt"]["exit_code"],
                "output": base_suite["receipt"]["output"],
            }
            if base_suite
            else None
        ),
        "fail_to_pass": proof.get("fail_to_pass"),
        "pass_to_pass": proof.get("pass_to_pass"),
        "not_run_on_base": proof.get("not_run_on_base"),
        "failed_on_candidate": proof.get("failed_on_candidate"),
        "suite_pass_to_pass": proof.get("suite_pass_to_pass"),
        "ignored_test_files": hidden,
        "no_test_changed": existing_guards,
        "guard_run_incomplete": bool(proof.get("guard_run_incomplete")),
        "unstable": proof.get("unstable"),
        "proof_stability": proof.get("proof_stability"),
        "diff_coverage": proof.get("diff_coverage"),
        "checks": checks,
    }


def product_files(changes) -> list[tuple[str, str]]:
    """The changed product code (not tests, not deletions) diff coverage and mutants read: (path, status)."""
    return [
        (path, status)
        for path, status in changes.items()
        if status != "deleted" and is_code_path(path) and not is_test_path(path)
    ]


def product_diff(workspace, base, files) -> str:
    """diff_coverage.product_diff of the staged tree changed_files compares (an untracked file is no deletion)."""
    with _staged(workspace, base) as (env, _):
        return diff_coverage.product_diff(workspace, base, files, env=env)


def _judge_regression(
    on_candidate,
    on_base,
    fail,
    unverified,
    notes,
    proof,
    review_reasons,
    *,
    known_failures,
    new_behavior=False,
    preserve_only=False,
    absent_go_base=False,
    absent_junit_base=False,
    seam_names=None,
):
    """Judge the targeted runs of the changed test files.

    With per-test results, the proof is a named test that ran and failed on base
    and ran and passed on the candidate. A module that fails to import on base
    (for example because the test imports a name the fix adds) is not a test that
    ran; ``seam_names(on_base)`` names such added names so the failure can say so.
    A test in the same files that already fails on the pristine base (for
    example one needing a network) neither blocks the fix nor counts as proof.
    Without per-test results, exit codes decide and the change needs review.
    """
    candidate = on_candidate.get("results")
    base = (on_base or {}).get("results")
    if not command_receipt.completed(on_candidate) or on_base is not None and not command_receipt.completed(on_base):
        unverified.append("Command ownership was interrupted; no complete regression proof exists")
        failed = set((candidate or {}).get("failed") or [])
        if failed:
            unexplained = sorted(failed - (known_failures() or set()))
            if unexplained:
                fail.append("The regression tests fail on the candidate: " + ", ".join(unexplained[:20]))
        return
    if on_candidate["timed_out"]:
        fail.append("The regression tests timed out on the candidate")
        return
    if on_candidate.get("results_expected"):
        if candidate is None:
            if on_candidate["exit_code"] == 0:
                unverified.append(
                    "The regression run exited 0 without reporting any test result (did the process exit early?)"
                )
            else:
                fail.append("The regression tests fail on the candidate (no test results were reported)")
            return
        failed = set(candidate["failed"])
        if candidate["complete"]:
            passed = set(candidate["passed"])
        else:
            # Incompleteness only makes the absence of a failure unproven (#421): a named
            # failure is judged first, and the fail-to-pass proof is given up after it.
            unverified.append("Per-test results of the regression run were incomplete")
            if failed:
                known = known_failures() or set()
                unexplained = sorted(failed - known)
                if unexplained:
                    fail.append("The regression tests fail on the candidate: " + ", ".join(unexplained[:20]))
                else:
                    notes.append(
                        "Tests in the changed files that already fail on base were not counted: "
                        + ", ".join(sorted(failed)[:20])
                    )
            return
        if candidate["complete"] and not passed:
            fail.append("The regression command ran no passing tests")
        if candidate["complete"] and not failed and on_candidate["exit_code"] not in (0, None):
            # The runner failed for a reason no test names (a coverage floor, a failing session hook): the
            # per-test results alone cannot show the regression run passed (#1113).
            fail.append(
                f"The regression command exited {on_candidate['exit_code']} although every reported test passed, "
                "so its passing results cannot be trusted"
            )
        if failed:
            known = known_failures() or set()
            unexplained = sorted(failed - known)
            if unexplained:
                fail.append("The regression tests fail on the candidate: " + ", ".join(unexplained[:20]))
            else:
                notes.append(
                    "Tests in the changed files that already fail on base were not counted: "
                    + ", ".join(sorted(failed)[:20])
                )
        if on_base is None:
            return
        if on_base["timed_out"]:
            unverified.append("The regression tests timed out on base; no complete fail-to-pass proof exists")
            return
        if base is None:
            # Outside a module, `go test` on the new files exits before any test event.
            # Those tests did not pass on a base that has no Go project (#685).
            if (
                new_behavior
                and not preserve_only
                and (absent_go_base or absent_junit_base)
                and passed
                and on_base is not None
                and (absent_junit_base or first_suite.go_reported_nothing(on_base))
            ):
                names = sorted(passed)
                proof["fail_to_pass"] = names
                proof["pass_to_pass"] = []
                proof["not_run_on_base"] = names
                return
            # A module that reads a seam while loading can stop the whole run before it reports any test.
            seam = seam_names(on_base) if seam_names and not new_behavior else []
            unverified.append(
                "The regression run on the base code reported no test results"
                + (". " + proof_seam.reason([], seam) if seam else "")
            )
            return
        if not base.get("complete"):
            unverified.append("Per-test results on the base code were incomplete")
            return
        setup_errors = base.get("setup_errors", {}) if not new_behavior else {}
        ran_and_failed = set(base["failed"]) - set(base["collection_errors"]) - set(setup_errors)
        if setup_errors:
            notes.append(test_setup.proof_note(setup_errors))
        # New behavior: a test that did not pass on base (failed, or could not even import
        # the code it tests) and passes now. A bug fix needs a test that ran and failed.
        flipped = sorted(passed - set(base["passed"])) if new_behavior else sorted(ran_and_failed & passed)
        proof["fail_to_pass"] = flipped
        # Tests that ran and passed on the original code and still pass: the proof
        # preserve cases are matched against (autocode_regression.check_cases).
        proof["pass_to_pass"] = sorted(set(base["passed"]) & passed)
        # Passing tests that never ran on the original code (their module did not import there): a
        # guard's test there is not shown to fail before, only not shown to pass (check_cases).
        proof["not_run_on_base"] = sorted(passed - set(base["passed"]) - set(base["failed"]))
        # Recognized test preparation failures are excluded above. Other missing-name
        # errors may be real product bugs, so preserve the reviewer warning for them.
        seam = seam_names(on_base) if seam_names and (base["collection_errors"] or flipped) and not new_behavior else []
        if not flipped and new_behavior and not preserve_only:
            fail.append(
                "No new or changed test passes with the change and did not pass without it, "
                "so the tests do not show the new behavior"
            )
        elif not flipped and not preserve_only:
            if setup_errors:
                fail.append(test_setup.proof_note(setup_errors))
            elif seam:
                fail.append(proof_seam.reason(base["collection_errors"], seam))
            elif base["collection_errors"]:
                fail.append(
                    "On the unfixed code the new tests only fail to import or collect ("
                    + ", ".join(base["collection_errors"][:5])
                    + "), so no test shows the bug. "
                    "Write the regression test against behavior that exists before the fix."
                )
            else:
                fail.append(
                    "No test fails on the unfixed base code and passes with the fix, "
                    "so the tests do not reproduce the bug"
                )
        elif seam:
            if base["collection_errors"]:
                notes.append(proof_seam.note(base["collection_errors"], seam))
            review_reasons.append(proof_seam.review_reason(flipped, seam))
        return
    # Exit codes only: honest, but weaker, so a person or the Reviewer must read the change.
    review_reasons.append("the regression proof rests on exit codes, not named tests")
    if on_candidate["exit_code"] != 0:
        fail.append("The regression tests fail on the candidate")
    if on_base is None:
        return
    if on_base["exit_code"] == 0 and not preserve_only:
        fail.append("The regression tests also pass on the unfixed base code, so they do not reproduce the bug")
    elif on_base["timed_out"]:
        unverified.append("The regression tests timed out on base; no complete fail-to-pass proof exists")


def _seam_names(workspace, base, changes, receipt):
    """Names the unfixed run reports missing that the candidate's source change adds and its tests use."""
    added, test_words = set(), set()
    for path, status in changes.items():
        file = Path(workspace) / path
        if status == "deleted" or not is_code_path(path) or not file.is_file() or file.is_symlink():
            continue
        after = _read(file)
        if is_test_path(path):
            test_words |= proof_seam.words(after)
        else:
            before = "" if status == "added" else _git(workspace, "show", f"{base}:{path}", check=False)
            added |= proof_seam.added_names(path, before, after)
    return proof_seam.used(_read(receipt.get("output") or ""), added, test_words)


def _pre_existing(
    framework,
    commands,
    changes,
    runnable_tests,
    workspace,
    base,
    run_dir,
    checks,
    *,
    timeout,
    dependencies_from,
    base_patch=None,
    generated_record=None,
    generated_unrecorded=False,
    ignored_inputs=None,
):
    """Failures of the changed test files' base versions on the pristine base, by test id."""
    if not framework or not framework.per_test or not str(commands["regression_source"]).startswith("derived"):
        return None
    existing = [p for p in runnable_tests if changes[p] == "modified"]
    command = framework.targeted(existing)
    if not command:
        return set()
    tree = make_tree(
        workspace,
        base,
        Path(run_dir) / "scratch" / "base",
        workspace,
        {},
        dependencies_from=dependencies_from,
        patch=base_patch,
        generated_record=generated_record,
        generated_unrecorded=generated_unrecorded,
        ignored_inputs=ignored_inputs,
    )
    try:
        receipt = run_suite(framework, command, tree, run_dir, "regression-files-on-base", timeout=timeout)
    finally:
        remove_tree(workspace, tree)
    checks["regression_files_on_base"] = receipt
    return set(receipt["results"]["failed"]) if command_receipt.completed(receipt) and receipt.get("results") else None


def _judge_suite(
    on_candidate,
    base_suite,
    fail,
    unverified,
    notes,
    *,
    allow_empty_base=False,
    allow_absent_go=False,
    allow_absent_junit=False,
    base_definition=None,
    empty_root=None,
    kept_tests=(),
    untested=False,
    repaired=(),
):
    """Nothing that passed on base may fail, be skipped, be deselected or disappear (``kept_tests``: first_suite).

    ``repaired``: the base's failing tests that pass on the candidate (first_suite.existing_flip when
    no test changed, first_suite.repaired_by_flip when the change also changed tests).
    """
    if on_candidate["timed_out"]:
        fail.append("The project suite timed out on the candidate")
        return
    candidate = on_candidate.get("results")
    base_receipt = (base_suite or {}).get("receipt") or {}
    base_results = base_receipt.get("results")
    candidate_owned = command_receipt.completed(on_candidate)
    base_owned = command_receipt.completed(base_receipt)
    if not candidate_owned or not base_owned:
        unverified.append("Command ownership was interrupted; preservation is unproven")
        failed = set((candidate or {}).get("failed") or [])
        if base_owned and (base_results or {}).get("complete"):
            new = sorted(failed - set(base_results["failed"]))
        else:
            new = sorted(failed & set((base_results or {}).get("passed") or []))
        if new:
            fail.append("Tests that pass on base fail on the candidate: " + ", ".join(new[:20]))
        return
    if candidate is not None and not candidate["failed"] and on_candidate["exit_code"] not in (0, None):
        # No test names the nonzero exit (a coverage floor, a failing session hook): complete per-test
        # results do not show the suite passed (#1113).
        problem = f"The project suite exited {on_candidate['exit_code']} on the candidate with no failing test reported"
        if base_receipt.get("exit_code") == 0:
            fail.append(problem + ", and it passes on base")
        else:
            unverified.append(problem + "; preservation is unproven")
    # A first Go suite reports the pattern as a build failure, or no results at all.
    # That is absence of a Go project, not a collection error to preserve (#685).
    absent_go = first_suite.absent_go_suite(base_receipt, on_candidate, no_go_project=allow_absent_go)
    if junit_first.absent_suite(
        base_receipt,
        on_candidate,
        family=junit_first.family(on_candidate.get("command")),
        no_project=allow_absent_junit,
    ):
        notes.append(
            "The pinned base has no project for this JUnit suite; it reports no project on base and passes completely on the candidate"
        )
        return
    # A module that never imported, collected or built is not an executed test; a failed
    # hook or cancellation is (Node and Vitest report both as collection errors, so only
    # ``uncollected`` carries the rule, #503). Keep comparison below so a separately
    # observed regression still wins over incomplete preservation. Results saved by
    # parsers without ``uncollected`` fall back to collection_errors and fail closed.
    for label, results in (("base", base_results), ("candidate", candidate)):
        collection = (results or {}).get("uncollected", (results or {}).get("collection_errors")) or []
        if collection and not (label == "base" and absent_go):
            unverified.append(
                f"The {label} suite has collection errors; preservation is unproven: " + ", ".join(collection[:5])
            )
    # Incompleteness only makes the absence of a failure unproven (#421): a candidate
    # failure that was observed passing on base stays FAIL below.
    if not absent_go and (
        base_receipt.get("timed_out") or (base_results is not None and not base_results.get("complete"))
    ):
        unverified.append("The base suite was incomplete; preservation of its passing tests is unproven")
    if candidate is not None and not candidate.get("total"):
        unverified.append("The project suite reported zero tests or incomplete per-test results")
        return
    if (
        empty_root is not None
        and test_roots.first_suite(base_receipt, on_candidate)
        and schedule.complete_results(on_candidate)
    ):
        notes.append(
            f"The base holds nothing under {empty_root}/, so the suite rooted there has no existing "
            "behavior to preserve; it ran and passed completely on the candidate"
        )
        return
    if candidate is not None and not candidate.get("complete"):
        unverified.append("The project suite's per-test results were incomplete; only its observed failures are judged")
    if on_candidate.get("results_expected") and candidate is None:
        if on_candidate["exit_code"] == 0:
            unverified.append(
                "The project suite exited 0 without reporting any test result (did the process exit early?)"
            )
        elif base_receipt.get("exit_code") == 0:
            fail.append("The project suite passes on base but fails on the candidate")
        else:
            unverified.append("The project suite reported no test results on the candidate")
        return
    # Matching runner failures do not establish preserved behavior: a broken
    # collector may report a complete set of error placeholders without running
    # the suite. Keep comparing observed failures below so a real regression
    # remains FAIL even when preservation coverage is unverified (#479).
    # Python 3.14 unittest and pytest use exit 5 for honest empty collection.
    # That is usable only when the caller identified a document-only pinned base and
    # the candidate's new suite actually ran and passed in full.
    empty_base = (
        allow_empty_base
        and first_suite.empty_collection(base_receipt)
        and on_candidate["exit_code"] == 0
        and schedule.complete_results(on_candidate)
        and candidate["passed"]
        and not candidate["failed"]
    )
    if empty_base:
        notes.append("The pinned base has no executable source, so its empty suite has no behavior to preserve")
    if absent_go:
        notes.append(
            "The pinned base has no Go project, so this suite has no existing behavior to preserve; "
            "it reports no packages on base and passes completely on the candidate"
        )
        return
    kept = not empty_base and first_suite.untested_base_kept(base_receipt, on_candidate, kept_tests, untested=untested)
    if kept:
        notes.append(first_suite.UNTESTED_BASE_KEPT)
    repaired_all = first_suite.repaired_all(base_results, repaired, notes)
    if base_results is not None:
        if not (empty_base or kept or repaired_all) and (
            not schedule.complete_results(base_receipt) or not base_results["passed"]
        ):
            unverified.append(first_suite.no_base_evidence(untested and first_suite.no_base_suite(base_receipt)))
    elif base_suite is not None and base_suite.get("health") == "broken":
        if first_suite.absent_base_suite(base_receipt, on_candidate, document_only=allow_empty_base):
            notes.append(
                "The pinned documentation-only base has no existing behavior to preserve; "
                "its new suite is absent on base and passes completely on the candidate"
            )
        elif not kept:
            unverified.append(
                first_suite.no_base_evidence(untested and first_suite.no_base_suite(base_receipt), ran=False)
            )
    if candidate is not None and base_results is not None:
        if base_results.get("complete") and not base_receipt.get("timed_out"):
            new = sorted(set(candidate["failed"]) - set(base_results["failed"]))
        else:
            # The base run stopped early, so a test absent from its failures may simply
            # never have run: only a failure that was observed passing on base is a FAIL.
            new = sorted(set(candidate["failed"]) & set(base_results["passed"]))
        if new:
            fail.append("Tests that pass on base fail on the candidate: " + ", ".join(new[:20]))
        if candidate["complete"] and base_results["complete"]:
            lost = sorted(set(base_results["passed"]) - set(candidate["passed"]) - set(new))
            if lost:
                fail.append(
                    "Tests that pass on base did not pass on the candidate (skipped, deselected, "
                    "renamed or missing): " + ", ".join(lost[:20])
                )
        elif candidate.get("complete") and candidate["total"] < base_results["total"]:
            fail.append(
                f"Fewer tests ran on the candidate ({candidate['total']}) than on base ({base_results['total']})"
            )
        if base_results["failed"] and not new:
            notes.append(f"{len(base_results['failed'])} test(s) already failed on base; none newly fail")
        return
    if on_candidate["exit_code"] == 0:
        # An exit code proves preservation only when both trees ran the same suite. A
        # script-driven command runs each tree's own definition, so the base's definition
        # was also executed over the candidate's product code (#528, #587): when that run
        # fails, the candidate narrowed which tests the suite runs; when it cannot run or
        # complete, or its boundary cannot be established, preservation is unproven. A
        # document-only base has no old behavior, so that first suite may still pass.
        boundary = (base_definition or {}).get("boundary") or ""
        definition_run = (base_definition or {}).get("run")
        if boundary:
            unverified.append(
                "The base suite definition could not be established ("
                + boundary
                + "); preservation of existing behavior is unproven"
            )
        elif definition_run is not None:
            if definition_run["timed_out"]:
                unverified.append(
                    "The base suite definition timed out over the candidate code; "
                    "preservation of existing behavior is unproven"
                )
            elif not command_receipt.completed(definition_run):
                unverified.append(
                    "The base suite definition run over the candidate code did not "
                    "complete; preservation of existing behavior is unproven"
                )
            elif definition_run["exit_code"] in (126, 127):
                unverified.append(
                    "The base suite definition command could not run over the candidate "
                    f"code (exit {definition_run['exit_code']}); preservation of existing "
                    "behavior is unproven"
                )
            elif definition_run["exit_code"] != 0:
                fail.append(
                    "The base suite definition fails against the candidate code: the candidate "
                    "changed which tests the suite runs, so tests the base ran no longer pass"
                )
            else:
                # Exit 0 alone cannot erase the base's passing-test identities (G5).
                # Apply the same completeness, collection and preservation rules to
                # the retained definition as to an ordinary candidate suite.
                definition_fail, definition_unverified, definition_notes = [], [], []
                _judge_suite(definition_run, base_suite, definition_fail, definition_unverified, definition_notes)
                if base_results is not None and definition_run.get("results") is None:
                    definition_unverified.append("The base's per-test results are missing from the definition run")
                elif base_results is None and (definition_run.get("results") or {}).get("failed"):
                    definition_fail.append("Tests fail under the base definition despite its zero exit status")
                if definition_run.get("results") is not None and not definition_run["results"].get("passed"):
                    definition_unverified.append("The definition run provided no passing-test evidence")
                prefix = "The base suite definition over the candidate code: "
                fail.extend(prefix + reason for reason in definition_fail)
                unverified.extend(prefix + reason for reason in definition_unverified)
                notes.extend(prefix + reason for reason in definition_notes)
        return
    if base_suite is None:
        unverified.append("The project suite fails on the candidate and there is no base run to compare with")
    elif base_receipt.get("exit_code") == 0:
        fail.append("The project suite passes on base but fails on the candidate")
    else:
        unverified.append(
            "The project suite already fails on base and per-test results are unavailable, "
            "so new failures cannot be ruled out (narrow it with --test-command)"
        )


def feedback(result, *, limit=3000) -> str:
    """A compact, model-readable account of a failed or unverified verification."""
    lines = [f"Verdict: {result['verdict']}"]
    if (result.get("framework") or {}).get("note"):
        lines.append(f"Note: {result['framework']['note']}")
    lines += [f"- FAIL: {reason}" for reason in result["failures"]]
    lines += [f"- UNVERIFIED: {reason}" for reason in result["unverified"]]
    lines += [f"- Note: {note}" for note in result["notes"]]
    if result.get("fail_to_pass"):
        lines.append("Fail-to-pass tests: " + ", ".join(result["fail_to_pass"][:20]))
    commands = result["commands"]
    lines.append(f"Regression command ({commands['regression_source']}): {commands['regression']}")
    lines.append(f"Suite command ({commands['suite_source']}): {commands['suite']}")
    for label, receipt in result["checks"].items():
        lines.append(f"\n## {label}: exit {receipt['exit_code']}{' (timed out)' if receipt['timed_out'] else ''}")
        lines.append(receipt["tail"][-limit:])
    return "\n".join(lines)
