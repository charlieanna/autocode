"""Collect whole-project unittest suites without writing component package markers.

One family algorithm serves both families (#2414): import the family's parent
package (real or namespace-bootstrapped; a broken parent fails loudly), let a
parent ``load_tests`` — defined, imported or aliased, decided by import — own the
family's collection, and otherwise run the parent's own TestCases once here plus
every member in its own isolated interpreter with the member directory first on
sys.path, so identical module names across members never collide. The workspace's
own tests run exactly once per whole-suite invocation, never once per family.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.machinery
import subprocess
import sys
import types
import unittest
from dataclasses import dataclass
from pathlib import Path

try:
    from .autocode_python_tests import TEST_MODULE
except ImportError:
    from autocode_python_tests import TEST_MODULE

FAMILIES = ("components", "repos")


@dataclass(frozen=True)
class Selection:
    """One selection carried through every isolated collection pass."""

    patterns: tuple[str, ...] = ()
    failfast: bool = False

    def loader(self, root, component=None, families=()):
        loader = ComponentLoader(root, component, families)
        loader.testNamePatterns = list(self.patterns) or None
        return loader

    def run(self, suite):
        result = unittest.TextTestRunner(verbosity=2, failfast=self.failfast).run(suite)
        return 0 if result.wasSuccessful() else 1

    def fork(self, argv):
        selection = [word for pattern in self.patterns for word in ("-k", pattern)]
        return subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "-v",
                *(["-f"] if self.failfast else []),
                *selection,
                *argv,
            ]
        ).returncode


class ComponentLoader(unittest.TestLoader):
    def __init__(self, root, component=None, families=()):
        super().__init__()
        self.root, self.component = root, component
        # Only the families scheduled for this whole-suite run are excluded from a root
        # pass: an undispatched family-shaped directory keeps ordinary discovery
        # semantics, so its import failure cannot be hidden by a green family (#2414).
        self.excluded = frozenset(families)

    def _match_path(self, path, full_path, pattern):
        return bool(TEST_MODULE.match(path))

    def _find_test_path(self, full_path, pattern, *namespace_args):
        path = Path(full_path)
        # A scheduled family directory is owned by its family passes; the root pass
        # never traverses it, so no member's tests run twice in one whole-suite run.
        if self.component is None and path in [self.root / family for family in self.excluded]:
            return None, False
        if (
            self.component is not None
            and path.is_dir()
            and path.is_relative_to(self.component)
            and not (path / "__init__.py").exists()
        ):
            name = self._get_name_from_path(full_path)
            namespace(name, path)
            return self.loadTestsFromModule(sys.modules[name], pattern=pattern), True
        return super()._find_test_path(full_path, pattern, *namespace_args)


def namespace(name, path):
    if name in sys.modules:
        return
    module = types.ModuleType(name)
    module.__path__ = [str(path)]
    module.__package__ = name
    module.__spec__ = importlib.machinery.ModuleSpec(name, loader=None, is_package=True)
    sys.modules[name] = module


def parent_package(root, kind):
    """The family's parent package module: imported when real, namespace-bootstrapped when not."""
    if (root / kind / "__init__.py").is_file():
        return importlib.import_module(kind)
    namespace(kind, root / kind)
    return sys.modules[kind]


def import_family(root, kind):
    """Import the family's parent with the family directory itself on sys.path."""
    parent = root / kind
    sys.path[:0] = [str(parent), str(parent / "src"), str(root)]
    return parent_package(root, kind)


def discover(root, directory, families=(), *, selection=Selection()):
    """One discovery with its own loader; only scheduled families are excluded."""
    return selection.loader(root, families=families).discover(str(directory), pattern="test*.py")


def root_suite(loader, root, selected):
    """A selected directory exactly as the detector bound it (legacy ``-s`` semantics)."""
    return loader.discover(str(root / selected), pattern="test*.py")


def whole_root_suite(root, selected, families=(), *, selection=Selection()):
    """The whole-suite command's root pass; the binding is made once, on the base tree.

    A selected directory keeps the exact ``-s`` semantics the previous binding had. The
    joint workspace binds ``.`` before the Builder writes its root tests, so ``.`` adds
    every MARKERLESS ``tests``/``test`` directory the Builder later creates — a marked
    package never routes through this fallback: root discovery already descends it and
    invokes its ``load_tests``, preserving identities and hook ownership (#2414). Only
    the scheduled family directories are excluded; a selected directory missing on the
    base simply contributes no root tests there instead of erroring.
    """
    suite = unittest.TestSuite()
    if selected != ".":
        if (root / selected).is_dir():
            suite.addTests(discover(root, root / selected, families, selection=selection))
        return suite
    suite.addTests(discover(root, root, families, selection=selection))
    for name in ("tests", "test"):
        markerless = root / name
        if markerless.is_dir() and not (markerless / "__init__.py").is_file():
            suite.addTests(discover(root, markerless, families, selection=selection))
    return suite


def member_suite(loader, root, kind, member):
    """One member's isolated suite: namespace-bootstrapped when unmarked, load_tests kept when real."""
    directory = root / kind / member
    if not directory.is_dir():
        raise SystemExit(f"{kind} directory is missing: {directory}")
    sys.path[:0] = [str(directory), str(directory / "src"), str(root)]
    parent_package(root, kind)
    if not (directory / "__init__.py").exists():
        namespace(f"{kind}.{member}", directory)
    # discover rejects an unmarked start directory; traversal retains package hooks.
    loader._top_level_dir = str(root)
    return unittest.TestSuite(loader._find_tests(str(directory), "test*.py"))


