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
    from . import autocode_node_tests as node_tests
    from . import autocode_package_scope as package_scope
    from . import autocode_rspec_tests as rspec_tests
except ImportError:
    import autocode_cargo_tests as cargo_tests
    import autocode_jest_tests as jest_tests
    import autocode_node_tests as node_tests
    import autocode_package_scope as package_scope
    import autocode_rspec_tests as rspec_tests

NODE = "node.jsonl"
JEST = "jest.json"
REPORTERS = ((NODE, node_tests), ("rspec.json", rspec_tests))


def extension(command, tree=None, root=None) -> str | None:
    """The result file extension of the reporter ``command`` runs with, or None for another command.

    With ``tree``, an ``npm test`` there whose script is node's own test runner reports as node:test does
    (``node_tests.npm_test``). A Jest command, or an ``npm test`` in ``root`` whose script is one, reports
    as Jest does."""
    found = next((found for found, runner in REPORTERS if runner.command_words(command)), None)
    if found or (tree is not None and node_tests.npm_test(command, tree)):
        return found or NODE
    return JEST if jest_tests.command_words(command, root) else None


def reports(command, tree=None, root=None) -> bool:
    """True when ``command`` must yield per-test results: a reporter's file, or Cargo's own output."""
    return extension(command, tree, root) is not None or cargo_tests.command_words(command) is not None


def instrument(command, result_path, tree=None, root=None) -> str | None:
    """``command`` writing its reporter's results to ``result_path``, or None for another command.

    A ``cargo test`` command writes no file; it is made to run every test binary."""
    runner = next((runner for _, runner in REPORTERS if runner.command_words(command)), None)
    if runner:
        return runner.instrument(command, result_path)
    if tree is not None and (actual := node_tests.instrument_npm(command, result_path, tree)):
        return actual
    if jest_tests.command_words(command, root):
        return jest_tests.instrument(command, result_path, root)
    return cargo_tests.instrument(command) if cargo_tests.command_words(command) else None


def reader(result_path, receipt=None, root=None):
    """The function that reads ``result_path``'s per-test results, or None when no reporter wrote it.

    For the ``receipt`` of a ``cargo test`` run (also one inside its package, ``cd PACKAGE && cargo test``)
    the function reads the run's own output instead, whichever suite was detected. Jest's report is read
    against ``root``, where the command ran."""
    if str(result_path).endswith("." + JEST):
        return lambda path: jest_tests.results(path, root)
    found = next((runner.results for found, runner in REPORTERS if str(result_path).endswith("." + found)), None)
    if found or not cargo_tests.command_words(package_scope.within((receipt or {}).get("command") or "")):
        return found
    return lambda _path: _cargo_results(receipt.get("output"))


def _cargo_results(output):
    return cargo_tests.results(Path(output).read_text(errors="replace")) if output and Path(output).is_file() else None
