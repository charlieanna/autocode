"""Locate a project's test dependencies without depending on the controller.

A linked task worktree normally lacks ignored virtual environments. Its main
Git checkout is an explicit dependency source; unrelated parent directories are
never searched. Keep the venv path (not the resolved interpreter symlink), since
Python uses it to find that environment's site-packages.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

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
            capture_output=True,
            text=True,
            timeout=10,
        )
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
    if not candidate.is_absolute() and "/" not in invocation.python:
        return None
    candidate = Path(os.path.abspath(Path(project) / candidate))
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    return None


def _owned_private_dir(path: Path) -> bool:
    """True when path is a real directory owned by the current POSIX user with mode 0700."""
    try:
        path.mkdir(mode=0o700, exist_ok=True)
        info = os.lstat(path)
        if not stat.S_ISDIR(info.st_mode):
            return False
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            return False
        if (info.st_mode & 0o777) != 0o700:
            path.chmod(0o700)
        return True
    except OSError:
        return False


def ensure_python_shim(python_path: str, *, root: str | Path | None = None) -> str | None:
    """A private user-owned directory on PATH providing only a 'python' symlink to python_path."""
    if not python_path:
        return None
    target = str(python_path)
    uid = os.getuid() if hasattr(os, "getuid") else "user"
    base = Path(root) if root is not None else Path(tempfile.gettempdir()) / f"autocode-python-shims-{uid}"
    try:
        if not _owned_private_dir(base):
            return None
        digest = hashlib.sha256(target.encode("utf-8")).hexdigest()[:16]
        shim_dir = base / digest
        if not _owned_private_dir(shim_dir):
            return None
        shim = shim_dir / "python"
        if not (
            shim.is_symlink()
            and (not hasattr(os, "getuid") or os.lstat(shim).st_uid == os.getuid())
            and os.readlink(shim) == target
        ):
            tmp = shim_dir / f".tmp_python_{os.getpid()}"
            with contextlib.suppress(OSError):
                tmp.unlink()
            tmp.symlink_to(target)
            tmp.replace(shim)
        if (
            shim.is_symlink()
            and (not hasattr(os, "getuid") or os.lstat(shim).st_uid == os.getuid())
            and os.readlink(shim) == target
        ):
            return str(shim_dir)
        return None
    except OSError:
        return None
