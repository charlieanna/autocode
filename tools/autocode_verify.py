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
    from . import autocode_source_snapshot as source_snapshot
except ImportError:
    import autocode_source_snapshot as source_snapshot

import contextlib
import json
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import tempfile
import sys
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse

try:
    from . import autocode_util as util, autocode_agent_env as agent_env
    from . import autocode_test_environment as test_env
    from . import autocode_python_tests as python_tests
    from . import autocode_investigation_workspace as investigation_workspace
    from . import autocode_verification_schedule as schedule
    from . import autocode_node_tests as node_tests, autocode_proof_seam as proof_seam
    from . import autocode_vitest_tests as vitest_tests
    from . import autocode_scratch_overlay as scratch_overlay
    from . import autocode_test_setup as test_setup
    from . import autocode_first_suite as first_suite, autocode_test_root as test_roots
    from . import autocode_command_supervision as command_supervision, autocode_command_receipt as command_receipt
except ImportError:
    import autocode_util as util, autocode_agent_env as agent_env
    import autocode_test_environment as test_env
    import autocode_python_tests as python_tests
    import autocode_investigation_workspace as investigation_workspace
    import autocode_verification_schedule as schedule
    import autocode_node_tests as node_tests
    import autocode_vitest_tests as vitest_tests
    import autocode_scratch_overlay as scratch_overlay
    import autocode_proof_seam as proof_seam
    import autocode_test_setup as test_setup
    import autocode_first_suite as first_suite, autocode_test_root as test_roots
    import autocode_command_supervision as command_supervision, autocode_command_receipt as command_receipt

PASS, FAIL, UNVERIFIED = "PASS", "FAIL", "UNVERIFIED"
# Directories that hold tests wherever they appear, and ones that do only at the repository root:
# numpy/testing/ and django/test/ are shipped product code, while a top-level test/ is not.
TEST_DIRS = frozenset({"tests", "__tests__", "__snapshots__", "testdata", "test_data"})
ROOT_TEST_DIRS = frozenset({"test", "spec"})
TEST_NAME = re.compile(  # case-sensitive: Latest.java and Contest.kt are product code
    r"^(test_.*\.py|.*_tests?\.py|conftest\.py|.*\.(test|spec)\.[cm]?[jt]sx?|.*\.snap|.*_test\.go"
    r"|.*_(spec|test)\.rb|.*Tests?\.(java|kt|cs|swift|scala)|Test[A-Z_]\w*\.(java|kt|cs|swift|scala)"
    r"|test_.*\.(rb|sh))$")
PYTHON_TEST_MODULE = python_tests.TEST_MODULE
CODE_SUFFIXES = frozenset({".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".go", ".rs", ".rb", ".java",
                           ".kt", ".kts", ".scala", ".swift", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".php",
                           ".m", ".mm", ".ex", ".exs", ".erl", ".hs", ".ml", ".lua", ".pl", ".sh", ".dart", ".zig"})
