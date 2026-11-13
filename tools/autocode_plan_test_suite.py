"""The plan's test files and the suite the regression proof will run them with, checked when a plan is drafted (#2889).

A live bug fix in a polyglot repository (idlecampus, 2026-11-07: a Node suite in the root package.json, a Rails
backend/ with RSpec) approved a plan whose every test: and guard: criterion was an RSpec example under
backend/spec/. The suite the proof used could not run one of them, and that surfaced only after a full Builder and
Tester round. It is known when the plan is drafted: the plan names its test files (in a milestone's or the first
task's affected_paths), and the proof chooses its suite from the repository (autocode_regression.prove): the suite
detected at the root, or, when that runs none of the changed tests, the suite of the project around them all
(autocode_package_scope.for_tests).

Before the files exist a runner cannot be asked which of them it would run (a Node suite runs the scripts it
found), so a planned test file is judged by its kind: a Python test module, a Go test, an RSpec spec, or a
JavaScript or TypeScript .test/.spec file. Other files in a test directory (helpers, fixtures, conftest.py) are not
judged. ``problems`` names the planned tests of another kind than the suite the proof will use runs. ``check``
refuses such a draft when it is installed and when it is approved, so the Planner settles it before a Builder
runs. A run whose commands the person chose (--test-command, --regression-command), a --test-root run, a design
job and the user's own edit of the plan are not judged here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

try:
    from . import autocode_command_guards as command_guards
    from . import autocode_native_test_names as native_test_names
    from . import autocode_package_scope as package_scope
    from . import autocode_project_kinds as project_kinds
    from . import autocode_test_cases as test_cases
    from . import autocode_verify as verify
except ImportError:
    import autocode_command_guards as command_guards
    import autocode_native_test_names as native_test_names
    import autocode_package_scope as package_scope
    import autocode_project_kinds as project_kinds
    import autocode_test_cases as test_cases
    import autocode_verify as verify

if TYPE_CHECKING:
    from .autocode_run_state import RunState

# The kind of test file each runner AutoCode targets runs (autocode_test_framework.Framework.targeted),
# from the languages' rows in autocode_project_kinds.
KINDS = project_kinds.COLLECTED
RUNNER_KIND = project_kinds.RUNNER_LANGUAGE
SHOWN = {language.name: language.shown for language in project_kinds.LANGUAGES if language.collected}


def kind(path) -> str | None:
    """The kind of test file ``path`` is by its name, or None when its name says no kind."""
    return project_kinds.collected_by(path)


def _plain(path) -> str | None:
    text = str(path).strip()
    text = text[2:] if text.startswith("./") else text
    parts = PurePosixPath(text).parts
    return text if parts and not PurePosixPath(text).is_absolute() and ".." not in parts else None


def planned_tests(body) -> list[str]:
    """The test files of a known kind the plan's milestones and first task name, in path order."""
    return [path for path in _planned(body) if kind(path)]


def problems(workspace, framework, tests, detect_in, *, scoped=False) -> list[str]:
    """The ``tests`` the proof will not run: those of another kind than ``framework`` runs, unless none of them
    is of its kind and the project around them all has a suite that runs them (autocode_package_scope.enclosing,
    never in a ``scoped`` run). A runner of no known kind (``make test``) may run any test, so none is named."""
    expected = RUNNER_KIND.get(framework.name) if framework is not None else None
    if expected is None:
        return []
    other = [test for test in tests if kind(test) != expected]
    if not other or scoped or len(other) < len(tests):
        return other
    holds = lambda found: all(kind(test) == RUNNER_KIND.get(found.name) for test in tests)  # noqa: E731
    return [] if package_scope.enclosing(workspace, tests, detect_in, runs=holds) else other


def unnamed_cases(workspace, framework, tests, body, detect_in) -> str | None:
    """Why the plan's test: and guard: criteria could never be proven by name here, or None.

    The proof matches such a criterion to a test only from a runner's per-test results
    (autocode_regression.check_cases). A runner that reports only an exit code (``make test``,
    Ruby test files run one by one, a Mocha suite) leaves every such criterion
    unproven after a full Builder and Tester round, and the Resolver then asked the person how to prove
    it (a Ruby Minitest bug fix, 2026-11-13). It is known when the plan is drafted, so the Planner gets
    it instead: the same criterion is proven by the exit code of the command that runs its test file.
    A suite of the project around the planned tests that reports per-test results counts, as the proof
    uses it (autocode_package_scope.for_tests). A project with test code but no runner AutoCode detects
    is judged the same way: its proof runs the approved plan's command (autocode_plan_proof_command). A new
    project, before its first test exists, is not judged: the proof detects its suite on the changed code.
    """
    named = [case["id"] for case in test_cases.plan_cases(body)]
    if not named or (framework is not None and framework.per_test):
        return None
    if framework is None and not _existing_tests(workspace):
        return None
    if tests and package_scope.enclosing(workspace, tests, detect_in, runs=lambda found: found.per_test):
        return None
    runner = f"the {framework.name} suite `{framework.suite}`" if framework is not None else "no runner AutoCode reads"
    return (
        f"Criteria {', '.join(named)} name a test after test: or guard:, but this project's tests run with {runner}, "
        "which reports only whether the whole run passed, never which test passed, so the runner could never match "
        "a test to these criteria. Write each of them without test: or guard:, as the command that runs its test "
        'file and what it shows, such as "`ruby test/pager_test.rb` fails on the original code and passes with '
        "the fix\", and name the test file in its milestone's affected_paths. For behavior that already works and "
        'must keep working, write "guard: run `ruby test/pager_test.rb`", the command that runs its existing test '
        "file. The regression proof runs each command on the original code and with the change and judges its "
        "exit code"
    )


