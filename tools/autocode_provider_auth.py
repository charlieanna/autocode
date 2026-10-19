"""Name structured authentication rejection or a trailing auth-helper timeout.

When Codex's external auth command times out (for example ``gocode api-key-helper``),
the stage log ends with plain ``ERROR:`` lines. The attempt is still uncertain and
must not be replayed automatically; this module only names the stop so the person
sees which helper to raise ``timeout_ms`` for. Imports only
``autocode_provider_error_lines``. Structured rejections use a fixed diagnostic;
provider messages and response bodies can contain credential material.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

try:
    from . import autocode_provider_error_lines as provider_error_lines
except ImportError:
    import autocode_provider_error_lines as provider_error_lines

_TIMEOUT = re.compile(
    r"provider auth command [`']([^`']+)[`'] timed out after (\d+) ms",
    re.I,
)
_FAILED = re.compile(
    r"Failed to resolve external auth: provider auth command [`']([^`']+)[`'] timed out after (\d+) ms",
    re.I,
)


def authentication_reason(rows) -> str | None:
    """A provider-owned HTTP 401, never a number in usage, tool output or prose."""
    for row in rows:
        if not isinstance(row, dict) or row.get("type") not in ("error", "turn.failed"):
            continue
        error = row.get("error")
        if not isinstance(error, dict):
            continue
        data = error.get("data")
        for fields in (error, data if isinstance(data, dict) else {}):
            if fields.get("statusCode") == 401 or fields.get("status_code") == 401:
                return (
                    "provider authentication failed (HTTP 401); check or refresh the selected "
                    "provider's credentials before retrying"
                )
    return None


def reason(path) -> str | None:
    """Explain a structured rejection or trailing helper timeout without echoing credentials."""
    path = Path(path)
    if path.is_file():
        for line in path.read_text(errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if auth := authentication_reason([row]):
                return auth
    lines = provider_error_lines.trailing(path)
    if not lines:
        return None
    text = "\n".join(lines)
    match = _FAILED.search(text) or _TIMEOUT.search(text)
    if not match:
        return None
    helper, millis = match.group(1), match.group(2)
    return (
        f"provider auth helper {helper} timed out after {millis} ms; "
        "raise its timeout_ms (or check the helper) and retry"
    )


def exit_message(role, exit_code, events, *, refused="", hint="") -> str:
    """Stage-exit detail: optional refusal and auth timeout, then the no-replay rule."""
    auth = reason(events)
    return (
        (f"{refused}. " if refused else "")
        + (f"{auth}. " if auth else "")
        + f"{role} exited {exit_code}; reconcile {events}, no automatic replay"
        + (f". {hint}" if hint else "")
    )
