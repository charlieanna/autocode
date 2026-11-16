"""The command that runs changed original tests in a project below the repository root.

A run binds one command for its original tests when it starts: the suite detected at the repository
root. The original-test gate (autocode_protected_oracles.replay) runs it on the change and again with
the original tests put back. In a repository of projects side by side (``api/`` in Python beside
``web/`` in Node) that command is one project's, and it cannot run another project's tests: a bug fix
in ``web/`` changed ``web/test/pager.js``, the gate ran ``python3 -m unittest discover -v`` at the
root, which found no test, and the run stopped with "Protected tests changed and the original gate
did not pass" (2026-11-15), although the regression proof had just passed with ``web/``'s own suite.

The proof finds the project around the changed tests (autocode_package_scope.enclosing). This finds
it the same way for the gate, for the suites a project declares (autocode_toolchain_suite.detect), and
gives that suite's command for exactly those test files.

Python tests have the mirror problem. The bound Python runner can run them, but from the repository's
root: ``python3 -m unittest -v api/tests/test_pager.py`` cannot import ``pager`` from ``api/``, where
the tests are written to run. The proof gives a repository whose root is no project of its own to the
project around the tests (autocode_package_scope.for_tests), and ``python_command`` does the same with
the bound runner.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_package_scope as package_scope
    from . import autocode_project_kinds as project_kinds
    from . import autocode_toolchain_suite as toolchain_suite
except ImportError:
    import autocode_package_scope as package_scope
    import autocode_project_kinds as project_kinds
    import autocode_toolchain_suite as toolchain_suite

try:
    from .autocode_test_framework import Framework
except ImportError:
    from autocode_test_framework import Framework


def _detect(directory):
    listed = subprocess.run(
        ["git", "-C", str(directory), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=False,
    )
    files = [name for name in listed.stdout.split("\0") if name] if listed.returncode == 0 else []
    return toolchain_suite.detect(Path(directory), files, project_kinds.is_test_path)


def command(names, workspace) -> str | None:
    """The command that runs the test files ``names`` in the innermost project below ``workspace`` that
    holds them all and whose own suite runs them, or None.

    A project inside one of the repository's own test directories (``tests/fixtures/app/``) is a fixture
    of the tests around it, not a project with tests of its own.
    """
    found = package_scope.enclosing(workspace, names, _detect)
    if found is None or project_kinds.is_test_path(found.directory + "/_"):
        return None
    return found.targeted(names)


# Options of a Python runner whose value is a path, read from where the command runs.
_PATH_OPTIONS = ("-c", "--rootdir", "--confcutdir", "--ignore", "--ignore-glob", "--deselect")


def detected(state: RunState) -> bool:
    """Whether the run's original-test command is the one AutoCode detected, not one the person typed
    (``--test-command``, or a later correction of the original tests' command). A typed command says
    where it runs, and the proof runs it there too."""
    typed = (state.get("settings", {}).get("regression") or {}).get("test_command")
    return not typed and not any(
        event.get("kind") == "protected_tests_revised" for event in state.get("user_events", [])
    )


def _moves(invocation) -> bool:
    """Whether the runner means the same from another directory: no environment set before it, no
    interpreter named by a relative path, no option that names a path."""
    relative = "/" in invocation.python and not Path(invocation.python).is_absolute()
    paths = any(word.split("=", 1)[0] in _PATH_OPTIONS for word in invocation.arguments)
    return not (invocation.prefix or relative or paths)


def python_command(invocation, bound, names, workspace) -> str | None:
    """The bound Python runner (``invocation``, parsed from the command ``bound``) for the test modules
    ``names``, run inside the innermost project below ``workspace`` that holds them all, or None.

    None when the root is a project of its own, whose suite runs its tests from the root, when no
    directory with a manifest holds them all, when that directory is a fixture among the repository's
    own tests, when the runner names something by where it runs (``_moves``), and when it cannot be
    narrowed to the files (the whole bound suite then runs where it was bound).
    """
    root = Path(workspace)
    if any((root / name).is_file() for name in package_scope.MANIFESTS) or not _moves(invocation):
        return None
    runner = Framework(invocation.kind, bound, invocation=invocation)
    found = package_scope.enclosing(workspace, names, lambda directory: runner)
    if found is None or project_kinds.is_test_path(found.directory + "/_"):
        return None
    targeted = found.targeted(names)
    return None if targeted == found.suite else targeted