def family_root(root, kind, members, *, selection=Selection()):
    """The one family algorithm, in this isolated process (the components semantics).

    A parent ``load_tests`` found on the imported package owns the family's collection;
    without one, the parent's own TestCases run once here and each member then runs in
    its own isolated interpreter. A broken real parent fails loudly rather than
    counting as "no hook": a green workspace must never hide that failure.
    """
    parent = root / kind
    if not parent.is_dir():
        raise SystemExit(f"{kind} directory is missing: {parent}")
    loader = selection.loader(root, parent)
    package = import_family(root, kind)
    if getattr(package, "load_tests", None) is None:
        code = selection.run(loader.loadTestsFromModule(package, pattern="test*.py"))
        for member in members:
            if code and selection.failfast:
                break
            code = max(code, selection.fork(["--member", kind, member]))
        return code
    # discover rejects an unmarked start directory; traversal retains package hooks.
    loader._top_level_dir = str(root)
    return selection.run(unittest.TestSuite(loader._find_tests(str(parent), "test*.py")))


def orchestrate(root, selected, components, repos, packages=(), *, selection=Selection()):
    """The whole suite: the workspace's own tests and each scheduled family exactly once.

    The family flags travel with the root pass fork so the root pass excludes exactly
    the scheduled families — never an undispatched family-shaped directory, whose
    ordinary discovery semantics (including its import failures) stay in force.
    """
    spec = ["--root", selected]
    if components is not None:
        spec += ["--components", *components]
    if repos is not None:
        spec += ["--repos", *repos]
    for folder, *members in packages:
        spec += ["--package-family", folder, *members]
    commands = [["--whole-root", *spec]]
    if components is not None:
        commands.append(["--family-root", "components", "--members", *components])
    if repos is not None:
        commands.append(["--family-root", "repos", "--members", *repos])
    # A monorepo package folder (autocode_python_tests.PACKAGE_DIRS): each package with tests runs
    # isolated, from its own directory first on sys.path, exactly as a component does.
    for folder, *members in packages:
        commands.append(["--family-root", folder, "--members", *members])
    code = 0
    for command in commands:
        code = max(code, selection.fork(command))
        if code and selection.failfast:
            break
    return code


def discard_script_directory():
    """The collector's own directory is not a project-module search path (#2456).

    The detected suite command runs this file by absolute path, so Python puts this
    directory first on sys.path — ahead of the workspace roots the runner supplies
    on PYTHONPATH — and unittest discovery does not move a root already there. A
    workspace module named like a sibling here (``run_suite`` is one) then imported
    AutoCode's file instead of the project's, and with no project file at all it
    still imported AutoCode's instead of failing. The bootstrap imports above have
    already resolved, so every sys.path entry for this directory now goes: the
    workspace's intended precedence stands and a genuinely missing module fails
    honestly. An entry that is itself the tested workspace — the collector run from
    inside its own checkout — stays: that root is the workspace's legitimate path,
    and discovery and the family and member passes establish exactly it anyway.
    """
    here = Path(__file__).resolve().parent
    workspace = Path.cwd().resolve()
    kept = {workspace, workspace / "src"}

    def own(entry):
        resolved = Path(entry).resolve()
        return resolved == here and resolved not in kept

    sys.path[:] = [entry for entry in sys.path if not own(entry)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", action="store_true")
    parser.add_argument("-k", action="append", default=[], help="Select observed test IDs (unittest patterns)")
    parser.add_argument("-f", "--failfast", action="store_true")
    parser.add_argument("--components", nargs="*")
    parser.add_argument("--component")
    parser.add_argument("--component-root", action="store_true")
    parser.add_argument("--repos", nargs="*")
    parser.add_argument("--member", nargs=2, metavar=("FAMILY", "NAME"))
    parser.add_argument("--family-root")
    parser.add_argument("--members", nargs="*")
    parser.add_argument("--whole-root", action="store_true")
    parser.add_argument("--root", default=".")
    parser.add_argument("--package-family", nargs="+", action="append", default=[], metavar=("FOLDER", "MEMBER"))
    args = parser.parse_args(argv)
    selection = Selection(tuple(args.k), args.failfast)
    packages = [tuple(group) for group in args.package_family]
    root = Path.cwd()
    if args.component is not None:
        args.member = ["components", args.component]
    if args.component_root:
        args.family_root, args.members = "components", args.components or []
    if args.family_root is not None:
        return family_root(root, args.family_root, args.members or [], selection=selection)
    if args.member is not None:
        kind, name = args.member
        loader = selection.loader(root, root / kind / name)
        return selection.run(member_suite(loader, root, kind, name))
    if args.whole_root:
        scheduled = tuple(
            name
            for name, scheduled in (("components", args.components is not None), ("repos", args.repos is not None))
            if scheduled
        ) + tuple(folder for folder, *_ in packages)
        return selection.run(whole_root_suite(root, args.root, scheduled, selection=selection))
    whole_suite = (
        (args.components is not None and not args.component_root)
        or (args.repos is not None and args.component is None)
        or bool(packages)
    )
    if whole_suite:
        return orchestrate(root, args.root, args.components, args.repos, packages, selection=selection)
    suite = root_suite(
        selection.loader(
            root,
            families=tuple(
                name
                for name, scheduled in (
                    ("components", args.components is not None),
                    ("repos", args.repos is not None),
                )
                if scheduled
            ),
        ),
        root,
        args.root,
    )
    return selection.run(suite)


if __name__ == "__main__":
    discard_script_directory()
    raise SystemExit(main())
