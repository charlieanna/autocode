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

import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse

try:
    from . import autocode_util as util, autocode_agent_env as agent_env
    from . import autocode_test_environment as test_env
    from . import autocode_investigation_workspace as investigation_workspace
    from . import autocode_verification_schedule as schedule
    from . import autocode_node_tests as node_tests, autocode_proof_seam as proof_seam
    from . import autocode_vitest_tests as vitest_tests
    from . import autocode_scratch_overlay as scratch_overlay
    from . import autocode_test_setup as test_setup
except ImportError:
    import autocode_util as util, autocode_agent_env as agent_env
    import autocode_test_environment as test_env
    import autocode_investigation_workspace as investigation_workspace
    import autocode_verification_schedule as schedule
    import autocode_node_tests as node_tests
    import autocode_vitest_tests as vitest_tests
    import autocode_scratch_overlay as scratch_overlay
    import autocode_proof_seam as proof_seam
    import autocode_test_setup as test_setup

PASS, FAIL, UNVERIFIED = "PASS", "FAIL", "UNVERIFIED"
# Directories that hold tests wherever they appear, and ones that do only at the repository root:
# numpy/testing/ and django/test/ are shipped product code, while a top-level test/ is not.
TEST_DIRS = frozenset({"tests", "__tests__", "__snapshots__", "testdata", "test_data"})
ROOT_TEST_DIRS = frozenset({"test", "spec"})
TEST_NAME = re.compile(  # case-sensitive: Latest.java and Contest.kt are product code
    r"^(test_.*\.py|.*_tests?\.py|conftest\.py|.*\.(test|spec)\.[cm]?[jt]sx?|.*\.snap|.*_test\.go"
    r"|.*_(spec|test)\.rb|.*Tests?\.(java|kt|cs|swift|scala)|Test[A-Z_]\w*\.(java|kt|cs|swift|scala)"
    r"|test_.*\.(rb|sh))$")
PYTHON_TEST_MODULE = re.compile(r"^(test_.*|.*_tests?)\.py$")
CODE_SUFFIXES = frozenset({".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".go", ".rs", ".rb", ".java",
                           ".kt", ".kts", ".scala", ".swift", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".php",
                           ".m", ".mm", ".ex", ".exs", ".erl", ".hs", ".ml", ".lua", ".pl", ".sh", ".dart", ".zig"})
DEPENDENCY_DIRS = ("node_modules", ".venv", "venv")
COLLECTION_ERROR = re.compile(r"unittest\.loader\.(_FailedTest|ModuleImportFailure)|^::")
UNITTEST_HEADER = re.compile(r"^(\w+) \(([\w.]+)\)")
UNITTEST_STATUS = re.compile(r"\.\.\. (ok|FAIL|ERROR|skipped|expected failure|unexpected success)\b")
UNITTEST_BARE_STATUS = re.compile(r"(ok|FAIL|ERROR|expected failure|unexpected success)|skipped( .*)?")
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


def _git(cwd, *args, check=True):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, encoding="utf-8", errors="replace")
    if check and result.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _ignored(path: str) -> bool:
    # Top-level dependency links are runner-made (link_dependencies), never part of a fix.
    return (path.startswith((".autocode/", ".autocode-ui/")) or "/__pycache__/" in f"/{path}"
            or path.endswith(".pyc") or path in DEPENDENCY_DIRS)


def changed_files(workspace, base) -> dict[str, str]:
    """Every path whose content differs from ``base``: committed, staged, dirty or untracked."""
    changes: dict[str, str] = {}
    tokens = _git(workspace, "diff", "--name-status", "-z", "--no-renames", base, "--").split("\0")
    for status, path in zip(tokens[0::2], tokens[1::2]):
        if path:
            changes[path] = {"A": "added", "D": "deleted"}.get(status[:1], "modified")
    for path in _git(workspace, "ls-files", "--others", "--exclude-standard", "-z").split("\0"):
        if path:
            changes[path] = "added"
    return {path: status for path, status in sorted(changes.items()) if not _ignored(path)}


BINARY_LINES = 1000  # a binary change is never "tiny"