DEPENDENCY_DIRS = ("node_modules", ".venv", "venv")
COLLECTION_ERROR = re.compile(r"unittest\.loader\.(_FailedTest|ModuleImportFailure)|^::")
UNITTEST_HEADER = re.compile(r"^(\w+) \(([\w.-]+)\)")
UNITTEST_STATUS = re.compile(r"\.\.\. (ok|FAIL|ERROR|skipped|expected failure|unexpected success)\b")
UNITTEST_BARE_STATUS = re.compile(r"(ok|FAIL|ERROR|expected failure|unexpected success)|skipped( .*)?")
UNITTEST_FIXTURES = frozenset({"setUpClass", "tearDownClass", "setUpModule", "tearDownModule"})
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
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, encoding="utf-8", errors="replace",
                            env=env, input=input)
    if check and result.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _document_only_base(workspace, base, *, dependencies_from=None, independent=None, ignored_inputs=None):
    """True when the base the proof runs holds no behavior: every entry of the
    pinned tree is a regular non-executable blob that is the root README.md or,
    when the candidate cannot have hidden ignored code (below), a Markdown
    document (``.md``), a .gitignore or an empty file; and make_tree copies no
    git-ignored code into the proof trees (``generated_sources`` of
    ``dependencies_from`` is empty, and so is the launch inventory of an
    in-place run, ``ignored_inputs``: autocode_launch_inputs.Supply).

    Source/test filename conventions cannot establish absence of existing
    behavior. Keep this positive inventory deliberately narrow: other non-empty
    files, executable files (even empty ones), links and submodules need
    preservation. A .gitignore and an empty file cannot hold behavior to
    preserve. An ignore rule (.gitignore, .git/info/exclude) keeps code out of
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
    launch_bound = bool(ignored_inputs is not None and getattr(ignored_inputs, "recorded", False)
                        and not ignored_inputs.unverified)
    separate = independent is not False and (launch_bound or (
        bool(dependencies_from) and Path(dependencies_from).resolve() != Path(workspace).resolve()))
    for entry in _git(workspace, "ls-tree", "-r", "-l", "-z", base).split("\0"):
        if not entry:
            continue
        metadata, separator, path = entry.partition("\t")
        fields = metadata.split()  # mode, type, object, size ("-" for a submodule)
        if not separator or len(fields) != 4 or fields[:2] != ["100644", "blob"]:
            return False
        name = PurePosixPath(path)
        if not (path == "README.md"
                or (separate and (name.name == ".gitignore" or name.suffix == ".md" or fields[3] == "0"))):
            return False
    launched = ignored_inputs.generated if ignored_inputs is not None else {}
    return not generated_sources(dependencies_from) and not launched


def _no_go_project(workspace, base, copied) -> bool:
    """True when the pinned base holds no Go project the suite could preserve.

    Every entry must be a regular file. A submodule or link can hide Go source
    the listing does not show, so either one keeps the ordinary base-suite rule.
    ``copied`` is ignored source make_tree puts into the proof trees; a ``.go``
    file there is existing Go behavior even though it is not in the commit.
    """
    paths = []
    for entry in _git(workspace, "ls-tree", "-r", "-l", "-z", base).split("\0"):
        if not entry:
            continue
        metadata, separator, path = entry.partition("\t")
        fields = metadata.split()
        if not separator or len(fields) != 4 or fields[:2] != ["100644", "blob"]:
            return False
        paths.append(path)
    return not first_suite.contains_go_project([*paths, *copied])


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
    return (path.startswith((".autocode/", ".autocode-ui/")) or "/__pycache__/" in f"/{path}"
            or path.endswith(".pyc") or path in DEPENDENCY_DIRS)


def _reachable(workspace, path) -> bool:
    """``path`` exists and is reached through real directories only, so Git can stage what is there."""
    return os.path.lexists(Path(workspace, path)) and not any(
        Path(workspace, parent).is_symlink() for parent in list(PurePosixPath(path).parents)[:-1])


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
            env.update(GIT_OBJECT_DIRECTORY=str(Path(scratch, "objects")),
                       GIT_ALTERNATE_OBJECT_DIRECTORIES=os.pathsep.join(alternates))
            Path(env["GIT_OBJECT_DIRECTORY"]).mkdir()
        real = Path(workspace, _git(workspace, "rev-parse", "--git-path", "index").strip())
        if real.is_file():
            shutil.copy2(real, env["GIT_INDEX_FILE"])  # with its timestamp: a racily clean entry stays racy
        dirty = [path for path in _git(workspace, "-c", "core.fsmonitor=false", "diff-files", "--name-only", "-z",
                                       env=env).split("\0") if path and not _ignored(path)]
        others = [path for path in _git(workspace, "ls-files", "-z", "--others", "--exclude-standard",
                                        env=env).split("\0") if path and not _ignored(path)]
        repositories = [path for path in others if path.endswith("/")]  # Git never stages inside one
        there = {path: _reachable(workspace, path) for path in dirty}
        gone = [path for path in dirty if not there[path]]
        add = [path for path in dirty if there[path]] + [path for path in others if not path.endswith("/")]
        if base:
            ignored = set(_git(workspace, "diff-index", "--cached", "--diff-filter=D", "--name-only", "-z",
                               "--no-renames", base, "--", env=env).split("\0")).difference(add, gone, [""])
            add += [path for path in sorted(ignored) if not _ignored(path) and _reachable(workspace, path)
                    and (Path(workspace, path).is_symlink() or Path(workspace, path).is_file())
                    and not path.startswith(tuple(repositories))]
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
        tokens = _git(workspace, "diff-index", "--cached", "--name-status", "-z", "--no-renames", base, "--",
                      env=env).split("\0")
        changes = {path: {"A": "added", "D": "deleted"}.get(status[:1], "modified")
                   for status, path in zip(tokens[0::2], tokens[1::2]) if path}
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
    return _git(workspace, "-c", "user.name=AutoCode", "-c", "user.email=autocode@localhost", "commit-tree",
                "--no-gpg-sign", tree, "-p", head, "-m", "AutoCode: the checkout as a run started").strip()


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
    return {"files": len(changes), "source_files": len(source), "test_files": len(changes) - len(source),
            "lines_added": sum(a for a, _ in lines.values()), "lines_removed": sum(r for _, r in lines.values()),
            "source_lines_changed": sum(sum(lines.get(p, (0, 0))) for p in source),
            "binary_files": sorted(binary), "non_code_files": sorted(p for p in changes if not is_code_path(p))}


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


class Framework:
    """How to run the whole suite and a targeted subset for one project."""

    def __init__(self, name, suite, *, python=None, runner=None, note="", node_files=(), invocation=None, test_root=None):
        self.name, self.suite, self.python, self.runner, self.note = name, suite, python, runner, note
        self.node_files = frozenset(node_files)
        self.invocation = invocation
        # A scope descriptor, not authority for the first-root exception in verify().
        self.test_root = test_root

    @property
    def per_test(self):
        return self.name in ("pytest", "unittest", "go", "node", "vitest")

    def targeted(self, test_paths):
        files = sorted(test_paths)
        if self.name == "unittest" and "autocode_component_tests.py" in self.suite:
            # Targeting files directly would bypass package load_tests hooks.
            return self.suite if any(PYTHON_TEST_MODULE.match(PurePosixPath(p).name) for p in files) else None
        if self.name == "node":
            if self.invocation:
                scripts = [p for p in files if PurePosixPath(p).suffix in
                           (".js", ".mjs", ".cjs", ".ts", ".mts", ".cts")]
                return self.invocation.targeted(scripts) if scripts else None
            scripts = [p for p in files if p in self.node_files]
            return "node --test " + " ".join(map(shlex.quote, scripts)) if scripts else None
        if self.invocation:
            modules = [p for p in files if PYTHON_TEST_MODULE.match(PurePosixPath(p).name)]
            return self.invocation.targeted(modules) if modules else None
        if self.name == "pytest":
            modules = [p for p in files if PYTHON_TEST_MODULE.match(PurePosixPath(p).name)]
            return (f"{shlex.quote(self.python)} -m pytest -q -p no:cacheprovider "
                    + " ".join(map(shlex.quote, modules))) if modules else None
        if self.name == "unittest":
            modules = [p for p in files if PYTHON_TEST_MODULE.match(PurePosixPath(p).name)]
            return (f"{shlex.quote(self.python)} -m unittest -v "
                    + " ".join(map(shlex.quote, modules))) if modules else None
        if self.name == "go":
            packages = sorted({"./" + str(PurePosixPath(p).parent) if str(PurePosixPath(p).parent) != "." else "."
                               for p in files if p.endswith("_test.go")})
            return ("go test " + " ".join(map(shlex.quote, packages))) if packages else None
        if self.name in ("jest", "vitest", "mocha"):
            scripts = [p for p in files if re.search(r"\.(test|spec)\.[cm]?[jt]sx?$", p) or p.endswith((".js", ".ts"))]
            if not scripts:
                return None
            verb = {"jest": "jest", "vitest": "vitest run", "mocha": "mocha"}[self.name]
            return f"npx --no-install {verb} " + " ".join(map(shlex.quote, scripts))
        if self.name == "rspec":
            specs = [p for p in files if p.endswith("_spec.rb")]
            return (f"{self.runner} " + " ".join(map(shlex.quote, specs))) if specs else None
        return None

    def to_dict(self):
        return {"name": self.name, "suite": self.suite, "python": self.python, "note": self.note}


def detect_framework(root, *, python=None, test_root=None, base=None) -> Framework | None:
    """Detect the project suite, or a caller-scoped Python suite with pinned base policy."""
    root = Path(root)
    # Untracked files count: AutoCode never commits, so in a new project every file the Builder wrote,
    # tests included, is untracked (a live greenfield run found no test command and could not prove its
    # tests, 2026-09-29). Ignored files, such as .autocode/, do not.
    files = [p for p in _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard",
                             check=False).split("\0") if p]
    if test_root is not None:
        return _rooted_python(root, test_roots.normalize(test_root), files, python, base=base)
    names = {PurePosixPath(p).name for p in files}
    has_python = any(p.endswith(".py") for p in files)
    if has_python:
        python = python or python_for(root)
        pyproject, setup_cfg, tox = _read(root / "pyproject.toml"), _read(root / "setup.cfg"), _read(root / "tox.ini")
        requirements = " ".join(_read(root / p) for p in files if re.match(r"(.*/)?requirements.*\.(txt|in)$", p))
        configured = ("pytest.ini" in names or "conftest.py" in names or "[tool.pytest" in pyproject
                      or "[tool:pytest]" in setup_cfg or "[pytest]" in tox or re.search(r"\bpytest\b", requirements + pyproject))
        if configured and _python_can_import(python, "pytest"):
            return Framework("pytest", f"{shlex.quote(python)} -m pytest -q -p no:cacheprovider "
                             "--continue-on-collection-errors", python=python)
        tests = [p for p in files if PYTHON_TEST_MODULE.match(PurePosixPath(p).name)]
        component_packages = [p for p in files if p.startswith("components/") and p.endswith("/__init__.py")
                              and python_tests.unittest_package(_read(root / p))]
        if tests or component_packages:
            note = f"pytest is configured but {python} cannot import it; using unittest" if configured else ""
            if any("/" not in p for p in tests) or (root / "tests" / "__init__.py").is_file() \
                    or (root / "test" / "__init__.py").is_file():
                start = ""
            elif (root / "tests").is_dir():
                start = " -s tests"
            elif (root / "test").is_dir():
                start = " -s test"
            else:
                start = ""
            components = sorted({PurePosixPath(p).parts[1] for p in [*tests, *component_packages]
                                 if len(PurePosixPath(p).parts) > 2 and PurePosixPath(p).parts[0] == "components"})
            if components or component_packages:
                command = shlex.join([python, str(Path(__file__).with_name("autocode_component_tests.py")), "-v",
                                      "--root", start.removeprefix(" -s ") or ".", "--components", *components])
                return Framework("unittest", command, python=python, note=note)
            return Framework("unittest", f"{shlex.quote(python)} -m unittest discover -v{start}", python=python, note=note)
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
            return Framework("node", suite or "node --test " + " ".join(map(shlex.quote, node_files)),
                             node_files=node_files)
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
    pyproject, setup_cfg, tox = (read(directory / "pyproject.toml"), read(directory / "setup.cfg"),
                               read(directory / "tox.ini"))
    requirements = " ".join(read(directory / p) for p in files if re.match(r"(.*/)?requirements.*\.(txt|in)$", p))
    return bool(names.intersection(_PYTEST_NATIVE_CONFIGS) or "conftest.py" in names or "[tool.pytest" in pyproject
                or "[tool:pytest]" in setup_cfg or "[pytest]" in tox
                or re.search(r"\bpytest\b", requirements + pyproject))


def _pinned_policy(workspace, path, entries):
    """Read one Git tree's policy with bounded relative pinned symlink resolution.

    An absent policy is empty; an unsafe or unresolved existing link is not.
    Directory links resolve too, without consulting the mutable checkout or host.
    """
    pending, resolved, links = list(path.parts), [], 0
    seen = set()
    while pending:
        part = pending.pop(0)
        if part == ".":
            continue
        if part == "..":
            if not resolved:
                raise ValueError("Pinned policy escapes the repository")
            resolved.pop()
            continue
        name = "/".join([*resolved, part])
        entry = entries.get(name)
        if entry is None:
            if links:
                raise ValueError("Pinned policy link target is absent from the base")
            return ""
        mode, kind, object_id = entry
        if mode == "120000" and kind == "blob":
            step = (name, tuple(pending))
            if step in seen:
                raise ValueError("Pinned policy link contains a loop")
            seen.add(step)
            links += 1
            target = _git(workspace, "cat-file", "blob", object_id)
            if links > 32 or not target or target.startswith("/") or "\0" in target:
                raise ValueError("Pinned policy link is external or exceeds the resolution limit")
            pending = [part for part in target.split("/") if part] + (["."] if target.endswith("/") else []) + pending
        elif mode == "040000" and kind == "tree":
            resolved.append(part)
        elif mode in ("100644", "100755") and kind == "blob" and not pending:
            return _git(workspace, "cat-file", "blob", object_id)
        else:
            raise ValueError("Pinned policy is not a regular file")
    raise ValueError("Pinned policy resolves to a directory")


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
        policies.append((base_files, lambda path: _pinned_policy(workspace, path.relative_to(workspace), entries)))
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
            except (OSError, ValueError, RuntimeError):
                return None  # Unknown policy cannot authorize a smaller or first suite.
    files = test_roots.inside(test_root, files)
    if not any(p.endswith(".py") for p in files):
        return None
    python = python or python_for(workspace)
    if configured:
        return Framework("pytest", f"{shlex.quote(python)} -m pytest -q -p no:cacheprovider "
                         f"--continue-on-collection-errors {shlex.quote(test_root)}", python=python, test_root=test_root)
    tests = [p for p in files if PYTHON_TEST_MODULE.match(PurePosixPath(p).name)]
    if not tests:
        return None
    package = ((directory / "__init__.py").is_file() or f"{test_root}/__init__.py" in base_files)
    # Explicit top level visits the start package's load_tests and preserves relative
    # imports even when components/ is a namespace rather than a regular package.
    start = test_root if package else test_roots.unittest_start(test_root, directory, tests)
    top = " -t ." if package else ""
    return Framework("unittest", f"{shlex.quote(python)} -m unittest discover -v -s {shlex.quote(start)}{top}",
                     python=python, test_root=test_root)


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
    return environment


def run_command(command, cwd, log_path, *, timeout=DEFAULT_TIMEOUT, env=None, checkpoint=None) -> dict:
    """Run one owned shell command and return its collected execution receipt."""
    return command_supervision.run(command, cwd, log_path, timeout=timeout,
                                   env=test_environment(cwd, env), checkpoint=checkpoint)


def _go_test(command):
    """A plain `go test ...` command the runner can ask for per-test JSON events (not a shell pipeline)."""
    return command.startswith("go test ") and not re.search(r"[;&|<>`$()]", command)


def _with_results(framework, command, xml_path, tree=None):
    if node_tests.command_words(command):
        return node_tests.instrument(command, xml_path)
    if vitest_tests.command_words(command, tree):
        return vitest_tests.instrument(command, xml_path, tree)
    if framework and framework.name == "pytest" and " -m pytest" in command:
        return f"{command} --junitxml={shlex.quote(str(xml_path))}"
    if framework and framework.name == "go" and _go_test(command) and " -json" not in command:
        # Go reports per-test results only as `go test -json` events (a live Go port could not be
        # proven without them, 2026-09-29). The flag goes before the packages, where go test reads it.
        return "go test -json " + command[len("go test "):]
    # Unittest names each test only at -v. A quiet or default command still runs
    # the same tests; the proof needs those names to tell a passing baseline
    # from a count with no identities.
    return python_tests.verbose_unittest(command)


def expects_results(framework, command, tree=None):
    """True when this command, run by the runner, must yield per-test results."""
    if node_tests.command_words(command):
        return True
    if vitest_tests.command_words(command, tree):
        return True
    if not framework or not framework.per_test or not command:
        return False
    if framework.name == "go":
        return _go_test(command)
    return (" -m pytest" in command) if framework.name == "pytest" else (
        (" -m unittest" in command or "autocode_component_tests.py" in command) and " -v" in command)


def _go_results(text):
    """Per-test results from `go test -json` events (one JSON object per line; other lines, such as
    compiler errors, are ignored). A test is ``package::Name`` (a subtest ``package::Name/sub``). A
    package that fails without running any test (it did not build, so its tests could not run, like
    a Python module that fails to import) is a collection error named ``package::[build failed]``.
    None when there are no events at all."""
    outcome, ran_in, package_fail, seen = {}, set(), set(), False
    for line in text.splitlines():
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or not event.get("Action"):
            continue
        seen = True
        package, test, action = event.get("Package") or "", event.get("Test"), event["Action"]
        if test:
            key = f"{package}::{test}"
            if action == "run":
                outcome.setdefault(key, None)
                ran_in.add(package)
            elif action in ("pass", "fail", "skip"):
                outcome[key] = action
                ran_in.add(package)
        elif action == "fail":
            package_fail.add(package)
    if not seen:
        return None
    passed = {key for key, action in outcome.items() if action == "pass"}
    skipped = {key for key, action in outcome.items() if action == "skip"}
    failed = {key for key, action in outcome.items() if action == "fail"}
    collection = {f"{package}::[build failed]" for package in package_fail if package not in ran_in}
    failed |= collection
    # A test that started but never ended (the binary panicked or timed out) is not attributed.
    complete = all(action is not None for action in outcome.values())
    return {"passed": sorted(passed), "failed": sorted(failed), "skipped": sorted(skipped),
            "collection_errors": sorted(collection), "uncollected": sorted(collection),
            "total": len(outcome) + len(collection), "complete": complete}


def _unittest_id(name, owner):
    return owner if owner.endswith("." + name) else f"{owner}::{name}"


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
        for case in result_tree.iter("testcase"):
            test = f"{case.get('classname', '')}::{case.get('name', '')}"
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
        ran = re.findall(r"^Ran (\d+) tests? in ", text, re.M)
        if not ran:
            return None
        total = sum(map(int, ran)) if "autocode_component_tests.py" in framework.suite else int(ran[-1])
        current = None
        for line in text.splitlines():
            if line.startswith(("=====", "-----")):
                current = None  # the failure details section follows
                continue
            match = UNITTEST_HEADER.match(line)
            if match:
                current, rest = _unittest_id(*match.groups()), line[match.end():]
            elif current is None:
                continue
            else:
                rest = line
            # "name (id) ... ok", a docstring line ending "... ok", or the word alone after test output
            found = UNITTEST_STATUS.search(rest)
            word = found.group(1) if found else None
            if word is None and not match:
                bare = UNITTEST_BARE_STATUS.fullmatch(rest.strip())
                word = bare and ("skipped" if bare.group(0).startswith("skipped") else bare.group(1))
            if word:
                (passed if word == "ok" else skipped if word in ("skipped", "expected failure") else failed).add(current)
                current = None
        for name, owner, detail in test_setup.failure_details(text):
            test = _unittest_id(name, owner)
            failed.add(test)
            reason = test_setup.setup_error(detail, tree, is_test_path)
            if reason:
                setup_errors[test] = reason
        passed -= failed
        # "setUpClass (m.C) ... skipped" or ERROR (also setUpModule, tearDown*) is outside "Ran N": a
        # skipped fixture names no test (its tests are absent from N); an error is one more failure, but
        # never a test: "Ran 0" with only fixture errors stays zero tests, so it proves nothing.
        fixtures = {test for test in skipped | failed if test.rpartition("::")[2] in UNITTEST_FIXTURES}
        skipped -= fixtures
        total += len(fixtures & failed) if total else 0
        collection = {test for test in failed if COLLECTION_ERROR.search(test)}
        complete = len(passed) + len(skipped) + len(failed) >= total
    results = {"passed": sorted(passed), "failed": sorted(failed), "skipped": sorted(skipped),
               "collection_errors": sorted(collection), "uncollected": sorted(collection),
               "total": total, "complete": complete}
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
    source = source_root / 'vendor'
    if source.is_symlink():
        raise ValueError('Vendored dependencies must not use a symlinked root')
    if not source.is_dir():
        return []
    included = set()
    for name in filter(None, _git(source_root, 'ls-files', '-z', '--others', '--ignored', '--exclude-standard',
                                  '--', 'vendor').split('\0')):
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
    target = Path(tree) / 'vendor'
    if (source_root / 'vendor').is_symlink() or target.is_symlink():
        raise ValueError('Vendored dependencies must not use a symlinked root')
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
    ignored = _git(source_root, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory",
                   check=False).split("\0")
    tracked_dirs = {str(PurePosixPath(p).parent) for p in _git(source_root, "ls-files", "-z", check=False).split("\0")
                    if p}
    selected = []
    for relative in ignored:
        path = PurePosixPath(relative)
        if (not relative or relative.endswith("/") or path.suffix not in CODE_SUFFIXES
                or str(path.parent) not in tracked_dirs or any(part in DEPENDENCY_DIRS for part in path.parts)):
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
        notes.append("Ignored generated sources were left out of the proof because this run has no record "
                     "of them from when it started: " + ", ".join(added))
    elif added:
        notes.append("Ignored generated sources added during the run were left out of the proof: "
                     + ", ".join(added))
    if changed:
        notes.append("Ignored generated sources changed during the run were left out of the proof: "
                     + ", ".join(changed))
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


def make_tree(repo, base, destination, overlay_root, changes, *, dependencies_from=None, patch=None,
              generated_record=None, generated_unrecorded=False, ignored_inputs=None):
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
            applied = subprocess.run(["git", "-C", str(destination), "apply", str(patch)],
                                     capture_output=True, text=True)
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
            copy_generated_sources(dependencies_from, destination, record=generated_record,
                                   unrecorded=generated_unrecorded)
        else:
            ignored_inputs.copy_into(destination)
    except BaseException:
        remove_tree(repo, destination)
        raise
    return destination


def patch_applies(repo, base, patch) -> str:
    """"" when ``patch`` applies cleanly to ``base``, else why not. Uses a scratch index, no worktree."""
    with tempfile.TemporaryDirectory() as scratch:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(scratch) / "index")}
        read = subprocess.run(["git", "-C", str(repo), "read-tree", base], capture_output=True, text=True, env=env)
        check = read if read.returncode else subprocess.run(
            ["git", "-C", str(repo), "apply", "--check", "--cached", str(patch)], capture_output=True, text=True,
            env=env)
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

_MANIFEST_BASENAMES = frozenset({"package.json", "package-lock.json", "npm-shrinkwrap.json",
                                 "yarn.lock", "pnpm-lock.yaml", "pnpm-workspace.yaml", ".npmrc"})
# Runner configs the tool reads from the working directory without the suite command naming
# them. A candidate can otherwise narrow a spec list here and the definition run never sees it (#662).
_RUNNER_CONFIG_BASENAMES = frozenset({
    ".mocharc", ".mocharc.js", ".mocharc.cjs", ".mocharc.mjs", ".mocharc.json", ".mocharc.jsonc",
    ".mocharc.yml", ".mocharc.yaml", "mocha.opts",
    "jest.config.js", "jest.config.cjs", "jest.config.mjs", "jest.config.json", "jest.config.ts",
    "vitest.config.js", "vitest.config.cjs", "vitest.config.mjs", "vitest.config.ts", "vitest.config.mts",
})
DEFINITION_FILE_BASENAMES = _MANIFEST_BASENAMES | _RUNNER_CONFIG_BASENAMES
_JS_MODULE_CALL = re.compile(r"\b(?:require|import)\s*\(")
_JS_EXEC_CALL = re.compile(r"\b(?:exec(?:Sync|File(?:Sync)?)?|spawn(?:Sync)?|fork)\s*\(")
_JS_READ_CALL = re.compile(r"\b(?:readFileSync|readFile|createReadStream)\s*\(")
_SHELL_INTERPRETERS = frozenset({"sh", "bash", "dash", "zsh"})


def _definition_file(path: str) -> bool:
    """A suite-definition filename wherever it appears: manifests, lockfiles, .npmrc, runner config."""
    return PurePosixPath(path).name in DEFINITION_FILE_BASENAMES


def _js_scan(text):
    """(mask, strings): ``mask`` blanks comments and quoted literals (same length), and
    ``strings`` lists each quoted literal as (start, end, value), so call structure and
    literal arguments can be read without executing anything."""
    mask, strings, i, n = list(text), [], 0, len(text)
    while i < n:
        char = text[i]
        if char == "/" and text[i + 1:i + 2] in ("//",):
            end = text.find("\n", i)
            end = n if end < 0 else end
        elif char == "/" and text[i + 1:i + 2] == "/*":
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
        elif char in "\"'":
            end, value = i + 1, []
            while end < n and text[end] != char:
                if text[end] == "\\" and end + 1 < n:
                    value.append(text[end + 1])
                    end += 2
                else:
                    value.append(text[end])
                    end += 1
            strings.append((i, min(end, n - 1) + 1, "".join(value)))
            for index in range(i, min(end + 1, n)):
                mask[index] = " "
            i = min(end + 1, n)
            continue
        else:
            i += 1
            continue
        for index in range(i, end):
            mask[index] = " "
        i = end
    return "".join(mask), strings


def _argument_span(mask, open_paren):
    """(start, end) covering a call's parentheses; literals and comments never nest."""
    depth = 0
    for index in range(open_paren, len(mask)):
        if mask[index] == "(":
            depth += 1
        elif mask[index] == ")":
            depth -= 1
            if depth == 0:
                return open_paren, index
    return open_paren, len(mask)


