"""One package of a monorepo as the scope of a run (#711).

``--package PATH`` names a directory of the repository when a run starts. The run saves it in
``settings.package`` and never changes it. Every reader asks this module:

- suite detection looks only inside the package, and the detected suite runs from the package
  directory: its command is ``cd PATH && SUITE``, so the command a person reads in a receipt is
  the command that ran, from the repository root, in every scratch copy;
- the protected-test inventory keeps only the package's tests, and their replay reads the saved
  package command's per-test results inside the package (``recognize``);
- the regression proof leaves a change outside the package unverified (``uncovered``), and in every
  package run a change outside it is a runner finding (``changes_outside``, read by
  autocode_package_writes); neither counts the paths autocode_package_writes.exempt names.

An explicit ``--test-command`` still runs from the repository root, exactly as given. Written as
``cd PATH && RUNNER`` for the run's own package, the proof reads it with that runner inside the
package (``own``).

Mostly helpers over command strings and repository-relative paths. Two read the checkout:
``directory_in`` checks each step of the path with ``lstat``, and ``require`` lists the package's
files with ``git ls-tree`` or ``git ls-files``. The rest is the framework wrapper the detection
returns. It imports only the test-root path rules and the test-runner descriptions, never the
controller or the proof.
"""

from __future__ import annotations

import os
import shlex
import stat
import subprocess
from pathlib import Path, PurePosixPath

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


def entered(text) -> tuple[str, str] | None:
    """(package, command run there) for a command in exactly the form ``command`` writes, else None."""
    if not isinstance(text, str) or not text.startswith("cd "):
        return None
    head, separator, _ = text[3:].partition(" && ")
    if not separator:
        return None
    try:
        package = normalize(head)
    except ValueError:
        return None
    rest = inner(package, text)
    return (package, rest) if rest is not None else None


def within(text):
    """The command ``text`` runs inside its package when it is in the form ``command`` writes; else ``text``."""
    found = entered(text)
    return found[1] if found else text


def recognize(text, recognize_inner) -> PackageFramework | None:
    """The framework of a command in exactly the form ``command`` writes (a replay has only the text).

    ``recognize_inner`` is autocode_verify.command_framework, applied to the command run inside the
    directory, so the per-test result checks apply as they do to the same command at the root. A
    scratch run reads any command this way, in a run with or without a package.
    """
    found = entered(text)
    if found is None:
        return None
    framework = recognize_inner(found[1])
    return PackageFramework(framework, found[0]) if framework is not None else None


def own(settings, text, recognize_inner) -> PackageFramework | None:
    """The framework of an explicit command written for the run's own package as ``cd PACKAGE && RUNNER``.

    At the root a recognised explicit command chooses the proof's result parser; this gives the same
    choice to that command written for the package, read inside it. A command that enters another
    directory, and any command in a run of the whole repository, gets None: the proof reads per-test
    ids relative to the run's package (``ids_root``).
    """
    framework = recognize(text, recognize_inner)
    return framework if framework is not None and framework.directory == of(settings) else None


def ids_root(root, package, command) -> Path:
    """The directory ``command``'s per-test ids are relative to: the package when it enters it."""
    return Path(root) / package if package and inner(package, command) is not None else Path(root)


def contains(package, path) -> bool:
    """True when ``path`` (repository-relative) is strictly inside the package."""
    return str(path).startswith(package + "/")


def inside(package, paths) -> list[str]:
    """Paths strictly inside the package, relative to it."""
    return test_roots.inside(package, paths)


def outside(package, paths) -> list[str]:
    """Paths not inside the package, sorted."""
    return test_roots.outside(package, paths)


def named_outside(package, paths) -> list[str]:
    """The ``paths`` a report names that are not inside the package, sorted. Each is read as a plain
    repository-relative path (``./`` and a trailing slash dropped), and the package directory itself is
    inside the package."""
    return sorted(
        {path for path in paths if (name := PurePosixPath(path).as_posix()) != package and not contains(package, name)}
    )


