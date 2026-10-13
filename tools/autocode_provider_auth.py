"""Name a provider auth-helper timeout from the stage log's trailing error lines.

When Codex's external auth command times out (for example ``gocode api-key-helper``),
the stage log ends with plain ``ERROR:`` lines. The attempt is still uncertain and
must not be replayed automatically; this module only names the stop so the person
sees which helper to raise ``timeout_ms`` for. Imports only
``autocode_provider_error_lines``.
"""

from __future__ import annotations

import re

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


def reason(path) -> str | None:
    """Explain a trailing auth-helper timeout, or None when the log ends otherwise."""
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
