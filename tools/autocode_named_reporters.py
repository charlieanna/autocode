"""The runners whose own machine-readable reporter the proof adds to a command: node:test, RSpec and Jest.

Each reporter writes per-test results to a file in the proof's evidence, named with its extension, and
``results`` reads that file back. Cargo needs no reporter: the test harness names every test in the
command's own output, which ``reader`` reads from the run's receipt (autocode_cargo_tests). Kept here,
below autocode_verify, so a runner is added in one place.

Jest's report names each test file by its full path, and an ``npm test`` is Jest only by the package's
script, so its functions also take ``root``, the directory the command runs in. Its command is kept as
written with two options added, so ``root`` is given whether or not ``tree`` is.
"""

from __future__ import annotations

from pathlib import Path

try:
    from . import autocode_cargo_tests as cargo_tests
    from . import autocode_jest_tests as jest_tests
    from . import autocode_minitest_tests as minitest_tests
    from . import autocode_mocha_tests as mocha_tests
    from . import autocode_node_tests as node_tests
    from . import autocode_npm_scripts as npm_scripts
    from . import autocode_package_scope as package_scope
    from . import autocode_rspec_tests as rspec_tests
    from . import autocode_vitest_tests as vitest_tests
except ImportError:
    import autocode_cargo_tests as cargo_tests
    import autocode_jest_tests as jest_tests
    import autocode_minitest_tests as minitest_tests
    import autocode_mocha_tests as mocha_tests
    import autocode_node_tests as node_tests
    import autocode_npm_scripts as npm_scripts
    import autocode_package_scope as package_scope
    import autocode_rspec_tests as rspec_tests
    import autocode_vitest_tests as vitest_tests

NODE = "node.jsonl"
JEST = "jest.json"
MOCHA = "mocha.json"
VITEST = "vitest.json"
REPORTERS = ((NODE, node_tests), ("rspec.json", rspec_tests))
_CHAIN_RUNNERS = ((JEST, jest_tests), (VITEST, vitest_tests), (MOCHA, mocha_tests))


def _npm_chain(command, tree):
    """When ``command`` is a composite ``&&`` ``npm test`` with one supported JS runner step, return it."""
    steps = npm_scripts.safe_root_script(command, tree)
    if not steps:
        return None
    matches = []
    for idx, step in enumerate(steps):
        for ext, runner in _CHAIN_RUNNERS:
            if runner.command_words(step, tree):
                matches.append((ext, steps, idx, runner))
    return matches[0] if len(matches) == 1 else None


def extension(command, tree=None, root=None) -> str | None:
    """The result file extension of the reporter ``command`` runs with, or None for another command."""
    found = next((found for found, runner in REPORTERS if runner.command_words(command)), None)
    if found or (tree is not None and node_tests.npm_test(command, tree)):
        return found or NODE
    if jest_tests.command_words(command, root):
        return JEST
    if mocha_tests.command_words(command, root):
        return MOCHA
    if tree is not None and (chain := _npm_chain(command, tree)):
        return chain[0]
    return None


def reports(command, tree=None, root=None) -> bool:
    """True when ``command`` must yield per-test results: a reporter's file, Cargo, or Minitest."""
    return (
        extension(command, tree, root) is not None
        or cargo_tests.command_words(command) is not None
        or minitest_tests.command_words(command) is not None
    )


def suite_reports(framework, workspace=None) -> bool:
    """Whether ``framework``'s suite actually yields per-test results in ``workspace`` (not an opaque script)."""
    if framework is None or not framework.per_test:
        return False
    inner = getattr(framework, "inner", framework)
    package = getattr(framework, "directory", None)
    root = Path(workspace) / package if workspace and package else workspace
    if root is None or inner.name not in ("jest", "vitest", "mocha", "node"):
        return True
    if npm_scripts.test_script(inner.suite, root) is None:
        return True
    return reports(inner.suite, root, root) or bool(vitest_tests.command_words(inner.suite, root))


def instrument(command, result_path, tree=None, root=None) -> str | None:
    """``command`` writing its reporter's results to ``result_path``, or None for another command."""
    runner = next((runner for _, runner in REPORTERS if runner.command_words(command)), None)
    if runner:
        return runner.instrument(command, result_path)
    if tree is not None and (actual := node_tests.instrument_npm(command, result_path, tree)):
        return actual
    if jest_tests.command_words(command, root):
        return jest_tests.instrument(command, result_path, root)
    if mocha_tests.command_words(command, root):
        return mocha_tests.instrument(command, result_path, root)
    if tree is not None and (chain := _npm_chain(command, tree)):
        _, steps, idx, chain_runner = chain
        updated = [*steps[:idx], chain_runner.instrument(steps[idx], result_path, tree), *steps[idx + 1 :]]
        return npm_scripts.npm_exec_call(updated)
    if cargo_tests.command_words(command):
        return cargo_tests.instrument(command)
    if minitest_tests.command_words(command):
        return minitest_tests.instrument(command, result_path)
    return None


def reader(result_path, receipt=None, root=None):
    """The function that reads ``result_path``'s per-test results, or None when no reporter wrote it."""
    if str(result_path).endswith("." + JEST):
        return lambda path: jest_tests.results(path, root)
    if str(result_path).endswith("." + MOCHA):
        return lambda path: mocha_tests.results(path, root)
    if str(result_path).endswith("." + VITEST):
        return lambda path: vitest_tests.results(path, root)
    found = next((runner.results for found, runner in REPORTERS if str(result_path).endswith("." + found)), None)
    if found:
        return found
    within = package_scope.within((receipt or {}).get("command") or "")
    if cargo_tests.command_words(within):
        return lambda _path: _cargo_results(receipt.get("output"))
    if minitest_tests.command_words(within):
        return lambda _path: _minitest_results(receipt.get("output"), root)
    return None


def _cargo_results(output):
    return cargo_tests.results(Path(output).read_text(errors="replace")) if output and Path(output).is_file() else None


def _minitest_results(output, root):
    return (
        minitest_tests.results(Path(output).read_text(errors="replace"), root)
        if output and Path(output).is_file()
        else None
    )
