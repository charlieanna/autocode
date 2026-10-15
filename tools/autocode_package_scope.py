"""One package of a monorepo as the scope of a run (#711).

``--package PATH`` names a directory of the repository when a run starts. The run saves it in
``settings.package`` and never changes it. Every reader asks this module:

- suite detection looks only inside the package, and the detected suite runs from the package
  directory: its command is ``cd PATH && SUITE``, so the command a person reads in a receipt is
  the command that ran, from the repository root, in every scratch copy;
- the protected-test inventory keeps only the package's tests;
- the regression proof leaves a change outside the package unverified.

An explicit ``--test-command`` still runs from the repository root, exactly as given.

Pure helpers over strings and paths, plus the framework wrapper the detection returns. It
imports only the test-runner descriptions, never the controller or the proof.
"""

from __future__ import annotations

import os
import shlex
import stat
import subprocess
from pathlib import Path

try:
    from . import autocode_test_root as test_roots
    from .autocode_test_framework import Framework
except ImportError:
    import autocode_test_root as test_roots
    from autocode_test_framework import Framework

SETTING = "package"


def add_arguments(parser):
    parser.add_argument(
        "--package",
        help="New runs in a monorepo: the repository directory the task works in, such as packages/api/; "
        "the detected suite runs there and only its tests are protected",
    )


def normalize(value) -> str:
    """A plain repository-relative directory, without a trailing slash."""
    try:
        return test_roots.normalize(value)
    except ValueError:
        raise ValueError(
            f"--package must be a directory inside the repository, such as packages/api/, named by plain "
            f"segments of letters, digits, '.', '_', '-', '+' and '@': {str(value)!r}"
        ) from None


def of(settings) -> str | None:
    """The run's package, or None for a run of the whole repository."""
    value = settings.get(SETTING) if isinstance(settings, dict) else None
    return value if isinstance(value, str) and value else None


def detection(settings) -> dict:
    """Keyword arguments for autocode_verify.detect_framework; empty for an unscoped run."""
    package = of(settings)
    return {"package": package} if package else {}


def directory_in(root, package) -> Path | None:
    """``root/package`` when every step of it is a real directory, never a symlink."""
    current = Path(root)
    for part in package.split("/"):
        current = current / part
        try:
            mode = os.lstat(current).st_mode
        except OSError:
            return None
        if not stat.S_ISDIR(mode):
            return None
    return current


def _listing(workspace, *args) -> str:
    try:
        result = subprocess.run(
            ["git", "--literal-pathspecs", *args], cwd=workspace, capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout if result.returncode == 0 else ""


def require(workspace, package, *, committed) -> None:
    """Refuse a package the run would not find.

    A new task worktree starts from the committed HEAD, so its package must hold committed files;
    an in-place run uses the checkout as it is, where any file Git does not ignore counts.
    """
    if committed:
        listing = _listing(workspace, "ls-tree", "-r", "-z", "--name-only", "HEAD", "--", package + "/")
        where = "committed files (a new task worktree starts from HEAD)"
    else:
        if directory_in(workspace, package) is None:
            raise ValueError(f"--package {package}/ is not a directory of {workspace}; a symlink does not count")
        listing = _listing(workspace, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", package)
        where = "files Git does not ignore"
    if not listing.strip("\0"):
        raise ValueError(f"--package {package}/ holds no {where} in {workspace}")


def command(package, text) -> str:
    """``text`` run from the package directory."""
    return f"cd {shlex.quote(package)} && {text}"


def inner(package, text) -> str | None:
    """The command ``command(package, ...)`` wraps, or None when ``text`` does not enter the package."""
    prefix = command(package, "")
    if isinstance(text, str) and text.startswith(prefix) and text[len(prefix) :].strip():
        return text[len(prefix) :]
    return None


def split(framework, text):
    """(package, command run there) for a packaged framework's command; (None, text) for any other."""
    package = getattr(framework, "directory", None)
    rest = inner(package, text) if package else None
    return (package, rest) if rest is not None else (None, text)


def contains(package, path) -> bool:
    """True when ``path`` (repository-relative) is strictly inside the package."""
    return str(path).startswith(package + "/")


def inside(package, paths) -> list[str]:
    """Paths strictly inside the package, relative to it."""
    return test_roots.inside(package, paths)


def outside(package, paths) -> list[str]:
    """Paths not inside the package, sorted."""
    return test_roots.outside(package, paths)


class PackageFramework(Framework):
    """A suite detected inside one package. Its commands enter the package directory first."""

    def __init__(self, found: Framework, package: str):
        super().__init__(
            found.name,
            command(package, found.suite),
            python=found.python,
            runner=found.runner,
            note=found.note,
            node_files=found.node_files,
            invocation=found.invocation,
        )
        self.inner, self.directory = found, package

    def targeted(self, test_paths):
        paths = inside(self.directory, test_paths)
        selected = self.inner.targeted(paths) if paths else None
        return command(self.directory, selected) if selected else None

    def to_dict(self):
        return {**super().to_dict(), "directory": self.directory}


def detect(root, package, detect_in) -> PackageFramework | None:
    """``detect_in(directory)`` run on the package directory as if it were the project root."""
    directory = directory_in(root, package)
    found = detect_in(directory) if directory is not None else None
    return PackageFramework(found, package) if found is not None else None