def _import_sites(text):
    """Every import/require site of JS source as (specifier, inside_exec_argument).

    ``specifier`` is the argument when it is one quoted literal or a fold of literals
    joined by ``+``, and None when it is computed at runtime: a fail-closed boundary
    (#587). Static import and export-from specifiers are literals by grammar.
    """
    mask, strings = _js_scan(text)
    by_start = {start: (end, value) for start, end, value in strings}
    exec_spans = [_argument_span(mask, match.end() - 1) for match in _JS_EXEC_CALL.finditer(mask)]
    sites = []
    for match in _JS_MODULE_CALL.finditer(mask):
        open_paren = match.end() - 1
        start, end = _argument_span(mask, open_paren)
        inner = [literal for literal in strings if start < literal[0] < end]
        covered = {index for literal in inner for index in range(literal[0], literal[1])}
        residue = "".join(text[index] for index in range(start + 1, end)
                          if index not in covered and not text[index].isspace() and mask[index] == text[index])
        specifier = "".join(literal[2] for literal in inner) if not residue.strip("+") else None
        sites.append((specifier, any(a <= open_paren < b for a, b in exec_spans)))
    for pattern in (r"\bfrom\s*(['\"])", r"(?m)^\s*import\s*(['\"])"):
        for match in re.finditer(pattern, text):
            quote = match.start(1)
            if quote in by_start:  # a real literal, not text inside a comment
                sites.append((by_start[quote][1], False))
    return sites


