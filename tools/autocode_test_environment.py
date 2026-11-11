"""Locate a project's test dependencies without depending on the controller.

A linked task worktree normally lacks ignored virtual environments. Its main
Git checkout is an explicit dependency source; unrelated parent directories are
never searched. Keep the venv path (not the resolved interpreter symlink), since
Python uses it to find that environment's site-packages.
"""

from __future__ import annotations

import configparser
import contextlib
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
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


def package_dirs(tree) -> dict[str, str]:
    """The setuptools ``package-dir`` mapping ``tree`` declares, as {package: directory}.

    Read from ``pyproject.toml`` (``[tool.setuptools] package-dir``) or, failing
    that, ``setup.cfg`` (``[options] package_dir``). The key ``""`` is the root
    directory of every unmapped package. An unreadable file declares nothing.
    """
    tree = Path(tree)
    try:
        project = tomllib.loads((tree / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        project = {}
    tool = project.get("tool")
    setuptools = tool.get("setuptools") if isinstance(tool, dict) else None
    mapping = setuptools.get("package-dir") if isinstance(setuptools, dict) else None
    if isinstance(mapping, dict):
        return {str(k): v for k, v in mapping.items() if isinstance(v, str)}
    config = configparser.ConfigParser()
    try:
        config.read_string((tree / "setup.cfg").read_text(encoding="utf-8"))
        declared = config.get("options", "package_dir", fallback="")
    except (OSError, UnicodeDecodeError, configparser.Error):
        declared = ""
    entries = {}
    for line in declared.splitlines():
        if "=" in line:
            name, _, directory = line.partition("=")
            entries[name.strip()] = directory.strip()
    return entries


def _inside(tree: Path, relative: str) -> Path | None:
    """``tree / relative`` resolved, when it is a directory inside ``tree``."""
    try:
        path = (tree / relative).resolve()
    except (OSError, RuntimeError):
        return None
    return path if path.is_dir() and path.is_relative_to(tree) else None


def scratch_import_roots(tree, *, root: str | Path | None = None) -> list[str]:
    """PYTHONPATH entries binding ``tree``'s mapped packages to ``tree``'s own source (#2988).

    An editable install of a checkout whose ``package-dir`` maps a package to a
    differently named directory (``{autocode_cli = "tools"}``) installs a finder
    that resolves that package name to the original checkout. Putting ``tree`` on
    PYTHONPATH does not help: it holds ``tools/``, not ``autocode_cli/``, so the
    import falls through to the finder and the scratch bytes are never run.

    The root directory a ``""`` key names is returned as a path entry. Each
    top-level package mapped elsewhere gets a symlink named after the package in
    a private shim directory outside ``tree`` (``tree`` is never written), and that
    directory is returned. Nested packages follow their parent's directory.
    Mappings outside ``tree`` or to a missing directory are left alone. Raises
    ValueError when a mapped package cannot be bound, rather than letting the
    tests import a different checkout.
    """
    tree = Path(tree).resolve()
    roots, links = [], {}
    for name, relative in sorted(package_dirs(tree).items()):
        directory = _inside(tree, relative)
        if directory is None:
            continue
        if not name:
            if directory != tree and directory != tree / "src":
                roots.append(str(directory))
            continue
        if "." in name or not name.isidentifier() or directory in (tree / name, tree / "src" / name):
            continue
        links[name] = str(directory)
    if not links:
        return roots
    uid = os.getuid() if hasattr(os, "getuid") else "user"
    base = Path(root) if root is not None else Path(tempfile.gettempdir()) / f"autocode-package-shims-{uid}"
    identity = json.dumps([str(tree), links], sort_keys=True).encode("utf-8")
    shim_dir = base / hashlib.sha256(identity).hexdigest()[:16]

    def bound(link: Path, target: str) -> bool:
        return (
            link.is_symlink()
            and (not hasattr(os, "getuid") or os.lstat(link).st_uid == os.getuid())
            and os.readlink(link) == target
        )

    try:
        if not (_owned_private_dir(base) and _owned_private_dir(shim_dir)):
            raise OSError(f"{shim_dir} is not a private directory")
        for name, target in links.items():
            link = shim_dir / name
            if not bound(link, target):
                tmp = shim_dir / f".tmp_{name}_{os.getpid()}"
                with contextlib.suppress(OSError):
                    tmp.unlink()
                tmp.symlink_to(target, target_is_directory=True)
                tmp.replace(link)
            if not bound(link, target):
                raise OSError(f"{link} does not point to {target}")
    except OSError as error:
        raise ValueError(f"Cannot bind mapped packages {sorted(links)} to the scratch tree: {error}") from error
    return [*roots, str(shim_dir)]


def external_roots(paths, workspace) -> set[Path]:
    """The resolved ``paths`` a runtime identity hashes as dependencies.

    Paths inside ``workspace`` and its mapped-package shims are left out: they
    hold the workspace's own bytes, which the source identity already binds.
    Hashing a shim again would key the base suite's cache on candidate source.
    """
    workspace = Path(workspace).resolve()
    shims = {Path(p).resolve() for p in scratch_import_roots(workspace)}
    return {p for p in paths if not p.is_relative_to(workspace) and p not in shims}
