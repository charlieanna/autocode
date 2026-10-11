"""Compare an executing package with a verified AutoCode source checkout.

This diagnostic never assigns the workspace's commit to the executing runner.
Package declarations select the same Python modules and resources on both sides;
bytecode and generated distribution metadata are not application payload.
"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MAX_FILES = 10_000
MAX_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True)
class Comparison:
    checkout: Path
    commit: str
    changed: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    extra: tuple[str, ...] = ()
    error: str | None = None


def _git(workspace: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return subprocess.check_output(
        ["git", "-c", "core.hooksPath=/dev/null", "-C", str(workspace), *args],
        env=env,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=10,
    ).removesuffix("\n")


def _checkout(workspace: Path):
    try:
        root = Path(_git(workspace, "rev-parse", "--show-toplevel")).resolve()
        project = tomllib.loads((root / "pyproject.toml").read_text())
        declared = project.get("project") or {}
        tool = project.get("tool") or {}
        if not isinstance(declared, dict) or not isinstance(tool, dict):
            return None
        packaging = tool.get("setuptools") or {}
        if not isinstance(packaging, dict) or not isinstance(packaging.get("package-dir"), dict):
            return None
        if declared.get("name") != "autocode-supervisor" or not isinstance(declared.get("version"), str):
            return None
        if not declared["version"].strip() or packaging["package-dir"].get("autocode_cli") != "tools":
            return None
        _git(root, "ls-files", "--error-unmatch", "pyproject.toml", "tools/autocode.py", "tools/autocode_util.py")
        commit = _git(root, "rev-parse", "--short", "HEAD")
        if _git(root, "status", "--porcelain", "--", "tools", "pyproject.toml"):
            commit += "+modified"
        return root, commit, packaging
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _patterns(packaging: dict) -> tuple[tuple[str, str], ...]:
    packages = packaging.get("packages")
    if not isinstance(packages, list) or "autocode_cli" not in packages:
        raise ValueError("unsupported package declaration")
    data = packaging.get("package-data") or {}
    if not isinstance(data, dict):
        raise ValueError("unsupported package resource declaration")
    selected = []
    for package in packages:
        if not isinstance(package, str) or (package != "autocode_cli" and not package.startswith("autocode_cli.")):
            raise ValueError("unsupported package name")
        parts = package.split(".")
        if any(not part.isidentifier() for part in parts):
            raise ValueError("unsupported package path")
        prefix = "/".join(parts[1:])
        resources = data.get(package, [])
        if not isinstance(resources, list):
            raise ValueError("unsupported package resources")
        for pattern in ["*.py", "*.pyi", "py.typed", *resources]:
            if (
                not isinstance(pattern, str)
                or PurePosixPath(pattern).is_absolute()
                or ".." in PurePosixPath(pattern).parts
            ):
                raise ValueError("unsafe package resource pattern")
            selected.append((prefix, pattern))
    return tuple(selected)


def _manifest(root: Path, patterns: tuple[tuple[str, str], ...]) -> dict[str, str]:
    root = root.resolve()
    paths = set()
    for prefix, pattern in patterns:
        directory = root / prefix
        if directory.is_symlink():
            raise ValueError("linked package directory cannot be compared")
        for path in directory.glob(pattern):
            relative = path.relative_to(root)
            if "__pycache__" in relative.parts or path.suffix in (".pyc", ".pyo"):
                continue
            if any(part.endswith((".dist-info", ".egg-info")) for part in relative.parts):
                continue
            if path.is_dir():
                continue
            if not path.resolve().is_relative_to(root):
                raise ValueError(f"package path escapes its root: {relative}")
            paths.add(path)
            if len(paths) > MAX_FILES:
                raise ValueError("package payload exceeds its file limit")
    result = {}
    total = 0
    for path in sorted(paths):
        named = path.lstat()
        if not stat.S_ISREG(named.st_mode):
            raise ValueError(f"package payload is not a regular file: {path.relative_to(root)}")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError(f"package payload is not a regular file: {path.relative_to(root)}")
            if (before.st_dev, before.st_ino) != (named.st_dev, named.st_ino):
                raise ValueError("package payload changed before being read")
            digest = hashlib.sha256()
            while block := os.read(fd, 65536):
                total += len(block)
                if total > MAX_BYTES:
                    raise ValueError("package payload exceeds its byte limit")
                digest.update(block)
            after = os.fstat(fd)
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise ValueError("package payload changed while being read")
            result[path.relative_to(root).as_posix()] = digest.hexdigest()
        finally:
            os.close(fd)
    return result


def compare(workspace: Path, executing: Path) -> Comparison | None:
    """Return no comparison for a foreign project or the executing source tree."""
    identified = _checkout(workspace)
    if identified is None:
        return None
    root, commit, packaging = identified
    if (root / "tools").resolve() == executing.resolve():
        return None
    try:
        patterns = _patterns(packaging)
        source = _manifest(root / "tools", patterns)
        installed = _manifest(executing, patterns)
        return Comparison(
            root,
            commit,
            tuple(sorted(name for name in source.keys() & installed.keys() if source[name] != installed[name])),
            tuple(sorted(source.keys() - installed.keys())),
            tuple(sorted(installed.keys() - source.keys())),
        )
    except (OSError, ValueError) as error:
        return Comparison(root, commit, error=str(error))