def changes_outside(package, changes, exempt=()) -> dict[str, str]:
    """The changes not inside the package, by path, in path order.

    ``changes`` maps each changed path to ``added``, ``modified`` or ``deleted``, as
    autocode_verify.changed_files reports a candidate: AutoCode's run state, bytecode and the
    runner's dependency links are already left out, and a rename is a deleted and an added path, so a
    rename into or out of the package leaves its outside side here. A file at the package path itself
    is outside it. Each ``exempt`` path is left out with everything under it: a nested repository
    (``dir/``) or a submodule is reported as itself or as the files it holds.
    """
    covered = [root for root in (str(path).rstrip("/") for path in exempt) if root]
    return {
        path: changes[path]
        for path in outside(package, changes)
        if not any(path == root or path.startswith(root + "/") for root in covered)
    }


def uncovered(package, changes, exempt=()) -> str | None:
    """The regression proof's note on the changes outside the package, which its suite never ran, naming
    the first ten; None when there are none."""
    elsewhere = list(changes_outside(package, changes, exempt))
    if not elsewhere:
        return None
    return f"Files outside the package {package}/ changed, and its suite does not cover them: " + ", ".join(
        elsewhere[:10]
    )


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
            setup=found.setup,
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


# Files that make a directory a project of its own, with a suite detection can find there.
MANIFESTS = ("package.json", "go.mod", "Cargo.toml", "pyproject.toml", "setup.py", "setup.cfg", "Gemfile")


def enclosing(root, tests, detect_in, *, runs=None) -> PackageFramework | None:
    """The suite of the innermost project directory below ``root`` that holds every one of ``tests``
    and runs them all, or None. ``runs(found)`` judges that instead of ``found.targeted(tests)``: before
    the tests exist, a plan's check judges them by their kind (autocode_plan_test_suite).

    A run of the whole repository detects one suite at its root. When that suite cannot run the
    tests (a Node package inside a Python repository, 2026-11-11), the tests belong to the project
    around them: this finds it the way ``--package`` would have named it, with the same commands
    (``cd PACKAGE && RUNNER``). Only a directory with its own manifest counts, never the root.
    """
    tests = [str(PurePosixPath(test)) for test in tests]
    if not tests:
        return None
    common = os.path.commonpath([str(PurePosixPath(test).parent) for test in tests])
    parts = PurePosixPath(common).parts if common not in ("", ".") else ()
    for end in range(len(parts), 0, -1):
        package = "/".join(parts[:end])
        directory = directory_in(root, package)
        if directory is None or not any((directory / name).is_file() for name in MANIFESTS):
            continue
        found = detect(root, package, detect_in)
        if found is not None and (runs(found) if runs else found.targeted(tests)):
            return found
    return None


def for_tests(root, framework, tests, detect_in) -> PackageFramework | None:
    """The suite of the project around ``tests`` when ``framework``, detected at the root, runs none of
    them, or when the root is no project of its own (``enclosing``), else None. A Node package's tests in a
    Python repository were handed to the root's pytest, which cannot run them, and the run paused
    (2026-11-11). A repository whose root has no manifest holds projects side by side: Docutils keeps its
    project in ``docutils/`` beside old experiments in ``sandbox/``, and the root's suite collected those
    too, whose Python 2 modules never load, so preservation could not be shown (#2944)."""
    tests = sorted(tests)
    if not tests:
        return None
    if framework is not None and framework.targeted(tests) and _is_project(Path(root)):
        return None
    return enclosing(root, tests, detect_in)


def _is_project(directory: Path) -> bool:
    return any((directory / name).is_file() for name in MANIFESTS)


def link_node_modules(tree, roots) -> None:
    """Link each package's own ``node_modules`` (beside a tracked ``package.json`` below the root) into the
    scratch ``tree``, from the first of ``roots`` that has it: a task worktree starts without the ignored
    directory its main checkout installed. The root's own is autocode_verify.link_dependencies'."""
    listed = _listing(tree, "ls-files", "-z").split("\0")
    for package in sorted(
        {str(PurePosixPath(path).parent) for path in listed if PurePosixPath(path).name == "package.json"}
    ):
        target = Path(tree) / package / "node_modules"
        if package == "." or target.exists() or target.is_symlink() or not target.parent.is_dir():
            continue
        source = next(
            (
                Path(root) / package / "node_modules"
                for root in roots
                if (Path(root) / package / "node_modules").is_dir()
            ),
            None,
        )
        if source is not None:
            target.symlink_to(source.resolve(), target_is_directory=True)