def _shell_script(path, text):
    """True when ``path`` is a shell script the suite closure should read as a command line.

    A ``.sh`` / ``.bash`` file, or a shebang for sh, bash, dash or zsh. ``#!/usr/bin/env node``
    is not a shell script.
    """
    if PurePosixPath(path).suffix.lower() in {".sh", ".bash"}:
        return True
    line = text.lstrip("\ufeff").splitlines()[:1]
    if not line or not line[0].startswith("#!"):
        return False
    tokens = line[0][2:].split()
    interpreter = PurePosixPath(tokens[-1]).name if tokens else ""
    if interpreter in _SHELL_INTERPRETERS:
        return True
    return interpreter == "env" and any(token in _SHELL_INTERPRETERS for token in tokens)


def _first_argument_end(mask, open_paren, close):
    """Index of the first-argument comma at parenthesis depth 1, else ``close``."""
    depth = 0
    for index in range(open_paren, close + 1):
        if mask[index] == "(":
            depth += 1
        elif mask[index] == ")":
            depth -= 1
        elif mask[index] == "," and depth == 1:
            return index
    return close


def _static_path(literals, residue):
    """``("cwd"|"file", path)`` when the argument is a static path, else None.

    A quoted literal (or ``+`` fold of literals) is relative to the process cwd, which is how
    ``fs.readFileSync`` resolves it. ``path.join(__dirname, ...)`` of literals is relative to
    the file. A computed path (``path.join(__dirname, name)``, a variable) is None.
    """
    compact = "".join(char for char in residue if not char.isspace())
    if not compact.strip("+"):
        folded = "".join(literals)
        return ("cwd", folded) if folded else None
    if literals and re.fullmatch(r"(?:path\.)?join\(__dirname(?:,)+\)", compact):
        return ("file", posixpath.join(*literals))
    return None


def _specifier_between(text, mask, strings, start, end):
    inner = [literal for literal in strings if start < literal[0] < end]
    covered = {index for literal in inner for index in range(literal[0], literal[1])}
    residue = "".join(text[index] for index in range(start + 1, end)
                      if index not in covered and not text[index].isspace() and mask[index] == text[index])
    return _static_path([literal[2] for literal in inner], residue)


def _read_paths(text):
    """Literal paths a JS file passes to ``readFile``, ``readFileSync`` or ``createReadStream``.

    Computed arguments are omitted. Pinning the files that are named is what stops a candidate
    from narrowing a suite through a data file (#662); a path the scan cannot see stays unnamed.
    """
    mask, strings = _js_scan(text)
    paths = []
    for match in _JS_READ_CALL.finditer(mask):
        open_paren = match.end() - 1
        _start, close = _argument_span(mask, open_paren)
        end = _first_argument_end(mask, open_paren, close)
        specifier = _specifier_between(text, mask, strings, open_paren, end)
        if specifier:
            paths.append(specifier)
    return paths


def _exec_command_literals(text):
    """Quoted literals inside ``exec`` / ``spawn`` / ``fork`` argument lists.

    Each literal is something the runner may execute (``sh test/run.sh``, ``node list.js``) or an
    option word (``inherit``). The caller keeps the ones that name tracked files. A computed
    argument contributes only the literals it still contains, so a command built by concatenation
    does not by itself make the boundary unestablished.
    """
    mask, strings = _js_scan(text)
    literals = []
    for match in _JS_EXEC_CALL.finditer(mask):
        start, end = _argument_span(mask, match.end() - 1)
        literals.extend(literal[2] for literal in strings if start < literal[0] < end)
    return literals


@contextlib.contextmanager
def _effective_base(workspace, base, patch=None):
    """(blobs, read) for the tracked files of ``base`` with ``patch`` applied.

    This is the tree the baseline executed — the base revision with the review's
    base_patch, never the raw base alone — so a runner or selector the patch
    introduces is part of the definition closure. The index and object store live
    in a scratch directory that vanishes afterwards; ``read`` is valid only inside
    the context. Tracked files only, never node_modules or other ignored trees.
    """
    workspace = Path(workspace)
    with tempfile.TemporaryDirectory(prefix="autocode-base-definition-") as scratch:
        objects = Path(workspace, _git(workspace, "rev-parse", "--git-path", "objects").strip()).resolve()
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(scratch, "index")),
               "GIT_OBJECT_DIRECTORY": str(Path(scratch, "objects")),
               "GIT_ALTERNATE_OBJECT_DIRECTORIES": os.pathsep.join(
                   filter(None, (str(objects), os.environ.get("GIT_ALTERNATE_OBJECT_DIRECTORIES"))))}
        Path(scratch, "objects").mkdir()
        if subprocess.run(["git", "-C", str(workspace), "read-tree", str(base)],
                          capture_output=True, env=env).returncode:
            raise RuntimeError(f"git read-tree {base} failed")
        if patch and subprocess.run(["git", "-C", str(workspace), "apply", "--cached", str(patch)],
                                    capture_output=True, text=True, env=env).returncode:
            raise ValueError(f"git apply --cached {patch} failed")
        blobs = {}
        listing = subprocess.run(["git", "-C", str(workspace), "ls-files", "-s", "-z"],
                                 capture_output=True, env=env).stdout.decode("utf-8", "replace")
        for entry in listing.split("\0"):
            fields = entry.split("\t", 1)
            metadata = fields[0].split()  # <mode> <object> <stage>
            if len(fields) == 2 and len(metadata) == 3:
                blobs[fields[1]] = metadata[1]

        def read(path):
            return subprocess.run(["git", "-C", str(workspace), "cat-file", "blob", blobs[path]],
                                  capture_output=True, env=env).stdout.decode("utf-8", "replace")

        yield blobs, read


def _base_suite_definition(workspace, base, suite_command, *, base_patch=None):
    """(pinned paths, unestablished reason) for the base suite definition (#587, #662).

    Pinned by rule: manifests, lockfiles, pnpm-workspace.yaml, .npmrc and runner-config
    basenames (mocha, jest, vitest) at any depth; every path is_test_path classifies as a
    test; and the transitive closure of path literals reachable from the manifests' script
    tokens, from suite-command tokens, from shell scripts those commands run, from literal
    commands passed to exec/spawn/fork, and from literal fs reads (a quoted path or
    ``path.join(__dirname, ...)``). Files base tests import through literals are product
    code and never pinned, so the candidate's version enters the definition tree; a
    non-test reach is pinned only when its literals flow into the executed test command;
    any other import, or a computed import path, leaves the boundary unestablished.
    """
    with _effective_base(workspace, base, base_patch) as (blobs, read):
        tracked = set(blobs)
        tests = {path for path in tracked if is_test_path(path)}
        definition = {path for path in tracked if _definition_file(path)} | tests

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
                    if (len(segment) != 2 or not target or target.startswith(("-", "/", "~"))
                            or any(mark in target for mark in ("$", "`", "*", "?"))):
                        return "a cd target is computed, so the suite definition boundary is unestablished"
                    landed = posixpath.normpath(posixpath.join(current, target))
                    inside = (landed in ("", ".") or (
                        not landed.startswith("../") and any(
                            path == landed or path.startswith(landed + "/") for path in tracked)))
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
                if (posixpath.basename(word) in {"sh", "bash", "dash", "zsh"}
                        and index + 2 < len(parsed) and parsed[index + 1] == "-c"):
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
                    index += 1
                    continue
                if word in {"||", "|", "&"}:
                    reason = finish(False)
                    if reason:
                        return [], reason
                    index += 1
                    continue
                segment.append(word)
                index += 1
            reason = finish(False)
            if reason:
                return [], reason
            return bindings, ""

        def seed_command(command, directory):
            bindings, reason = shell_bindings(command, directory)
            if reason:
                return reason
            for word, effective in bindings:
                candidate = posixpath.normpath(posixpath.join(effective, word))
                if not candidate.startswith("../") and candidate in tracked and candidate not in product:
                    seeds.add(candidate)
                    cwd_of.setdefault(candidate, effective)
            return ""

        seeds, cwd_of, boundary_reason = set(), {}, ""
        for path in sorted(tracked):
            if PurePosixPath(path).name != "package.json":
                continue
            for script in _package_scripts(read(path)).values():
                boundary_reason = seed_command(str(script), posixpath.dirname(path) or ".")
                if boundary_reason:
                    return set(), boundary_reason
        boundary_reason = seed_command(suite_command or "", ".")
        if boundary_reason:
            return set(), boundary_reason
        pinned, queue, queued = set(), [], set()

        def consider(candidate, directory):
            """Queue a tracked file the suite command reaches. Tests are scanned only when they
            are shell scripts; product code and manifests are never queued from here."""
            if (not candidate or candidate.startswith("../") or candidate not in tracked
                    or candidate in product or _definition_file(candidate) or candidate in queued):
                return
            cwd_of.setdefault(candidate, directory)
            queued.add(candidate)
            if not is_test_path(candidate):
                pinned.add(candidate)
            queue.append(candidate)

        for path in sorted(seeds):
            consider(path, cwd_of.get(path, "."))

        def follow_shell(command, directory):
            bindings, reason = shell_bindings(command, directory)
            if reason:
                return reason
            for word, effective in bindings:
                consider(posixpath.normpath(posixpath.join(effective, word)), effective)
            return ""

        unestablished = ""
        scanned = set()
        while queue and not unestablished:
            path = queue.pop()
            if path in scanned:
                continue
            scanned.add(path)
            text = read(path)
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
                    unestablished = (f"{path} reaches {target}, which is neither a test, a manifest "
                                     "nor an input of the executed test command")
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
        if unestablished:
            return set(), unestablished
        return definition | pinned, ""


# --- verification -----------------------------------------------------------

def _mentions_tests(command, test_paths):
    """True when a shell word of ``command`` names a changed test file (or a pytest node in it)."""
    try:
        words = shlex.split(command, comments=True)
    except ValueError:
        words = command.split()
    for path in test_paths:
        module = str(PurePosixPath(path).with_suffix("")).replace("/", ".")
        for word in words:
            if word in (path, "./" + path, module) or word.startswith((path + "::", module + ".")):
                return True
    return False


