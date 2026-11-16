"""Pure domain file-role classification, result instrumentation, and test-output parsing (#3468).

Operates strictly over paths, framework descriptions, receipts, and output files;
never spawns subprocesses or scratch worktrees.
"""

from __future__ import annotations

import re
import shlex
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

try:
    from . import autocode_dependency_dirs as dependency_dirs
    from . import autocode_go_tests as go_tests
    from . import autocode_named_reporters as reporters
    from . import autocode_package_scope as package_scope
    from . import autocode_project_kinds as project_kinds
    from . import autocode_python_tests as python_tests
    from . import autocode_test_role_inventory as role_inventory
    from . import autocode_test_setup as test_setup
    from . import autocode_vitest_tests as vitest_tests
    from .autocode_verification_commands import _go_test
except ImportError:
    import autocode_dependency_dirs as dependency_dirs
    import autocode_go_tests as go_tests
    import autocode_named_reporters as reporters
    import autocode_package_scope as package_scope
    import autocode_project_kinds as project_kinds
    import autocode_python_tests as python_tests
    import autocode_test_role_inventory as role_inventory
    import autocode_test_setup as test_setup
    import autocode_vitest_tests as vitest_tests
    from autocode_verification_commands import _go_test

CODE_SUFFIXES = project_kinds.CODE_SUFFIXES


def is_test_path(path: str) -> bool:
    """True for files that belong to the test suite rather than the product (autocode_project_kinds)."""
    return project_kinds.is_test_path(path)


def is_code_path(path: str) -> bool:
    """True for source/test files whose suffix matches a supported programming language."""
    return PurePosixPath(path).suffix in CODE_SUFFIXES


def product_files(changes, *, roles=None, source_files=None) -> list[tuple[str, str]]:
    """The changed product code (not tests, deletions or installed dependencies, #3164) diff coverage and mutants read."""
    return [
        (path, status)
        for path, status in changes.items()
        if status != "deleted"
        and is_code_path(path)
        and not dependency_dirs.installed(path)
        and (
            path in source_files
            if source_files is not None
            else not (roles.is_test(path) if roles else is_test_path(path))
        )
    ]


def removed_python_tests(workspace, base, changes, *, git, roles=None, replaced=None) -> list[str]:
    """Names of ``def test*`` functions deleted from modified Python test files, except the tests the approved
    plan replaces (``replaced``, a test of an id)."""
    roles = roles or role_inventory.from_git(workspace, base, is_test_path, changes=changes)
    removed = []
    pattern = re.compile(r"^\s*(?:async\s+)?def\s+(test\w*)\s*\(", re.M)
    for path, status in changes.items():
        if not (path.endswith(".py") and roles.protected(path)) or status == "added":
            continue
        before = set(pattern.findall(git(workspace, "show", f"{base}:{path}", check=False)))
        after_file = Path(workspace) / path
        after = set(pattern.findall(after_file.read_text(errors="replace"))) if after_file.is_file() else set()
        removed += [f"{path}::{name}" for name in sorted(before - after)]
    return [name for name in removed if not (replaced and replaced(name))]


def with_results(framework, command, xml_path, tree=None, *, scripts=True):
    """``command`` asking its runner for per-test results. ``scripts`` false leaves an ``npm test`` whose
    script is node's own test runner as written (a native check runs exactly the command it planned)."""
    if instrumented := reporters.instrument(command, xml_path, tree if scripts else None, tree):
        return instrumented
    if vitest_tests.command_words(command, tree):
        return vitest_tests.instrument(command, xml_path, tree)
    if framework and framework.name == "pytest" and " -m pytest" in command:
        return f"{command} --junitxml={shlex.quote(str(xml_path))}"
    if framework and framework.name == "go" and (invocation := go_tests.parse(command)):
        # Go reports per-test results only as `go test -json` events (a live Go port could not be
        # proven without them, 2026-09-29). The flag goes before the packages, where go test reads it.
        return invocation.instrument()
    # Unittest names each test only at -v. A quiet or default command still runs
    # the same tests; the proof needs those names to tell a passing baseline
    # from a count with no identities.
    return python_tests.verbose_unittest(command)


_with_results = with_results


def expects_results(framework, command, tree=None, *, scripts=True):
    """True when this command, run by the runner, must yield per-test results."""
    if framework is not None and framework.name == "junit":
        return bool(command)
    if reporters.reports(command, tree if scripts else None, tree):
        return True
    if vitest_tests.command_words(command, tree):
        return True
    if not framework or not framework.per_test or not command:
        return False
    if framework.name == "go":
        return _go_test(command)
    return (
        (" -m pytest" in command)
        if framework.name == "pytest"
        else ((" -m unittest" in command or "autocode_component_tests.py" in command) and " -v" in command)
    )


def per_test_results(
    framework,
    receipt,
    xml_path,
    *,
    tree=None,
    go_results=go_tests.results,
    unittest_results=python_tests.unittest_results,
) -> dict | None:
    """Passed, failed and skipped test ids, or None when the run produced no parseable results.

    ``collection_errors`` are failures of a module to import or collect; they are
    failures, but they never name a test that ran. ``uncollected`` is the subset
    that never imported, collected or built at all: a failed hook or fixture that
    Node and Vitest report as a collection error executed and is judged like any
    other failing test, so it is not in ``uncollected``.
    """
    if read := reporters.reader(xml_path, receipt, tree):
        return read(xml_path)
    if str(xml_path).endswith(".vitest.json"):
        return vitest_tests.results(xml_path, tree)
    if not framework or not framework.per_test or framework.name not in ("pytest", "unittest", "go"):
        return None
    if framework.name == "go":
        invocation = go_tests.parse(package_scope.split(framework, receipt.get("command") or framework.suite)[1])
        if invocation is None or invocation.json_disabled:
            return None
        output = receipt.get("output")
        return go_results(Path(output).read_text(errors="replace")) if output and Path(output).is_file() else None
    passed, failed, skipped, collection = set(), set(), set(), set()
    setup_errors = {}
    roles = getattr(framework, "test_roles", None)
    is_test = (lambda path: roles.role(path) == "test") if roles else is_test_path
    if framework.name == "pytest":
        if not Path(xml_path).is_file():
            return None
        try:
            result_tree = ET.parse(xml_path)
        except ET.ParseError:
            return None
        for test, case in python_tests.pytest_case_names(result_tree.iter("testcase"), tree):
            problem = case.find("failure") if case.find("failure") is not None else case.find("error")
            if problem is not None:
                failed.add(test)
                reason = test_setup.setup_error(problem.text or "", tree, is_test)
                if reason:
                    setup_errors[test] = reason
                if not case.get("classname") or "collection failure" in (problem.get("message") or ""):
                    collection.add(test)
            elif case.find("skipped") is not None:
                skipped.add(test)
            else:
                passed.add(test)
        # A test that fails and then errors in teardown is two testcases of one id; a test reported
        # with two different outcomes stays a duplicate id, never a complete result.
        total = len(passed | failed | skipped)
        complete = True
    else:
        text = Path(receipt["output"]).read_text(errors="replace")
        return unittest_results(
            text,
            component="autocode_component_tests.py" in framework.suite,
            classify_setup=lambda detail: test_setup.setup_error(detail, tree, is_test),
        )
    results = {
        "passed": sorted(passed),
        "failed": sorted(failed),
        "skipped": sorted(skipped),
        "collection_errors": sorted(collection),
        "uncollected": sorted(collection),
        "total": total,
        "complete": complete,
    }
    if setup_errors:
        results["setup_errors"] = setup_errors
    return results
