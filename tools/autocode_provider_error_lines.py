"""The provider's own error lines at the end of a stage log, when it wrote them as plain text.

A stage log holds the provider's stdout and stderr. A tool that streams JSON events reports a
failed request as an ``error`` or ``turn.failed`` event; a command-line tool can instead end with
plain lines such as ``ERROR: exceeded retry limit, last status: 429 Too Many Requests`` (``codex
exec`` without ``--json``), which the JSON reader skips (#562). Only the final run of error lines
counts. Tool output, inside a JSON event or printed by a human-readable CLI, comes before the
provider's last word, so a test report that mentions 429 is never read as the provider's error.
"""

from __future__ import annotations

import re
from pathlib import Path

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
# ``ERROR:`` as ``codex exec`` prints it, ``Error:`` as Node and Rust CLIs do. Older Codex
# releases put a bracketed timestamp first. Cursor prints an exhausted model quota
# as ``ActionRequiredError:``. A JSON event line starts with ``{`` and ends the run.
_ERROR = re.compile(r"(?:\[[^\]]*\]\s*)?(?:error|actionrequirederror):\s*\S", re.I)


def trailing(path) -> list[str]:
    """The plain-text error lines that end the log at ``path``, in log order; blank lines are skipped."""
    path = Path(path)
    if not path.is_file():
        return []
    found = []
    for line in reversed(path.read_text(errors="replace").splitlines()):
        line = _ANSI.sub("", line).strip()
        if not line:
            continue
        if not _ERROR.match(line):
            break
        found.append(line)
    return found[::-1]
