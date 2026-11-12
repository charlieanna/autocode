"""The runners whose own machine-readable reporter the proof adds to a command: node:test and RSpec.

Each reporter writes per-test results to a file in the proof's evidence, named with its extension, and
``results`` reads that file back. Kept here, below autocode_verify, so a runner is added in one place.
"""

from __future__ import annotations

try:
    from . import autocode_node_tests as node_tests
    from . import autocode_rspec_tests as rspec_tests
except ImportError:
    import autocode_node_tests as node_tests
    import autocode_rspec_tests as rspec_tests

REPORTERS = (("node.jsonl", node_tests), ("rspec.json", rspec_tests))


def extension(command) -> str | None:
    """The result file extension of the reporter ``command`` runs with, or None for another command."""
    return next((found for found, runner in REPORTERS if runner.command_words(command)), None)


def instrument(command, result_path) -> str | None:
    """``command`` writing its reporter's results to ``result_path``, or None for another command."""
    runner = next((runner for _, runner in REPORTERS if runner.command_words(command)), None)
    return runner.instrument(command, result_path) if runner else None


def reader(result_path):
    """The function that reads ``result_path``'s per-test results, or None when no reporter wrote it."""
    return next((runner.results for found, runner in REPORTERS if str(result_path).endswith("." + found)), None)
