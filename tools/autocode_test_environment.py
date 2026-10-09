"""Locate a project's test dependencies without depending on the controller.

A linked task worktree normally lacks ignored virtual environments. Its main
Git checkout is an explicit dependency source; unrelated parent directories are
never searched. Keep the venv path (not the resolved interpreter symlink), since
Python uses it to find that environment's site-packages.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

try:
    from . import autocode_python_tests as python_tests
except ImportError:
    import autocode_python_tests as python_tests


def dependency_roots(project):
    """The task checkout, then its main Git checkout when Git identifies one."""
    project = Path(project).absolute()
    roots = [project]
    try:
        result = subprocess.run(
            ["git", "-C", str(project), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return roots
    common = Path(result.stdout.strip())
    # A normal repository's .git is authoritative. Bare repositories and
    # separately configured Git directories do not imply a checkout parent.
    if result.returncode == 0 and common.name == ".git" and common.is_dir():
        main = common.parent
        if main.resolve() != project.resolve():
            roots.append(main)
    return roots


def virtualenv_python(project):
    """Prefer a task's own virtualenv, otherwise inherit its main checkout's."""
    for root in dependency_roots(project):
        for relative in (".venv/bin/python", "venv/bin/python"):
            candidate = root / relative
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return None


def python_for(project):
    return virtualenv_python(project) or shutil.which("python3") or sys.executable


def python_for_test_command(project, command):
    """Reuse an explicit interpreter from a literal pytest/unittest command.

    The existing parser accepts literal ``env NAME=value`` prefixes, not shell
    programs or computed paths. Bare interpreter names (including an env PATH
    override) retain normal project discovery. Normalize relative paths against
    the project without resolving virtualenv interpreter symlinks.
    """
    invocation = python_tests.parse(python_tests.verbose_unittest(command))
    if invocation is None:
        return None
    candidate = Path(invocation.python)
    if not candidate.is_absolute() and '/' not in invocation.python:
        return None
    candidate = Path(os.path.abspath(Path(project) / candidate))
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    return None
