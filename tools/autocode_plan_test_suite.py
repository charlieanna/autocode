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

import re
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

try:
    from . import autocode_native_test_names as native_test_names
    from . import autocode_package_scope as package_scope
    from . import autocode_test_cases as test_cases
    from . import autocode_verify as verify
except ImportError:
    import autocode_native_test_names as native_test_names
    import autocode_package_scope as package_scope
    import autocode_test_cases as test_cases
    import autocode_verify as verify

if TYPE_CHECKING:
    from .autocode_run_state import RunState

KINDS = (
    ("python", re.compile(r"^(test_.*|.*_test)\.py$")),
    ("go", re.compile(r"^.*_test\.go$")),
    ("ruby", re.compile(r"^.*_spec\.rb$")),
    ("javascript", re.compile(r"^.*\.(test|spec)\.[cm]?[jt]sx?$")),
)
# The kind of test file each runner AutoCode targets runs (autocode_test_framework.Framework.targeted).
RUNNER_KIND = {
    "pytest": "python",
    "unittest": "python",
    "go": "go",
    "rspec": "ruby",
    "node": "javascript",
    "jest": "javascript",
    "vitest": "javascript",
    "mocha": "javascript",
}
SHOWN = {"python": "Python", "go": "Go", "ruby": "RSpec", "javascript": "JavaScript or TypeScript"}


def kind(path) -> str | None:
    """The kind of test file ``path`` is by its name, or None when its name says no kind."""
    name = PurePosixPath(str(path)).name
    return next((found for found, pattern in KINDS if pattern.match(name)), None)


def _plain(path) -> str | None:
    text = str(path).strip()
    text = text[2:] if text.startswith("./") else text
    parts = PurePosixPath(text).parts
    return text if parts and not PurePosixPath(text).is_absolute() and ".." not in parts else None


def planned_tests(body) -> list[str]:
    """The test files of a known kind the plan's milestones and first task name, in path order."""
    paths = [path for row in body.get("milestones") or [] for path in row.get("affected_paths") or []]
    paths += (body.get("initial_task") or {}).get("affected_paths") or []
    return sorted({plain for path in paths if (plain := _plain(path)) and kind(plain)})


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


def check(state: RunState, body, *, origin=None) -> None:
    """Refuse a plan whose test files the regression proof would never run (see the module docstring)."""
    options = (state.get("settings") or {}).get("regression") or {}
    if origin == native_test_names.USER_EDIT or test_cases.design_only(state):
        return
    if any(options.get(key) for key in ("test_command", "regression_command", "test_root")):
        return
    tests, workspace = planned_tests(body), state.get("workspace")
    if not tests or not workspace or not Path(workspace).is_dir():
        return
    python = options.get("python") or verify.python_for(state.get("project_workspace") or workspace)
    settings = state.get("settings")
    framework = verify.detect_framework(workspace, python=python, **package_scope.detection(settings))
    detect = lambda directory: verify.detect_framework(directory, python=python)  # noqa: E731
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
