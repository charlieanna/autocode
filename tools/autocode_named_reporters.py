"""The runners whose own machine-readable reporter the proof adds to a command: node:test and RSpec.

Each reporter writes per-test results to a file in the proof's evidence, named with its extension, and
``results`` reads that file back. Cargo needs no reporter: the test harness names every test in the
command's own output, which ``reader`` reads from the run's receipt (autocode_cargo_tests). Kept here,
below autocode_verify, so a runner is added in one place.
"""

from __future__ import annotations

from pathlib import Path

try:
    from . import autocode_cargo_tests as cargo_tests
    from . import autocode_node_tests as node_tests
    from . import autocode_package_scope as package_scope
    from . import autocode_rspec_tests as rspec_tests
except ImportError:
    import autocode_cargo_tests as cargo_tests
    import autocode_node_tests as node_tests
    import autocode_package_scope as package_scope
    import autocode_rspec_tests as rspec_tests

REPORTERS = (("node.jsonl", node_tests), ("rspec.json", rspec_tests))


def extension(command) -> str | None:
    """The result file extension of the reporter ``command`` runs with, or None for another command."""
    return next((found for found, runner in REPORTERS if runner.command_words(command)), None)


def reports(command) -> bool:
    """True when ``command`` must yield per-test results: a reporter's file, or Cargo's own output."""
    return extension(command) is not None or cargo_tests.command_words(command) is not None


def instrument(command, result_path) -> str | None:
    """``command`` writing its reporter's results to ``result_path``, or None for another command.

    A ``cargo test`` command writes no file; it is made to run every test binary."""
    runner = next((runner for _, runner in REPORTERS if runner.command_words(command)), None)
    if runner:
        return runner.instrument(command, result_path)
    return cargo_tests.instrument(command) if cargo_tests.command_words(command) else None


def reader(result_path, receipt=None):
    """The function that reads ``result_path``'s per-test results, or None when no reporter wrote it.

    For the ``receipt`` of a ``cargo test`` run (also one inside its package, ``cd PACKAGE && cargo test``)
    the function reads the run's own output instead, whichever suite was detected."""
    found = next((runner.results for found, runner in REPORTERS if str(result_path).endswith("." + found)), None)
    if found or not cargo_tests.command_words(package_scope.within((receipt or {}).get("command") or "")):
        return found
    return lambda _path: _cargo_results(receipt.get("output"))


def _cargo_results(output):
    return cargo_tests.results(Path(output).read_text(errors="replace")) if output and Path(output).is_file() else None