def _planned(body) -> list[str]:
    """Every plain path the plan's milestones and first task name."""
    paths = [path for row in body.get("milestones") or [] for path in row.get("affected_paths") or []]
    paths += (body.get("initial_task") or {}).get("affected_paths") or []
    return sorted({plain for path in paths if (plain := _plain(path))})


def _in_tests(path) -> bool:
    """A test file, or a directory that holds tests (``tests/``), which a plan may name instead."""
    return verify.is_test_path(path) or verify.is_test_path(path.rstrip("/") + "/_")


def untested_code(workspace, body) -> str | None:
    """Why the proof could find no test for the code this plan changes, or None.

    Every change to product code owes the regression proof a test (autocode_changed_code_trigger): one
    that fails on the original code and passes with the change, or, for a change that alters no
    behavior, a guard on tests the project already has. A project with no test code has neither, so a
    plan there that names code files and no test file cannot pass its proof. A live run asked to add an
    option to a script in such a project planned the script alone, with shell commands as its criteria:
    the proof failed twice with "No regression test was added or changed", the Resolver asked the person
    to allow a test file, and the run took 20 stages (2026-11-13). It is known when the plan is drafted.

    Only that certain case is judged: a project with no test code, and a plan that names at least one
    code file, names no test file or test directory, and has no test: or guard: criterion. A project
    that has tests may guard the change with them or already hold the failing test. ``check`` asks only
    for a build or a bug fix: a review or a discussion may name code it reads.
    """
    if _existing_tests(workspace):
        return None
    paths = _planned(body)
    code = [path for path in paths if verify.is_code_path(path) and not _in_tests(path)]
    if not code or any(_in_tests(path) for path in paths):
        return None
    if test_cases.plan_cases(body) or command_guards.plan_guards(body):
        return None
    return (
        f"This plan changes code ({', '.join(code[:5])}) and plans no test, and the project has no tests yet. "
        "Every change to code needs a test that fails on the original code and passes with the change, so "
        "the regression proof would fail after a full Builder round, and nothing here can guard the change "
        "instead. Plan the test with the change: name a new test file in the affected_paths of the milestone "
        "that changes the code, write the criteria its tests show as test: criteria, and do not restrict the "
        "change to the code files alone"
    )


def _test_paths(workspace) -> list[str]:
    """The workspace's test paths, tracked or not."""
    listed = subprocess.run(
        ["git", "-C", str(workspace), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
    )
    names = listed.stdout.split("\0") if listed.returncode == 0 else []
    return [name for name in names if name and verify.is_test_path(name)]


def _existing_tests(workspace) -> bool:
    """Whether the workspace already holds test code (a test path that is a code file), tracked or not."""
    return any(verify.is_code_path(name) for name in _test_paths(workspace))


def check(state: RunState, body, *, origin=None) -> None:
    """Refuse a plan whose test files the regression proof would never run (see the module docstring)."""
    options = (state.get("settings") or {}).get("regression") or {}
    if origin == native_test_names.USER_EDIT or test_cases.design_only(state):
        return
    if any(options.get(key) for key in ("test_command", "regression_command", "test_root")):
        return
    tests, workspace = planned_tests(body), state.get("workspace")
    if not workspace or not Path(workspace).is_dir():
        return
    python = options.get("python") or verify.python_for(state.get("project_workspace") or workspace)
    settings = state.get("settings")
    framework = verify.detect_framework(workspace, python=python, **package_scope.detection(settings))
    detect = lambda directory: verify.detect_framework(directory, python=python)  # noqa: E731
    if unnamed := unnamed_cases(workspace, framework, tests, body, detect):
        raise ValueError(unnamed)
    # A guard that is a command runs tests the project already has, where no runner names them.
    if unusable := command_guards.problem(command_guards.plan_guards(body), framework, _test_paths(workspace)):
        raise ValueError(unusable)
    builds = (state.get("workflow") or {}).get("kind") in (None, "build", "bugfix")
    if builds and (untested := untested_code(workspace, body)):
        raise ValueError(untested)
    if not tests:
        return
    unrun = problems(workspace, framework, tests, detect, scoped=package_scope.of(settings) is not None)
    if unrun and framework is not None:  # problems() names none without a framework
        runs = SHOWN[RUNNER_KIND[framework.name]]
        raise ValueError(
            f"The regression proof would never run the plan's tests {', '.join(unrun)}: it runs the plan's tests "
            f"with one suite, here the {framework.name} suite `{framework.suite}`, which runs {runs} tests, and no "
            "project directory around all of the plan's tests has a suite of its own that runs them all. Put every "
            f"new or changed test where one suite runs them all: {runs} tests for `{framework.suite}`, or every "
            "test inside one project directory with its own manifest and suite (a Gemfile, package.json, go.mod or "
            "pyproject.toml there), which the proof then runs while the root's suite still checks the code outside "
            "it. Name each test file in the affected_paths of its milestone"
        )
