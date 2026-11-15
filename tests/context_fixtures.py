"""Writable run paths for prompt tests whose handoffs can be externalized."""

from pathlib import Path
from tempfile import TemporaryDirectory


def state_path(test_case):
    directory = TemporaryDirectory(prefix="autocode-context-test-")
    test_case.addCleanup(directory.cleanup)
    return Path(directory.name) / "state.json"