def diff_stats(workspace, base, changes) -> dict:
    lines, binary = {}, []
    for record in _git(workspace, "diff", "--numstat", "-z", "--no-renames", base, "--").split("\0"):
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

    def __init__(self, name, suite, *, python=None, runner=None, note="", node_files=()):
        self.name, self.suite, self.python, self.runner, self.note = name, suite, python, runner, note
        self.node_files = frozenset(node_files)

    @property
    def per_test(self):
        return self.name in ("pytest", "unittest", "go", "node", "vitest")

    def targeted(self, test_paths):
        files = sorted(test_paths)
        if self.name == "node":
            scripts = [p for p in files if p in self.node_files]
            return "node --test " + " ".join(map(shlex.quote, scripts)) if scripts else None
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


def detect_framework(root, *, python=None) -> Framework | None:
    """Best-effort detection from the project's own configuration files."""
    root = Path(root)
    # Untracked files count: AutoCode never commits, so in a new project every file the Builder wrote,
    # tests included, is untracked (a live greenfield run found no test command and could not prove its
    # tests, 2026-09-29). Ignored files, such as .autocode/, do not.
    files = [p for p in _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard",
                             check=False).split("\0") if p]
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
        if tests:
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


# --- execution --------------------------------------------------------------

def _kill_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def test_environment(tree, env=None):
    """Environment for a command run in ``tree``.

    The tree (and its ``src/``) goes first on PYTHONPATH so an editable install
    of the user's checkout (a ``.pth`` file in a linked venv) cannot shadow the
    code being tested. Credential-like variables are withheld: the tests are
    model-written code (autocode_agent_env).
    """
    environment = dict(agent_env.scrubbed(os.environ if env is None else env), PYTHONDONTWRITEBYTECODE="1", CI="1")
    roots = [str(Path(tree) / "src")] if (Path(tree) / "src").is_dir() else []
    roots.append(str(tree))
    if environment.get("PYTHONPATH"):
        roots.append(environment["PYTHONPATH"])
    environment["PYTHONPATH"] = os.pathsep.join(roots)
    python = test_env.virtualenv_python(tree)
    if python:
        # Test fixtures often launch `python3` rather than sys.executable.
        # They must inherit the same dependencies as the parent test process.
        environment["PATH"] = str(Path(python).parent) + os.pathsep + environment.get("PATH", "")
        environment["VIRTUAL_ENV"] = str(Path(python).parent.parent)
    return environment


def run_command(command, cwd, log_path, *, timeout=DEFAULT_TIMEOUT, env=None) -> dict:
    """Run one shell command in its own process group and return a receipt."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    timed_out = False
    with log_path.open("wb") as output:
        process = subprocess.Popen(["/bin/sh", "-c", command], cwd=cwd, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
                                   env=test_environment(cwd, env))
        try:
            exit_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(process)
            process.wait()
            exit_code = None
        finally:
            _kill_group(process)  # descendants that outlived the shell, or an interrupted run
    data = log_path.read_bytes()
    return {"command": command, "exit_code": exit_code, "timed_out": timed_out,
            "duration_seconds": round(time.monotonic() - started, 2), "output": str(log_path),
            "output_sha256": hashlib.sha256(data).hexdigest(),
            "tail": data[-TAIL_CHARS:].decode("utf-8", "replace")}


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
    return command


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
    return (" -m pytest" in command) if framework.name == "pytest" else (" -m unittest" in command and " -v" in command)


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
            "collection_errors": sorted(collection), "total": len(outcome) + len(collection), "complete": complete}


def _unittest_id(name, owner):
    return owner if owner.endswith("." + name) else f"{owner}::{name}"


def per_test_results(framework, receipt, xml_path, *, tree=None) -> dict | None:
    """Passed, failed and skipped test ids, or None when the run produced no parseable results.

    ``collection_errors`` are failures of a module to import or collect; they are
    failures, but they never name a test that ran.
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
        total = 0
        for case in result_tree.iter("testcase"):
            total += 1
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
        complete = True
    else:
        text = Path(receipt["output"]).read_text(errors="replace")
        ran = re.findall(r"^Ran (\d+) tests? in ", text, re.M)
        if not ran:
            return None
        total = int(ran[-1])
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
        collection = {test for test in failed if COLLECTION_ERROR.search(test)}
        complete = len(passed) + len(skipped) + len(failed) >= total
    results = {"passed": sorted(passed), "failed": sorted(failed), "skipped": sorted(skipped),
               "collection_errors": sorted(collection), "total": total, "complete": complete}
    if setup_errors:
        results["setup_errors"] = setup_errors
    return results


