"""Launcher call markers identify an invocation, not a tool's inputs.

Use this one classification for preflight identity and test execution. Keep all
other variables, including account, configuration and executable selection.
Do not drop arbitrary CODEX_* variables: those can select real tool settings.
"""

from collections.abc import Mapping

# Node inserts NODE_TEST_CONTEXT into test workers. An independent nested
# node:test runner treats it as a recursion marker and exits without running.
CALL_MARKERS = frozenset({"CODEX_TOOL_CALL_ID", "AUTOCODE_CAPTURE_FAMILY", "NODE_TEST_CONTEXT"})


def without_call_markers(environment: Mapping[str, str]) -> dict[str, str]:
    """Return a copy without invocation-only metadata; never mutate the caller."""
    return {name: value for name, value in environment.items() if name not in CALL_MARKERS}
