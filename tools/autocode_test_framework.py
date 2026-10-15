"""Runner descriptions shared by proof adapters, without controller dependencies."""

from __future__ import annotations

import re
import shlex
from pathlib import PurePosixPath

try:
    from . import autocode_python_tests as python_tests
except ImportError:
    import autocode_python_tests as python_tests

PYTHON_TEST_MODULE = python_tests.TEST_MODULE


class Framework:
    """How to run the whole suite and a targeted subset for one project."""

    def __init__(
        self, name, suite, *, python=None, runner=None, note="", node_files=(), invocation=None, test_root=None
    ):
        self.name, self.suite, self.python, self.runner, self.note = name, suite, python, runner, note
        self.node_files = frozenset(node_files)
        self.invocation = invocation
        # A scope descriptor, not authority for the first-root exception in verify().
        self.test_root = test_root

    @property
    def per_test(self):
        return self.name in ("pytest", "unittest", "go", "node", "vitest")

    def targeted(self, test_paths):
        files = sorted(test_paths)
        if self.name == "unittest" and "autocode_component_tests.py" in self.suite:
            # Targeting files directly would bypass package load_tests hooks.
            return self.suite if any(PYTHON_TEST_MODULE.match(PurePosixPath(p).name) for p in files) else None
        if self.name == "node":
            if self.invocation:
                scripts = [
                    p for p in files if PurePosixPath(p).suffix in (".js", ".mjs", ".cjs", ".ts", ".mts", ".cts")
                ]
                return self.invocation.targeted(scripts) if scripts else None
            scripts = [p for p in files if p in self.node_files]
            return "node --test " + " ".join(map(shlex.quote, scripts)) if scripts else None
        if self.invocation:
            modules = [p for p in files if PYTHON_TEST_MODULE.match(PurePosixPath(p).name)]
            return self.invocation.targeted(modules) if modules else None
        if self.name == "pytest":
            modules = [p for p in files if PYTHON_TEST_MODULE.match(PurePosixPath(p).name)]
            return (
                (f"{shlex.quote(self.python)} -m pytest -q -p no:cacheprovider " + " ".join(map(shlex.quote, modules)))
                if modules
                else None
            )
        if self.name == "unittest":
            modules = [p for p in files if PYTHON_TEST_MODULE.match(PurePosixPath(p).name)]
            return (
                (f"{shlex.quote(self.python)} -m unittest -v " + " ".join(map(shlex.quote, modules)))
                if modules
                else None
            )
        if self.name == "go":
            packages = sorted(
                {
                    "./" + str(PurePosixPath(p).parent) if str(PurePosixPath(p).parent) != "." else "."
                    for p in files
                    if p.endswith("_test.go")
                }
            )
            return ("go test " + " ".join(map(shlex.quote, packages))) if packages else None
        if self.name in ("jest", "vitest", "mocha"):
            scripts = [p for p in files if re.search(r"\.(test|spec)\.[cm]?[jt]sx?$", p) or p.endswith((".js", ".ts"))]
            if not scripts:
                return None
            verb = {"jest": "jest", "vitest": "vitest run", "mocha": "mocha"}[self.name]
            return f"npx --no-install {verb} " + " ".join(map(shlex.quote, scripts))
        if self.name == "rspec":
            specs = [p for p in files if p.endswith("_spec.rb")]
            return (f"{self.runner} " + " ".join(map(shlex.quote, specs))) if specs else None
        return None

    def to_dict(self):
        return {"name": self.name, "suite": self.suite, "python": self.python, "note": self.note}