# --- scratch trees ----------------------------------------------------------

def copy_vendored_dependencies(source_root, tree):
    """Make ignored vendored dependencies available without sharing writable source files."""
    if not source_root:
        return
    source_root = Path(source_root).resolve()
    source, target = source_root / 'vendor', Path(tree) / 'vendor'
    if source.is_symlink() or target.is_symlink():
        raise ValueError('Vendored dependencies must not use a symlinked root')
    if not source.is_dir() or target.exists():
        return  # Tracked dependencies already come from the selected Git base and overlay.
    ignored_files = _git(source_root, 'ls-files', '-z', '--others', '--ignored', '--exclude-standard',
                         '--', 'vendor').split('\0')
    # Include only ignored, untracked files and their ancestors. In particular,
    # a new tracked vendor tree must not bring candidate code into the base.
    included = set()
    for name in filter(None, ignored_files):
        path = Path(name)
        included.update((path, *path.parents))
    if not included:
        return

    def ignored_entries(directory, names):
        relative = Path(directory).relative_to(source_root)
        omitted = {name for name in names if relative / name not in included}
        return omitted | investigation_workspace.ignored_entries(source_root, directory, set(names) - omitted)

    shutil.copytree(source, target, ignore=ignored_entries)

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


def copy_generated_sources(source_root, tree):
    """Copy build-generated source files (git-ignored code next to tracked code,
    such as a setuptools-scm or hatch-vcs ``_version.py``) into a scratch tree.
    A fresh worktree lacks them, so the package would not import there. Base and
    candidate trees receive the same files, so the comparison stays fair."""
    if not source_root:
        return []
    source_root, tree = Path(source_root), Path(tree)
    ignored = _git(source_root, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory",
                   check=False).split("\0")
    tracked_dirs = {str(PurePosixPath(p).parent) for p in _git(source_root, "ls-files", "-z", check=False).split("\0")
                    if p}
    copied = []
    for relative in ignored:
        path = PurePosixPath(relative)
        if (not relative or relative.endswith("/") or path.suffix not in CODE_SUFFIXES
                or str(path.parent) not in tracked_dirs or any(part in DEPENDENCY_DIRS for part in path.parts)):
            continue
        source, target = source_root / relative, tree / relative
        if (source.is_file() and not source.is_symlink() and not target.exists()
                and source.stat().st_size <= GENERATED_SOURCE_LIMIT):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied.append(relative)
    return copied


