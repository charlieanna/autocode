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
"""

from __future__ import annotations

import subprocess
from pathlib import Path

try:
    from . import autocode_package_scope as package_scope
    from . import autocode_project_kinds as project_kinds
    from . import autocode_toolchain_suite as toolchain_suite
except ImportError:
    import autocode_package_scope as package_scope
    import autocode_project_kinds as project_kinds
    import autocode_toolchain_suite as toolchain_suite


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
