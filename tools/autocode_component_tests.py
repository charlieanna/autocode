"""Collect whole-project unittest suites without writing component package markers.

Isolate component imports in fresh interpreters; real packages keep load_tests.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.machinery
import subprocess
import sys
import types
import unittest
from pathlib import Path

try:
    from .autocode_python_tests import TEST_MODULE
except ImportError:
    from autocode_python_tests import TEST_MODULE


class ComponentLoader(unittest.TestLoader):
    def __init__(self, root, component=None):
        super().__init__()
        self.root, self.component = root, component

    def _match_path(self, path, full_path, pattern):
        return bool(TEST_MODULE.match(path))

    def _find_test_path(self, full_path, pattern, *namespace_args):
        path = Path(full_path)
        if self.component is None and path == self.root / "components":
            return None, False
        if (self.component is not None and path.is_dir() and path.is_relative_to(self.component)
                and not (path / "__init__.py").exists()):
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", action="store_true")
    parser.add_argument("--components", nargs="*")
    parser.add_argument("--component")
    parser.add_argument("--component-root", action="store_true")
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)
    root = Path.cwd()
    if args.components is not None and not args.component_root:
        code = 0
        for extra in (["--root", args.root], ["--component-root", "--components", *args.components]):
            result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "-v", *extra])
            code = max(code, result.returncode)
        return code
    component = (root / "components" / args.component if args.component else
                 root / "components" if args.component_root else None)
    loader = ComponentLoader(root, component)
    if component is not None:
        if not component.is_dir():
            parser.error(f"component directory is missing: {component}")
        sys.path[:0] = [str(component), str(component / "src"), str(root)]
        if (root / "components" / "__init__.py").exists():
            importlib.import_module("components")
        else:
            namespace("components", root / "components")
        package = sys.modules["components"]
        if args.component_root and getattr(package, "load_tests", None) is None:
            suite = loader.loadTestsFromModule(package, pattern="test*.py")
            code = 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
            for cid in args.components:
                result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "-v", "--component", cid])
                code = max(code, result.returncode)
            return code
        if args.component and not (component / "__init__.py").exists():
            namespace(f"components.{args.component}", component)
        # discover rejects an unmarked start directory; traversal retains package hooks.
        loader._top_level_dir = str(root)
        suite = unittest.TestSuite(loader._find_tests(str(component), "test*.py"))
    else:
        suite = loader.discover(str(root / args.root), pattern="test*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