def _clear(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def make_tree(repo, base, destination, overlay_root, changes, *, dependencies_from=None, patch=None):
    """A detached worktree of ``base`` with ``changes`` copied from ``overlay_root``.

    ``patch`` (a patch file) is applied to ``base`` before the changes are copied in: the
    base a review follow-up is proven against is the change the review judged."""
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
        copy_vendored_dependencies(dependencies_from, destination)
        copy_generated_sources(dependencies_from, destination)
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
    kind = schedule.collection_kind(command)
    if kind:
        return Framework(kind, command, python=shlex.split(command)[0])
    if _go_test(command):
        return Framework("go", command)
    return None


def execution_identity(workspace, *, command=None, dependencies_from=None, full=True):
    """Conservative observable source/runtime/environment identity for receipts.

    Full dependency bytes are included, not only manifests or changed paths.
    Unknown runtimes cannot reuse clean replay evidence. This does not attest a
    remote service, wall clock or provider sandbox; those remain fresh checks.
    """
    workspace = Path(workspace)
    source_snapshot = util.snapshot(workspace)
    source_symlinks = [name for name, value in source_snapshot.get("files", {}).items()
                       if value.startswith("symlink:") and name not in DEPENDENCY_DIRS]
    source_metadata = {}
    for name in source_snapshot.get("files", {}):
        path = workspace / name
        if path.is_file() and not path.is_symlink():
            stat = path.stat()
            source_metadata[name] = [stat.st_mode, stat.st_mtime_ns, stat.st_uid, stat.st_gid]
    environment = test_environment(workspace)
    relative_pythonpath = [p for p in environment.get("PYTHONPATH", "").split(os.pathsep)
                           if p and not Path(p).is_absolute()
                           and not (workspace / p).resolve().is_relative_to(workspace.resolve())]
    try:
        words = shlex.split(command or "")
    except ValueError:
        words = []
    python_command = bool(words and re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", Path(words[0]).name)
                          and not re.search(r"[;&|<>`$\n]", command))
    python = words[0] if python_command else python_for(dependencies_from or workspace)
    executable = shutil.which(python, path=environment.get("PATH", ""))
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
                    if (target / ".git").exists():
                        editable_sources[str(target)] = util.snapshot(target)["revision"]
                    else:
                        editable_sources[str(target)] = schedule.tree_identity(target, excluded={
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
    if dependency_roots and (dependency_roots[0] / "vendor").exists():
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
    return {"source_revision": source_snapshot["revision"], "source_metadata": util.digest(source_metadata),
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
            "platform": [sys.platform, os.uname().release, os.uname().machine]}


# Candidate-tree fields: they change with every Builder edit and therefore must
# not key a cache of the base suite, which always runs on the base commit (#426).
_SOURCE_IDENTITY_KEYS = ("source_revision", "source_metadata", "generated_sources", "unbound_source_symlinks")


def baseline_identity(workspace, *, command=None, dependencies_from=None):
    """Runtime/dependency identity of a base-suite run, never the candidate tree.

    The base suite is executed on a scratch tree of the base commit (plus an
    optional base patch). Builder edits to the candidate workspace cannot change
    its result, so this binding omits source revision, per-file metadata and
    generated sources. Dependency trees, interpreter, environment and platform
    stay: they do determine the base result.

    Reuse is offered when the remaining binding is complete enough to notice a
    runtime change (no unbound editables or relative PYTHONPATH, and dependency
    roots hashed or none present). Unlike ``execution_identity``, an isolated
    Python virtualenv is not required: npm, Go and a global interpreter still
    get a stable cache key.
    """
    identity = execution_identity(workspace, command=command, dependencies_from=dependencies_from)
    environment = test_environment(workspace)
    roots = test_env.dependency_roots(dependencies_from or workspace)
    dependencies = []
    for name in DEPENDENCY_DIRS:
        source = next((root / name for root in roots if (root / name).exists()), None)
        if source:
            dependencies.append(schedule.tree_identity(source.resolve()))
    if roots and (roots[0] / "vendor").exists():
        dependencies.append(schedule.tree_identity((roots[0] / "vendor").resolve()))
    bound = {k: v for k, v in identity.items() if k not in _SOURCE_IDENTITY_KEYS}
    bound["dependencies"] = sorted(dependencies) or identity.get("dependencies")
    bound["environment_hash"] = util.digest(environment)
    bound["cache_policy"] = "baseline_runtime_identity"
    bound["cache_binding_complete"] = not (identity.get("unbound_editables")
                                           or identity.get("unbound_relative_pythonpath"))
    bound["reuse_supported"] = bool(bound["cache_binding_complete"]
                                    and (bound["dependencies"] is not None or not any(
                                        (root / name).exists() for name in DEPENDENCY_DIRS for root in roots)))
    return bound


def scratch_run(workspace, run_dir, *, patch=None, tests=(), command=None, timeout=DEFAULT_TIMEOUT,
                files=None, links=None) -> dict:
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
    tree = make_tree(workspace, head, run_dir / "scratch" / "tree", workspace, changed_files(workspace, head),
                     dependencies_from=workspace)
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
        return {**receipt, "error": ""}
    finally:
        remove_tree(workspace, tree)


def baseline(workspace, base, run_dir, *, framework, suite_command, timeout=DEFAULT_TIMEOUT,
             dependencies_from=None, base_patch=None) -> dict:
    """Run the suite once on the pristine base revision, with ``base_patch`` applied (cached by the caller)."""
    evidence = Path(run_dir) / "baseline"
    tree = make_tree(workspace, base, Path(run_dir) / "scratch" / "baseline", workspace, {},
                     dependencies_from=dependencies_from, patch=base_patch)
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


def verify(workspace, base, run_dir, *, framework=None, suite_command=None, regression_command=None,
           reported=None, base_suite=None, timeout=DEFAULT_TIMEOUT, dependencies_from=None,
           allow_no_test=False, new_behavior=False, base_patch=None) -> dict:
    """Verify the candidate in ``workspace`` against ``base``; see module docstring.

    ``base_patch`` is a patch file applied to ``base`` wherever the proof runs "the original
    code": a follow-up that fixes a reviewed change is proven against that change, where the
    review's findings exist, not against the code before it.

    ``new_behavior`` is for a feature rather than a bug fix: a new test proves the change
    when it passes on the candidate and did not pass on base, which includes failing to
    import there because the code it tests does not exist yet. A bug fix's test must run
    and fail on base (an import error is not a reproduction).
    """
    workspace, run_dir = Path(workspace), Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    before = util.snapshot(workspace)["revision"]
    changes = changed_files(workspace, base)
    tests = [p for p in changes if is_test_path(p)]
    sources = [p for p in changes if not is_test_path(p)]
    test_changes = {p: changes[p] for p in tests}
    fail, unverified, notes, review_reasons = [], [], [], []
    checks: dict[str, dict] = {}  # command receipts only
    proof: dict = {}
    commands = select_commands(framework, [p for p in tests if changes[p] != "deleted"],
                               suite_command=suite_command, regression_command=regression_command,
                               reported=reported)
    notes += commands["notes"]
    if not changes:
        fail.append("No change: the candidate is identical to the base revision")
    elif not sources:
        fail.append("Only test files changed; a fix must change product code")
    deleted = [p for p in tests if changes[p] == "deleted"]
    if deleted:
        fail.append("Existing test files were deleted: " + ", ".join(deleted))
    removed = removed_python_tests(workspace, base, changes)
    if removed:
        fail.append("Existing tests were removed: " + ", ".join(removed[:20]))
    runnable_tests = [p for p in tests if changes[p] != "deleted"]
    if not runnable_tests:
        (unverified if allow_no_test else fail).append(
            "No regression test was added or changed, so the bug is not shown to be reproduced")
    for kind in ("regression", "suite"):
        if commands[f"{kind}_source"] == "builder":
            unverified.append(f"The {kind} command came from the Builder's own report; pass "
                              f"--{'regression' if kind == 'regression' else 'test'}-command to verify with "
                              "a command you trust")

    trees = {}
    try:
        if changes and (commands["regression"] or commands["suite"]):
            trees["candidate"] = make_tree(workspace, base, run_dir / "scratch" / "candidate", workspace, changes,
                                           dependencies_from=dependencies_from)
        if trees and sources and runnable_tests:
            trees["base_with_tests"] = make_tree(workspace, base, run_dir / "scratch" / "base-with-tests",
                                                 workspace, test_changes, dependencies_from=dependencies_from,
                                                 patch=base_patch)
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
                              new_behavior=new_behavior, known_failures=lambda: _pre_existing(
                                  framework, commands, changes, runnable_tests, workspace, base, run_dir, checks,
                                  timeout=timeout, dependencies_from=dependencies_from, base_patch=base_patch),
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
                if on_base["exit_code"] == 0:
                    fail.append("The new tests pass on the unfixed base code, so they do not reproduce the bug")
        elif tests and sources:
            unverified.append("No command to run the regression tests; pass --regression-command")

        # No regressions: the suite on the candidate, compared with base.
        if "candidate" in trees and sources and commands["suite"]:
            reuse = checks.get("regression_on_candidate") if commands["suite"] == commands["regression"] else None
            on_candidate = reuse or run_suite(framework, commands["suite"], trees["candidate"], run_dir,
                                              "suite-on-candidate", timeout=timeout)
            checks["suite_on_candidate"] = on_candidate
            comparable = base_suite if base_suite and base_suite.get("command") == commands["suite"] else None
            _judge_suite(on_candidate, comparable, fail, unverified, notes)
        elif sources:
            unverified.append("No project test command was found; existing behavior was not checked "
                              "(pass --test-command)")
    finally:
        for tree in trees.values():
            remove_tree(workspace, tree)
    after = util.snapshot(workspace)["revision"]
    if after != before:
        unverified.append("The candidate changed while it was being verified; verify again")
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
            "not_run_on_base": proof.get("not_run_on_base"),
            "checks": checks}


def _judge_regression(on_candidate, on_base, fail, unverified, notes, proof, review_reasons, *, known_failures,
                      new_behavior=False, seam_names=None):
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
            unverified.append("Per-test results of the regression run were incomplete")
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
        if not flipped and new_behavior:
            fail.append("No new or changed test passes with the change and did not pass without it, "
                        "so the tests do not show the new behavior")
        elif not flipped:
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
    if on_base["exit_code"] == 0:
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


def _pre_existing(framework, commands, changes, runnable_tests, workspace, base, run_dir, checks, *,
                  timeout, dependencies_from, base_patch=None):
    """Failures of the changed test files' base versions on the pristine base, by test id."""
    if not framework or not framework.per_test or not str(commands["regression_source"]).startswith("derived"):
        return None
    existing = [p for p in runnable_tests if changes[p] == "modified"]
    command = framework.targeted(existing)
    if not command:
        return set()
    tree = make_tree(workspace, base, Path(run_dir) / "scratch" / "base", workspace, {},
                     dependencies_from=dependencies_from, patch=base_patch)
    try:
        receipt = run_suite(framework, command, tree, run_dir, "regression-files-on-base", timeout=timeout)
    finally:
        remove_tree(workspace, tree)
    checks["regression_files_on_base"] = receipt
    return set(receipt["results"]["failed"]) if receipt.get("results") else None


def _judge_suite(on_candidate, base_suite, fail, unverified, notes):
    """Nothing that passed on base may fail, be skipped, be deselected or disappear."""
    if on_candidate["timed_out"]:
        fail.append("The project suite timed out on the candidate")
        return
    candidate = on_candidate.get("results")
    base_receipt = (base_suite or {}).get("receipt") or {}
    base_results = base_receipt.get("results")
    if base_receipt.get("timed_out") or (base_results is not None and not base_results.get("complete")):
        unverified.append("The base suite was incomplete; preservation of its passing tests is unproven")
        return
    if candidate is not None and (not candidate.get("complete") or not candidate.get("total")):
        unverified.append("The project suite reported zero tests or incomplete per-test results")
        return
    if on_candidate.get("results_expected") and candidate is None:
        if on_candidate["exit_code"] == 0:
            unverified.append("The project suite exited 0 without reporting any test result "
                              "(did the process exit early?)")
        elif base_receipt.get("exit_code") == 0:
            fail.append("The project suite passes on base but fails on the candidate")
        else:
            unverified.append("The project suite reported no test results on the candidate")
        return
    if candidate is not None and base_results is not None:
        new = sorted(set(candidate["failed"]) - set(base_results["failed"]))
        if new:
            fail.append("Tests that pass on base fail on the candidate: " + ", ".join(new[:20]))
        if candidate["complete"] and base_results["complete"]:
            lost = sorted(set(base_results["passed"]) - set(candidate["passed"]) - set(new))
            if lost:
                fail.append("Tests that pass on base did not pass on the candidate (skipped, deselected, "
                            "renamed or missing): " + ", ".join(lost[:20]))
        elif candidate["total"] < base_results["total"]:
            fail.append(f"Fewer tests ran on the candidate ({candidate['total']}) than on base "
                        f"({base_results['total']})")
        if base_results["failed"] and not new:
            notes.append(f"{len(base_results['failed'])} test(s) already failed on base; none newly fail")
        return
    if on_candidate["exit_code"] == 0:
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