def select_commands(framework, test_paths, *, suite_command=None, regression_command=None, reported=None):
    """Choose commands: explicit flags, then detection, then the builder's report.

    A builder-reported command is used only to give the Builder feedback: it
    can never make the verdict PASS, because the candidate would be judged by a
    check it chose. A builder regression command must also name a changed test.
    """
    reported = reported or {}
    notes = []
    suite, suite_source = suite_command, "explicit" if suite_command else None
    if not suite and framework:
        suite, suite_source = framework.suite, f"detected:{framework.name}"
    if not suite and str(reported.get("test_command", "")).strip():
        suite, suite_source = reported["test_command"].strip(), "builder"
    regression, regression_source = regression_command, "explicit" if regression_command else None
    if not regression and framework:
        derived = framework.targeted(test_paths)
        if derived:
            regression, regression_source = derived, f"derived:{framework.name}"
    builder_regression = str(reported.get("regression_command", "")).strip()
    if not regression and builder_regression:
        if _mentions_tests(builder_regression, test_paths):
            regression, regression_source = builder_regression, "builder"
        else:
            notes.append("Ignored the builder's regression command because it does not name a changed test file")
    return {"suite": suite, "suite_source": suite_source, "regression": regression,
            "regression_source": regression_source, "notes": notes}


def run_suite(framework, command, tree, evidence_dir, label, *, timeout):
    extension = ("node.jsonl" if node_tests.command_words(command) else
                 "vitest.json" if vitest_tests.command_words(command, tree) else "junit.xml")
    xml = Path(evidence_dir) / f"{label}.{extension}"
    xml.unlink(missing_ok=True)  # never parse a previous run's results
    receipt = run_command(_with_results(framework, command, xml, tree), tree, Path(evidence_dir) / f"{label}.log",
                          timeout=timeout)
    receipt["results"] = per_test_results(framework, receipt, xml, tree=tree)
    receipt["results_expected"] = expects_results(framework, command, tree)
    return receipt


def command_framework(command):
    """Recognize a plain runner invocation, never infer coverage from similar text."""
    invocation = python_tests.parse(command)
    if invocation:
        return Framework(invocation.kind, command, python=invocation.python, invocation=invocation)
    invocation = node_tests.parse(command)
    if invocation:
        return Framework("node", command, invocation=invocation)
    if _go_test(command):
        return Framework("go", command)
    return None


