"""Runner descriptions shared by proof adapters, without controller dependencies."""

from __future__ import annotations

import re
import shlex
from pathlib import PurePosixPath

try:
    from . import autocode_project_kinds as project_kinds
    from . import autocode_python_tests as python_tests
except ImportError:
    import autocode_project_kinds as project_kinds
    import autocode_python_tests as python_tests

PYTHON_TEST_MODULE = python_tests.TEST_MODULE
# The test files each targeted runner is given, from its language's row (autocode_project_kinds).
GO_TEST = project_kinds.targets("go")
JS_TEST = project_kinds.targets("jest")
RSPEC_TEST = project_kinds.targets("rspec")
MINITEST_TEST = re.compile(r"^(?:.*_test|test_.*)\.rb$")


class Framework:
    """How to run the whole suite and a targeted subset for one project."""

    test_roles: object

    def __init__(
        self,
        name,
        suite,
        *,
        python=None,
        runner=None,
        note="",
        node_files=(),
        invocation=None,
        test_root=None,
        setup=None,
    ):
        self.name, self.suite, self.python, self.runner, self.note = name, suite, python, runner, note
        # The project's own build steps a targeted test command needs first (autocode_node_tests.script_steps);
        # run_suite runs them before it, never before the whole suite, whose own command builds.
        self.setup = setup
        self.node_files = frozenset(node_files)
        self.invocation = invocation
        # A scope descriptor, not authority for the first-root exception in verify().
        self.test_root = test_root

    @property
    def per_test(self):
        return self.name in project_kinds.PER_TEST_RUNNERS

    def targeted(self, test_paths):
        files = sorted(test_paths)
        if self.name == "cargo":
            # Cargo builds every test target of the package together, so its changed test files run in
            # the suite itself; the proof reads that run's results per test (autocode_cargo_tests).
            return self.suite if any(p.endswith(".rs") for p in files) else None
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
        if self.name == "go":
            packages = sorted(
                {
                    "./" + str(PurePosixPath(p).parent) if str(PurePosixPath(p).parent) != "." else "."
                    for p in files
                    if GO_TEST.match(PurePosixPath(p).name)
                }
            )
            if not packages:
                return None
            # Keep the approved command's env prefix (#3091); parse() reads it, no second parser.
            prefix = list(self.invocation.prefix) if self.invocation else []
            return shlex.join([*prefix, "go", "test", *packages])
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
        if self.name in ("jest", "vitest", "mocha"):
            scripts = [p for p in files if JS_TEST.match(PurePosixPath(p).name) or p.endswith((".js", ".ts"))]
            if not scripts:
                return None
            verb = {"jest": "jest", "vitest": "vitest run", "mocha": "mocha"}[self.name]
            return f"npx --no-install {verb} " + " ".join(map(shlex.quote, scripts))
        if self.name == "rspec":
            specs = [p for p in files if RSPEC_TEST.match(PurePosixPath(p).name)]
            return (f"{self.runner} " + " ".join(map(shlex.quote, specs))) if specs else None
        if self.name == "minitest":
            scripts = [
                p
                for p in files
                if MINITEST_TEST.match(PurePosixPath(p).name) and PurePosixPath(p).name != "test_helper.rb"
            ]
            if not scripts:
                return None
            prefix = ["bundle", "exec"] if str(self.suite or "").startswith("bundle exec ") else []
            if len(scripts) == 1:
                return shlex.join([*prefix, "ruby", "-Ilib", "-Itest", scripts[0]])
            requires = [arg for script in scripts for arg in ("-r", "./" + script.lstrip("./"))]
            return shlex.join([*prefix, "ruby", "-Ilib", "-Itest", *requires, "-e", ""])
        return None

    def to_dict(self):
        shown = {"name": self.name, "suite": self.suite, "python": self.python, "note": self.note}
        return {**shown, "setup": self.setup} if self.setup else shown