def execution_identity(workspace, *, command=None, dependencies_from=None, full=True, source_paths=(),
                       generated_record=None, generated_unrecorded=False, ignored_inputs=None):
    """Conservative observable source/runtime/environment identity for receipts.

    Full dependency bytes are included, not only manifests or changed paths.
    Unknown runtimes cannot reuse clean replay evidence. This does not attest a
    remote service, wall clock or provider sandbox; those remain fresh checks.
    ``ignored_inputs`` is make_tree's: what it supplies is bound in place of the
    generated sources and ``vendor`` tree of ``dependencies_from``.
    """
    workspace = Path(workspace)
    source_snapshot_value = source_snapshot.snapshot(workspace, paths=source_paths)
    source_symlinks = [name for name, value in source_snapshot_value.get("files", {}).items()
                       if value.startswith("symlink:") and name not in DEPENDENCY_DIRS]
    source_metadata = {}
    for name in source_snapshot_value.get("files", {}):
        path = workspace / name
        if path.is_file() and not path.is_symlink():
            stat = path.stat()
            source_metadata[name] = [stat.st_mode, stat.st_mtime_ns, stat.st_uid, stat.st_gid]
    environment = test_environment(workspace)
    invocation = python_tests.parse(command)
    if invocation and invocation.prefix:
        environment.update(word.split('=', 1) for word in invocation.prefix[1:])
    relative_pythonpath = [p for p in environment.get("PYTHONPATH", "").split(os.pathsep)
                           if p and not Path(p).is_absolute()
                           and not (workspace / p).resolve().is_relative_to(workspace.resolve())]
    try:
        words = shlex.split(command or "")
    except ValueError:
        words = []
    python_command = bool(words and re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", Path(words[0]).name)
                          and not re.search(r"[;&|<>`$\n]", command))
    python = (invocation.python if invocation else words[0] if python_command
              else python_for(dependencies_from or workspace))
    node_invocation = node_tests.parse(command)
    runtime = node_invocation.node if node_invocation else python
    search_path = environment.get("PATH", "")
    if node_invocation:
        command_root = workspace.resolve()
        if os.path.dirname(runtime) and not Path(runtime).is_absolute():
            runtime = str(command_root / runtime)
        # The shell executes in the proof workspace. Relative and empty PATH
        # entries must select its Node, never a binary in the controller cwd.
        search_path = os.pathsep.join(str(Path(entry) if Path(entry).is_absolute() else command_root / entry)
                                      for entry in search_path.split(os.pathsep))
    executable = shutil.which(runtime, path=search_path)
    venv_config = Path(executable).parent.parent / "pyvenv.cfg" if executable else None
    try:
        isolated_runtime = venv_config is not None and venv_config.is_file() and not re.search(
            r"^\s*include-system-site-packages\s*=\s*true\s*$", venv_config.read_text(), re.I | re.M)
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
        probe = subprocess.run([executable, "-I", "-B", "-c", "import json,sys; print(json.dumps(sys.path))"],
                               cwd=workspace, env=environment, capture_output=True, text=True, timeout=30)
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
                if (url.scheme != "file" or url.netloc not in ("", "localhost") or
                        target not in (workspace.resolve(), Path(__file__).resolve().parent.parent)):
                    unbound_editables.append(str(metadata))
                    continue
                if target != workspace.resolve():
                    # The install exposes only the package directory (pyproject maps autocode_cli to tools/). The
                    # rest of the checkout (tests, docs, run output) is not importable through it, and binding it
                    # let a file another process wrote there change the identity mid-proof (#665).
                    package = Path(__file__).resolve().parent.relative_to(target).as_posix()
                    if (target / ".git").exists():
                        editable_sources[str(target)] = util.digest(
                            {name: value for name, value in util.snapshot(target)["files"].items()
                             if name.startswith(package + "/")})
                    else:
                        editable_sources[str(target)] = schedule.tree_identity(target / package, excluded={
                            ".git", ".autocode", ".autocode-ui", ".scenario-runs", ".venv", "venv",
                            "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".DS_Store"})
            except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.CalledProcessError):
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
    runtime = {str(p.relative_to(runtime_root)): util.file_hash(p)
               for pattern in ("*.py", "*.json") for p in runtime_root.rglob(pattern)
               if not _ignored(str(p.relative_to(runtime_root))) and "node_modules" not in p.parts}
    generated = {p: schedule.tree_identity(workspace / p) for p in
                 _git(workspace, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory").split("\0")
                 if p and not p.endswith("/") and PurePosixPath(p).suffix in CODE_SUFFIXES
                 and not _ignored(p) and not any(part in DEPENDENCY_DIRS for part in PurePosixPath(p).parts)}
    if ignored_inputs is None:
        trusted, _notes, omitted_generated = classify_generated_sources(
            dependencies_from or workspace, generated_record, unrecorded=generated_unrecorded)
        copied_inputs = {p: schedule.tree_identity(Path(dependencies_from or workspace) / p) for p in trusted}
    else:
        copied_inputs, omitted_generated = ignored_inputs.identity, None
    identity = {"source_revision": source_snapshot_value["revision"], "source_metadata": util.digest(source_metadata),
            "unbound_source_symlinks": source_symlinks,
            "reuse_supported": python_command and full, "cache_binding_complete": full,
            "cache_policy": "isolated_python_full_contents" if full else "fresh_execution_only",
            "environment_hash": util.digest(environment), "runtime_sources": util.digest(runtime),
            "interpreter": schedule.tree_identity(executable) if executable else None,
            "shell": schedule.tree_identity("/bin/sh"),
            "dependencies": [schedule.tree_identity(p) for p in sorted(roots)] if full else None,
            "editable_sources": editable_sources, "unbound_editables": unbound_editables,
            "unbound_relative_pythonpath": relative_pythonpath,
            "generated_sources": generated,
            # make_tree receives these from the dependency checkout, which may
            # differ from the candidate. They also affect the base suite: keep
            # this binding when baseline_identity drops candidate source fields.
            "generated_dependency_sources": copied_inputs,
            "platform": [sys.platform, os.uname().release, os.uname().machine]}
    if omitted_generated is not None:
        # The optional filter changes receipt identity even when it omits no files.
        identity["omitted_generated_sources"] = omitted_generated
    return identity


# Candidate-tree fields: they change with every Builder edit and therefore must
# not key a cache of the base suite, which always runs on the base commit (#426).
_SOURCE_IDENTITY_KEYS = ("source_revision", "source_metadata", "generated_sources", "unbound_source_symlinks")


def baseline_identity(workspace, *, command=None, dependencies_from=None,
                      generated_record=None, generated_unrecorded=False, ignored_inputs=None):
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
    identity = execution_identity(workspace, command=command, dependencies_from=dependencies_from,
                                  generated_record=generated_record, generated_unrecorded=generated_unrecorded,
                                  ignored_inputs=ignored_inputs)
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
    node_command = node_tests.parse(command) is not None
    bound["cache_policy"] = "fresh_execution_only" if node_command else "baseline_runtime_identity"
    bound["cache_binding_complete"] = not (node_command or identity.get("unbound_editables")
                                           or identity.get("unbound_relative_pythonpath"))
    bound["reuse_supported"] = bool(bound["cache_binding_complete"]
                                    and (bound["dependencies"] is not None or not any(
                                        (root / name).exists() for name in DEPENDENCY_DIRS for root in roots)))
    return bound


def scratch_run(workspace, run_dir, *, patch=None, tests=(), command=None, timeout=DEFAULT_TIMEOUT,
                files=None, links=None, source_paths=(), dependencies_from=None,
                generated_record=None, generated_unrecorded=False, ignored_inputs=None) -> dict:
    """Run tests or one command in a scratch copy of the workspace as it is now, never in the workspace.

    The copy is HEAD plus every uncommitted change (so files a stage just delivered are there),
    with ``patch`` (a path in the repository) applied on top when given: a review's change under
    review. ``tests`` runs those test files with the project's framework and returns per-test
    results; ``command`` runs a shell command (a discussion's probe). Returns the receipt with
    ``results`` (or None) and ``error`` (why nothing could be run, else "").
    ``files`` maps a path inside the tree to a file outside it that is copied in first (a stuck
    investigation's cited run files, under ``run/``), so a probe sees exactly what was cited and
    never the real run directory. ``links`` restores original relative test links, with
    every target also supplied in the overlay; candidate links are never written through.
    """
    workspace, run_dir = Path(workspace), Path(run_dir)
    head = _git(workspace, "rev-parse", "HEAD").strip()
    tree = make_tree(workspace, head, run_dir / "scratch" / "tree", workspace, changed_files(workspace, head, source_paths=source_paths),
                     dependencies_from=dependencies_from or workspace, generated_record=generated_record,
                     generated_unrecorded=generated_unrecorded, ignored_inputs=ignored_inputs)
    try:
        if patch:
            applied = subprocess.run(["git", "-C", str(tree), "apply", str(patch)], capture_output=True, text=True)
            if applied.returncode:
                return {"error": f"git apply {patch} failed: {(applied.stderr or applied.stdout).strip()[-300:]}",
                        "results": None}
        scratch_overlay.apply(tree, files, links)
        if command is None:
            python = python_for(workspace)
            framework = detect_framework(tree, python=python)
            if framework is None and all(str(test).endswith(".py") for test in tests):
                # A project with no suite of its own: standard unittest files still run.
                framework = Framework("unittest", f"{shlex.quote(python)} -m unittest discover -v", python=python)
            command = framework.targeted(list(tests)) if framework else None
            if not command:
                return {"error": "no test command runs these files: " + ", ".join(tests), "results": None}
            receipt = run_suite(framework, command, tree, run_dir, "scratch-tests", timeout=timeout)
        else:
            # A command naming the workspace's absolute path runs against the copy, never the workspace.
            for root in dict.fromkeys((str(workspace.resolve()), str(workspace))):
                command = command.replace(root, str(tree))
            framework = command_framework(command)
            if framework:
                receipt = run_suite(framework, command, tree, run_dir, "scratch-command", timeout=timeout)
                if (receipt["exit_code"] == 0 and not receipt["timed_out"]
                        and not schedule.complete_results(receipt)):
                    return {**receipt, "error": "Test command reported zero tests or incomplete per-test results"}
            else:
                receipt = run_command(command, tree, run_dir / "scratch-command.log", timeout=timeout)
                receipt["results"] = None
                try:
                    words = shlex.split(command)
                except ValueError:
                    words = []
                if (words[1:3] == ["-m", "unittest"] and receipt["exit_code"] == 0
                        and not re.search(r"[;&|<>`$\n]", command)):
                    text = Path(receipt["output"]).read_text(errors="replace")
                    ran = re.findall(r"^Ran (\d+) tests? in ", text, re.M)
                    if not ran or int(ran[-1]) == 0 or not re.search(r"^OK(?:\s|$)", text, re.M):
                        return {**receipt, "error": "Test command reported zero tests or incomplete output"}
        return {**receipt, "error": receipt.get("error") or ""}
    finally:
        remove_tree(workspace, tree)


def baseline(workspace, base, run_dir, *, framework, suite_command, timeout=DEFAULT_TIMEOUT,
             dependencies_from=None, base_patch=None, generated_record=None, generated_unrecorded=False,
             ignored_inputs=None) -> dict:
    """Run the suite once on the pristine base revision, with ``base_patch`` applied (cached by the caller)."""
    evidence = Path(run_dir) / "baseline"
    tree = make_tree(workspace, base, Path(run_dir) / "scratch" / "baseline", workspace, {},
                     dependencies_from=dependencies_from, patch=base_patch, generated_record=generated_record,
                     generated_unrecorded=generated_unrecorded, ignored_inputs=ignored_inputs)
    try:
        receipt = run_suite(framework, suite_command, tree, evidence, "suite-on-base", timeout=timeout)
    finally:
        remove_tree(workspace, tree)
    return {"base": base, "command": suite_command, "receipt": receipt, "health": suite_health(receipt)}


def suite_health(receipt) -> str:
    """passing | failing_tests (some pass) | failing (no per-test detail) | broken | timeout."""
    results = receipt.get("results")
    if receipt["timed_out"]:
        return "timeout"
    if not command_receipt.completed(receipt):
        return "broken"  # interrupted ownership cannot establish passing tests
    if receipt["exit_code"] in (126, 127):
        return "broken"  # the command itself could not run
    if receipt.get("results_expected") and results is None:
        return "broken"  # the runner ended before reporting any test
    if results is not None and not schedule.complete_results(receipt):
        return "broken"
    if results is not None and (results["total"] == 0 or (results["complete"] and not results["passed"])):
        return "broken"
    if receipt["exit_code"] == 0:
        return "passing"
    return "failing" if results is None else "failing_tests"


def _run_suite_base_definition(framework, suite_command, workspace, base, changes, run_dir, trees, checks, *,
                               timeout, dependencies_from=None, base_patch=None, generated_record=None,
                               generated_unrecorded=False, ignored_inputs=None):
    """Execute the base suite definition over the candidate's product code (#587).

    The tree is the effective base the baseline executed — ``base`` with ``base_patch``,
    through make_tree with the other proof trees' parameters — overlaid with only the
    candidate changes that are not suite-definition paths by rule: product code stays the
    candidate's, definition files stay the base's, a deleted definition file stays, and a
    candidate-added definition file never enters. Returns {"run", "boundary"}: the receipt
    when the definition ran, or why the boundary could not be established (never a PASS).
    """
    try:
        definition, boundary = _base_suite_definition(workspace, base, suite_command, base_patch=base_patch)
    except (OSError, RuntimeError, ValueError) as error:
        return {"run": None, "boundary": f"the effective base could not be read ({error})"}
    if boundary:
        return {"run": None, "boundary": boundary}
    overlay = {path: status for path, status in changes.items()
               if path not in definition and not _definition_file(path) and not is_test_path(path)}
    tree = make_tree(workspace, base, Path(run_dir) / "scratch" / "base-definition", workspace, overlay,
                     dependencies_from=dependencies_from, patch=base_patch, generated_record=generated_record,
                     generated_unrecorded=generated_unrecorded, ignored_inputs=ignored_inputs)
    trees["base_definition"] = tree
    receipt = run_suite(framework, suite_command, tree, run_dir, "suite-base-definition", timeout=timeout)
    checks["suite_base_definition_on_candidate"] = receipt
    return {"run": receipt, "boundary": ""}


def verify(workspace, base, run_dir, *, framework=None, suite_command=None, regression_command=None,
           reported=None, base_suite=None, timeout=DEFAULT_TIMEOUT, dependencies_from=None,
           independent_dependencies=None, allow_no_test=False, new_behavior=False, preserve_only=False,
           base_patch=None, source_paths=(), test_only_allowed=False, generated_record=None,
           generated_unrecorded=False, ignored_inputs=None, guards=(), test_root=None) -> dict:
    """Verify the candidate in ``workspace`` against ``base``; see module docstring.

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
    then be empty (a validation-only re-check of a merged workstream). The suite runs on the
    candidate (_suite_guards).

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
    """
    workspace, run_dir = Path(workspace), Path(run_dir)
    test_root = test_roots.normalize(test_root) if test_root is not None else None
    run_dir.mkdir(parents=True, exist_ok=True)
    configured = node_tests.parse(suite_command)
    if configured:
        framework = Framework("node", suite_command, invocation=configured)
    before = source_snapshot.snapshot(workspace, paths=source_paths)["revision"]
    changes = changed_files(workspace, base, source_paths=source_paths)
    tests = [p for p in changes if is_test_path(p)]
    sources = [p for p in changes if not is_test_path(p)]
    preserve = preserve_only or (test_only_allowed and not sources)
    test_changes = {p: changes[p] for p in tests}
    fail, unverified, notes, review_reasons = [], [], [], []
    checks: dict[str, dict] = {}  # command receipts only
    proof: dict = {}
    commands = select_commands(framework, [p for p in tests if changes[p] != "deleted"],
                               suite_command=suite_command, regression_command=regression_command,
                               reported=reported)
    notes += commands["notes"]
    if ignored_inputs is None:
        notes += classify_generated_sources(dependencies_from or workspace, generated_record,
                                            unrecorded=generated_unrecorded)[1]
    runnable_tests = [p for p in tests if changes[p] != "deleted"]
    # Coverage the source already has: no test changed, so nothing can flip; the guards must hold.
    existing_guards = preserve_only and not runnable_tests
    if not changes and not preserve_only:
        fail.append("No change: the candidate is identical to the base revision")
    elif not sources and not preserve:
        fail.append("Only test files changed; a fix must change product code")
    deleted = [p for p in tests if changes[p] == "deleted"]
    if deleted:
        fail.append("Existing test files were deleted: " + ", ".join(deleted))
    removed = removed_python_tests(workspace, base, changes)
    if removed:
        fail.append("Existing tests were removed: " + ", ".join(removed[:20]))
    if not runnable_tests and not existing_guards:
        (unverified if allow_no_test else fail).append(
            "No regression test was added or changed, so the bug is not shown to be reproduced")
    stray = test_roots.outside(test_root, changes) if test_root is not None else []
    if stray:
        unverified.append(f"Files outside the test root {test_root}/ changed, and no test there covers them: "
                          + ", ".join(stray[:10]))
    for kind in ("regression", "suite"):
        if commands[f"{kind}_source"] == "builder":
            unverified.append(f"The {kind} command came from the Builder's own report; pass "
                              f"--{'regression' if kind == 'regression' else 'test'}-command to verify with "
                              "a command you trust")

    trees, hidden = {}, []
    try:
        if (changes or existing_guards) and (commands["regression"] or commands["suite"]):
            trees["candidate"] = make_tree(workspace, base, run_dir / "scratch" / "candidate", workspace, changes,
                                           dependencies_from=dependencies_from, generated_record=generated_record,
                                           generated_unrecorded=generated_unrecorded, ignored_inputs=ignored_inputs)
        if trees and runnable_tests and (sources or preserve):
            trees["base_with_tests"] = make_tree(workspace, base, run_dir / "scratch" / "base-with-tests",
                                                 workspace, test_changes, dependencies_from=dependencies_from,
                                                 patch=base_patch, generated_record=generated_record,
                                                 generated_unrecorded=generated_unrecorded, ignored_inputs=ignored_inputs)
        # A base with no Go project cannot run `go test` (no module, or no packages).
        # The same fact lets the suite comparison and, when the base run reports nothing,
        # the regression comparison treat that as absence rather than a broken suite (#685).
        no_go_project = bool(test_root is None and new_behavior and not preserve and not base_patch
                             and framework is not None and framework.name == "go"
                             and _no_go_project(workspace, base, _copied_generated(
                                 dependencies_from, generated_record, generated_unrecorded,
                                 ignored_inputs)))
        # Regression proof: identical tests, base source versus candidate source.
        if trees and runnable_tests and commands["regression"]:
            on_candidate = run_suite(framework, commands["regression"], trees["candidate"], run_dir,
                                     "regression-on-candidate", timeout=timeout)
            checks["regression_on_candidate"] = on_candidate
            on_base = None
            if "base_with_tests" in trees:
                on_base = run_suite(framework, commands["regression"], trees["base_with_tests"], run_dir,
                                    "regression-on-base", timeout=timeout)
                checks["regression_on_base"] = on_base
            _judge_regression(on_candidate, on_base, fail, unverified, notes, proof, review_reasons,
                              new_behavior=new_behavior, preserve_only=preserve,
                              absent_go_base=no_go_project and _go_test(commands["regression"] or ""),
                              known_failures=lambda: _pre_existing(
                                  framework, commands, changes, runnable_tests, workspace, base, run_dir, checks,
                                  timeout=timeout, dependencies_from=dependencies_from, base_patch=base_patch,
                                  generated_record=generated_record, generated_unrecorded=generated_unrecorded,
                                  ignored_inputs=ignored_inputs),
                              seam_names=lambda receipt: _seam_names(workspace, base, changes, receipt))
        elif "base_with_tests" in trees and commands["suite"]:
            # No targeted command: the whole suite proves the flip when base was green.
            if base_suite is None or base_suite["health"] != "passing":
                unverified.append("No targeted regression command, and the base suite is not green, "
                                  "so a fail-to-pass flip cannot be attributed to the new tests")
            else:
                on_base = run_suite(framework, commands["suite"], trees["base_with_tests"], run_dir,
                                    "suite-on-base-with-tests", timeout=timeout)
                checks["regression_on_base"] = on_base
                review_reasons.append("the regression proof rests on the whole suite's exit code")
                if on_base["exit_code"] == 0 and not preserve:
                    fail.append("The new tests pass on the unfixed base code, so they do not reproduce the bug")
        elif tests and sources:
            unverified.append("No command to run the regression tests; pass --regression-command")

        # No regressions: the suite on the candidate, compared with base.
        if "candidate" in trees and (sources or preserve) and commands["suite"]:
            reuse = checks.get("regression_on_candidate") if commands["suite"] == commands["regression"] else None
            on_candidate = reuse or run_suite(framework, commands["suite"], trees["candidate"], run_dir,
                                              "suite-on-candidate", timeout=timeout)
            checks["suite_on_candidate"] = on_candidate
            comparable = base_suite if base_suite and base_suite.get("command") == commands["suite"] else None
            # A positively identified document-only project (a root README.md,
            # plus .gitignore files and empty files when the ignored code is read
            # from a checkout the candidate does not edit, all regular and
            # non-executable, and no ignored code copied into the trees) may
            # introduce its first source and suite. Filename heuristics cannot
            # rule out old behavior: empty collection can hide a filtered
            # existing program, so any other base file or copied ignored code
            # still counts.
            allow_empty_base = bool(test_root is None and new_behavior and not preserve and not base_patch
                                    and comparable and comparable.get("base") == base
                                    and _document_only_base(workspace, base,
                                                            dependencies_from=dependencies_from,
                                                            independent=independent_dependencies,
                                                            ignored_inputs=ignored_inputs))
            # A first Go project on a base that has code in another language (a C#
            # port, for example) has no Go suite to preserve. `go test` then reports
            # no packages — inside a parent module that the candidate just added,
            # "matched no packages" — and that is not a broken base suite. A base
            # that already has a Go module or Go source keeps the ordinary rule.
            allow_absent_go = bool(no_go_project and comparable and comparable.get("base") == base
                                   and _go_test(commands["suite"] or ""))
            # The base's own suite definition, executed over the candidate's product code,
            # decides preservation for an exit-code-only script-driven suite (#587). The
            # trigger is evidential — passing base, completed exit-0 candidate suite, no
            # per-test results, not a document-only first suite — never command-string
            # recognition, so pretest, config, files, .npmrc, workspace and unrecognized
            # command shapes are all judged by the run.
            base_definition = None
            if (comparable is not None and comparable.get("health") == "passing" and not allow_empty_base
                    and command_receipt.completed(on_candidate) and not on_candidate["timed_out"]
                    and on_candidate["exit_code"] == 0 and on_candidate.get("results") is None):
                base_definition = _run_suite_base_definition(
                    framework, commands["suite"], workspace, base, changes, run_dir, trees, checks,
                    timeout=timeout, dependencies_from=dependencies_from, base_patch=base_patch,
                    generated_record=generated_record, generated_unrecorded=generated_unrecorded,
                    ignored_inputs=ignored_inputs)
            empty_root = None
            if (test_root is not None and new_behavior and not preserve and not base_patch
                    and suite_command is None and regression_command is None and framework is not None
                    and comparable and comparable.get("base") == base
                    and test_roots.empty_on_base(workspace, base, test_root)):
                # Public Framework fields and detected:* labels are not provenance.
                canonical = detect_framework(workspace, python=framework.python, test_root=test_root, base=base)
                if (canonical is not None and canonical.test_root == test_root
                        and (framework.name, framework.python, commands["suite"])
                        == (canonical.name, canonical.python, canonical.suite)):
                    empty_root = test_root
            if test_root is not None and not (empty_root is not None
                    and test_roots.first_suite((comparable or {}).get("receipt") or {}, on_candidate)
                    and schedule.complete_results(on_candidate)):
                receipt = (comparable or {}).get("receipt") or {}
                results = receipt.get("results")
                collector = schedule.collection_kind(commands["suite"])
                typed = (schedule.complete_results(receipt) and bool((results or {}).get("passed"))
                         and (not collector or schedule.complete_results(on_candidate)))
                # A known collector without parsed results is not an untyped
                # script merely because the supplied Framework mislabels it.
                untyped = (results is None and not receipt.get("results_expected")
                           and not collector and receipt.get("exit_code") == 0)
                baseline_evidence = (comparable is not None and comparable.get("base") == base
                                     and command_receipt.completed(receipt) and receipt.get("timed_out") is False
                                     and (typed or untyped))
                if not baseline_evidence:
                    unverified.append("The scoped suite has no comparable executed baseline evidence or canonical "
                                      "first-root authority; preservation of existing behavior is unproven")
            _judge_suite(on_candidate, comparable, fail, unverified, notes,
                         allow_empty_base=allow_empty_base, allow_absent_go=allow_absent_go,
                         base_definition=base_definition, empty_root=empty_root)
            # make_tree copies ignored test files into both trees, so a guard could rest on a test base never held.
            hidden = [path for path in _copied_generated(dependencies_from, generated_record, generated_unrecorded,
                                                         ignored_inputs) if is_test_path(path)]
            if hidden and existing_guards:
                unverified.append("Ignored test files are copied into the proof trees, so the guards cannot "
                                  "be shown to rest on tests the base revision holds: " + ", ".join(hidden[:5]))
            with_tests = None
            if guards and "base_with_tests" in trees and not hidden:
                # Without a targeted command the whole suite already ran there (regression_on_base).
                with_tests = (checks.get("regression_on_base") if not commands["regression"] else None) or \
                    run_suite(framework, commands["suite"], trees["base_with_tests"], run_dir,
                              "suite-on-base-with-tests", timeout=timeout)
                checks.setdefault("regression_on_base" if not commands["regression"] else
                                  "suite_on_base_with_tests", with_tests)
            _suite_guards(on_candidate, comparable, proof, unchanged=existing_guards, with_tests=with_tests)
        elif sources or preserve:
            unverified.append("No project test command was found; existing behavior was not checked "
                              "(pass --test-command)")
    finally:
        for tree in trees.values():
            remove_tree(workspace, tree)
    after = source_snapshot.snapshot(workspace, paths=source_paths)["revision"]
    if after != before:
        unverified.append("The candidate changed while it was being verified; verify again")
    for label, receipt in checks.items():
        if not command_receipt.completed(receipt):
            unverified.append(f"{label} has no complete owned-command evidence")
    stats = diff_stats(workspace, base, changes)
    if stats["binary_files"]:
        review_reasons.append("binary files changed: " + ", ".join(stats["binary_files"][:5]))
    if stats["non_code_files"]:
        review_reasons.append("non-code files changed: " + ", ".join(stats["non_code_files"][:5]))
    verdict = FAIL if fail else UNVERIFIED if unverified else PASS
    return {"verdict": verdict, "failures": fail, "unverified": unverified, "notes": notes,
            "review_reasons": review_reasons, "base": base, "base_patch": str(base_patch) if base_patch else None,
            "source_revision": before,
            "changes": changes, "test_files": tests, "source_files": sources, "stats": stats,
            "commands": {k: commands[k] for k in ("suite", "suite_source", "regression", "regression_source")},
            "framework": framework.to_dict() if framework else None,
            "baseline": ({"health": base_suite["health"], "exit_code": base_suite["receipt"]["exit_code"],
                          "output": base_suite["receipt"]["output"]} if base_suite else None),
            "fail_to_pass": proof.get("fail_to_pass"), "pass_to_pass": proof.get("pass_to_pass"),
            "not_run_on_base": proof.get("not_run_on_base"), "failed_on_candidate": proof.get("failed_on_candidate"),
            "suite_pass_to_pass": proof.get("suite_pass_to_pass"), "ignored_test_files": hidden,
            "no_test_changed": existing_guards, "guard_run_incomplete": bool(proof.get("guard_run_incomplete")),
            "checks": checks}


def _judge_regression(on_candidate, on_base, fail, unverified, notes, proof, review_reasons, *, known_failures,
                      new_behavior=False, preserve_only=False, absent_go_base=False, seam_names=None):
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
    if (not command_receipt.completed(on_candidate)
            or on_base is not None and not command_receipt.completed(on_base)):
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
                unverified.append("The regression run exited 0 without reporting any test result "
                                  "(did the process exit early?)")
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
                    notes.append("Tests in the changed files that already fail on base were not counted: "
                                 + ", ".join(sorted(failed)[:20]))
            return
        if candidate["complete"] and not passed:
            fail.append("The regression command ran no passing tests")
        if failed:
            known = known_failures() or set()
            unexplained = sorted(failed - known)
            if unexplained:
                fail.append("The regression tests fail on the candidate: " + ", ".join(unexplained[:20]))
            else:
                notes.append("Tests in the changed files that already fail on base were not counted: "
                             + ", ".join(sorted(failed)[:20]))
        if on_base is None:
            return
        if on_base["timed_out"]:
            unverified.append("The regression tests timed out on base; no complete fail-to-pass proof exists")
            return
        if base is None:
            # Outside a module, `go test` on the new files exits before any test event.
            # Those tests did not pass on a base that has no Go project (#685).
            if (new_behavior and not preserve_only and absent_go_base and passed
                    and on_base is not None and first_suite.go_reported_nothing(on_base)):
                names = sorted(passed)
                proof["fail_to_pass"] = names
                proof["pass_to_pass"] = []
                proof["not_run_on_base"] = names
                return
            # A module that reads a seam while loading can stop the whole run before it reports any test.
            seam = seam_names(on_base) if seam_names and not new_behavior else []
            unverified.append("The regression run on the base code reported no test results"
                              + (". " + proof_seam.reason([], seam) if seam else ""))
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
        seam = (seam_names(on_base) if seam_names and (base["collection_errors"] or flipped) and not new_behavior
                else [])
        if not flipped and new_behavior and not preserve_only:
            fail.append("No new or changed test passes with the change and did not pass without it, "
                        "so the tests do not show the new behavior")
        elif not flipped and not preserve_only:
            if setup_errors:
                fail.append(test_setup.proof_note(setup_errors))
            elif seam:
                fail.append(proof_seam.reason(base["collection_errors"], seam))
            elif base["collection_errors"]:
                fail.append("On the unfixed code the new tests only fail to import or collect ("
                            + ", ".join(base["collection_errors"][:5]) + "), so no test shows the bug. "
                            "Write the regression test against behavior that exists before the fix.")
            else:
                fail.append("No test fails on the unfixed base code and passes with the fix, "
                            "so the tests do not reproduce the bug")
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


def _suite_guards(on_candidate, base_suite, proof, *, unchanged, with_tests=None):
    """What the whole suite shows about guards (preserve cases), with complete per-test results on both runs.

    When no test changed (``unchanged``), nothing can flip and the base suite ran the same tests, so the
    targeted lists stay empty and the guards rest on its tests that pass on base and on the candidate, by
    id as well as by exact name (autocode_regression.check_cases). Otherwise
    ``with_tests`` is the suite on the base with the change's test files: its tests that pass there and on
    the candidate (``suite_pass_to_pass``) ran the same content on the original code and the change, which
    the pristine base cannot show once a test, fixture or golden file changed. ``failed_on_candidate`` is
    the candidate's failures: a guard with a failing variant does not hold, even when that variant already
    failed on base.
    """
    candidate = on_candidate.get("results")
    if not (candidate and candidate.get("complete")):
        return
    if unchanged:
        base = ((base_suite or {}).get("receipt") or {}).get("results")
        if base and base.get("complete"):
            proof.update(fail_to_pass=[], pass_to_pass=[], not_run_on_base=[],
                         suite_pass_to_pass=sorted(set(base["passed"]) & set(candidate["passed"])),
                         failed_on_candidate=sorted(candidate["failed"]))
        return
    before = (with_tests or {}).get("results")
    if before and before.get("complete") and not with_tests.get("timed_out"):
        proof.update(suite_pass_to_pass=sorted(set(before["passed"]) & set(candidate["passed"])),
                     failed_on_candidate=sorted(candidate["failed"]))
    elif with_tests is not None:
        proof["guard_run_incomplete"] = True  # a guard left without a test is then unproven, not refuted


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


def _pre_existing(framework, commands, changes, runnable_tests, workspace, base, run_dir, checks, *,
                  timeout, dependencies_from, base_patch=None, generated_record=None, generated_unrecorded=False,
                  ignored_inputs=None):
    """Failures of the changed test files' base versions on the pristine base, by test id."""
    if not framework or not framework.per_test or not str(commands["regression_source"]).startswith("derived"):
        return None
    existing = [p for p in runnable_tests if changes[p] == "modified"]
    command = framework.targeted(existing)
    if not command:
        return set()
    tree = make_tree(workspace, base, Path(run_dir) / "scratch" / "base", workspace, {},
                     dependencies_from=dependencies_from, patch=base_patch, generated_record=generated_record,
                     generated_unrecorded=generated_unrecorded, ignored_inputs=ignored_inputs)
    try:
        receipt = run_suite(framework, command, tree, run_dir, "regression-files-on-base", timeout=timeout)
    finally:
        remove_tree(workspace, tree)
    checks["regression_files_on_base"] = receipt
    return (set(receipt["results"]["failed"])
            if command_receipt.completed(receipt) and receipt.get("results") else None)


def _judge_suite(on_candidate, base_suite, fail, unverified, notes, *, allow_empty_base=False,
                 allow_absent_go=False, base_definition=None, empty_root=None):
    """Nothing that passed on base may fail, be skipped, be deselected or disappear."""
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
    # A first Go suite reports the pattern as a build failure, or no results at all.
    # That is absence of a Go project, not a collection error to preserve (#685).
    absent_go = first_suite.absent_go_suite(base_receipt, on_candidate, no_go_project=allow_absent_go)
    # A module that never imported, collected or built is not an executed test; a failed
    # hook or cancellation is (Node and Vitest report both as collection errors, so only
    # ``uncollected`` carries the rule, #503). Keep comparison below so a separately
    # observed regression still wins over incomplete preservation. Results saved by
    # parsers without ``uncollected`` fall back to collection_errors and fail closed.
    for label, results in (("base", base_results), ("candidate", candidate)):
        collection = (results or {}).get("uncollected", (results or {}).get("collection_errors")) or []
        if collection and not (label == "base" and absent_go):
            unverified.append(f"The {label} suite has collection errors; preservation is unproven: "
                              + ", ".join(collection[:5]))
    # Incompleteness only makes the absence of a failure unproven (#421): a candidate
    # failure that was observed passing on base stays FAIL below.
    if not absent_go and (base_receipt.get("timed_out")
                          or (base_results is not None and not base_results.get("complete"))):
        unverified.append("The base suite was incomplete; preservation of its passing tests is unproven")
    if candidate is not None and not candidate.get("total"):
        unverified.append("The project suite reported zero tests or incomplete per-test results")
        return
    if (empty_root is not None and test_roots.first_suite(base_receipt, on_candidate)
            and schedule.complete_results(on_candidate)):
        notes.append(f"The base holds nothing under {empty_root}/, so the suite rooted there has no existing "
                     "behavior to preserve; it ran and passed completely on the candidate")
        return
    if candidate is not None and not candidate.get("complete"):
        unverified.append("The project suite's per-test results were incomplete; "
                          "only its observed failures are judged")
    if on_candidate.get("results_expected") and candidate is None:
        if on_candidate["exit_code"] == 0:
            unverified.append("The project suite exited 0 without reporting any test result "
                              "(did the process exit early?)")
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
    empty_base = (allow_empty_base and base_results is not None
                  and base_receipt.get("results_expected") is True
                  and (base_receipt.get("exit_code") == 0
                       or (base_receipt.get("exit_code") == 5
                           and schedule.collection_kind(base_receipt.get("command", ""))
                           in ("unittest", "pytest")))
                  and base_results.get("complete") is True and base_results.get("total") == 0
                  and all(base_results.get(key) == [] for key in
                          ("passed", "failed", "skipped", "collection_errors"))
                  and on_candidate["exit_code"] == 0 and schedule.complete_results(on_candidate)
                  and candidate["passed"] and not candidate["failed"])
    if absent_go:
        notes.append("The pinned base has no Go project, so this suite has no existing behavior to preserve; "
                     "it reports no packages on base and passes completely on the candidate")
        return
    if base_results is not None:
        if not empty_base and (not schedule.complete_results(base_receipt) or not base_results["passed"]):
            unverified.append("The base suite provided no complete passing-test evidence; "
                              "preservation of existing behavior is unproven")
    elif base_suite is not None and base_suite.get("health") == "broken":
        if first_suite.absent_base_suite(base_receipt, on_candidate, document_only=allow_empty_base):
            notes.append("The pinned documentation-only base has no existing behavior to preserve; "
                         "its new suite is absent on base and passes completely on the candidate")
        else:
            unverified.append("The base suite could not run; preservation of existing behavior is unproven")
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
                fail.append("Tests that pass on base did not pass on the candidate (skipped, deselected, "
                            "renamed or missing): " + ", ".join(lost[:20]))
        elif candidate.get("complete") and candidate["total"] < base_results["total"]:
            fail.append(f"Fewer tests ran on the candidate ({candidate['total']}) than on base "
                        f"({base_results['total']})")
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
            unverified.append("The base suite definition could not be established (" + boundary
                              + "); preservation of existing behavior is unproven")
        elif definition_run is not None:
            if definition_run["timed_out"]:
                unverified.append("The base suite definition timed out over the candidate code; "
                                  "preservation of existing behavior is unproven")
            elif not command_receipt.completed(definition_run):
                unverified.append("The base suite definition run over the candidate code did not "
                                  "complete; preservation of existing behavior is unproven")
            elif definition_run["exit_code"] in (126, 127):
                unverified.append("The base suite definition command could not run over the candidate "
                                  f"code (exit {definition_run['exit_code']}); preservation of existing "
                                  "behavior is unproven")
            elif definition_run["exit_code"] != 0:
                fail.append("The base suite definition fails against the candidate code: the candidate "
                            "changed which tests the suite runs, so tests the base ran no longer pass")
        return
    if base_suite is None:
        unverified.append("The project suite fails on the candidate and there is no base run to compare with")
    elif base_receipt.get("exit_code") == 0:
        fail.append("The project suite passes on base but fails on the candidate")
    else:
        unverified.append("The project suite already fails on base and per-test results are unavailable, "
                          "so new failures cannot be ruled out (narrow it with --test-command)")


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
