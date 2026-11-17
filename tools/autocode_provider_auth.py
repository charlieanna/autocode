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


# The fixed diagnostic for a provider-owned HTTP 401: never the provider's own message, which
# can carry credential material. The recovery view recognizes a stop by it (autocode_recovery_view).
AUTH_FAILED = (
    "provider authentication failed (HTTP 401); check or refresh the selected provider's credentials before retrying"
)
AUTH_ACCESS_DENIED = "provider account access denied (HTTP 403); check the selected account's access"
_HELPER_PREFIX = "provider auth helper "
_HELPER_ADVICE = "raise its timeout_ms (or check the helper) and retry"
_HELPER_DIAGNOSTIC = re.compile(
    rf"(?:^|\. ){re.escape(_HELPER_PREFIX)}[^\r\n]+ timed out after \d+ ms; {re.escape(_HELPER_ADVICE)}"
)
# What a person can do about each, in plain words. A refused account is theirs to fix, so it is named
# even when AutoCode's own recovery of the attempt is exhausted and nothing else is asked (#3682).
ACCOUNT_CAUSES = {
    AUTH_FAILED: (
        "The model provider rejected the saved credentials (HTTP 401): the key or sign-in has expired or "
        "been revoked. Sign in to the backend again."
    ),
    AUTH_ACCESS_DENIED: (
        "The model provider denied access for the selected account or organization (HTTP 403). "
        "Check that this account is allowed to use the selected backend and model."
    ),
}


def account_cause(text) -> str | None:
    """The plain sentence for the core's own authentication diagnostic in ``text``, or None."""
    return next((cause for diagnostic, cause in ACCOUNT_CAUSES.items() if diagnostic in str(text or "")), None)


def helper_timed_out(text) -> bool:
    """Recognize the runner's saved helper diagnostic without exposing the helper or operator advice."""
    return _HELPER_DIAGNOSTIC.search(str(text or "")) is not None


def authentication_reason(rows) -> str | None:
    """A provider-owned 401, or 403 paired with a typed terminal authentication failure."""
    forbidden = False
    authentication_failed = False
    for row in rows:
        if not isinstance(row, dict) or row.get("type") not in ("error", "turn.failed"):
            continue
        error = row.get("error")
        if not isinstance(error, dict):
            continue
        if row.get("type") == "turn.failed" and error.get("code") == "authentication_failed":
            authentication_failed = True
        data = error.get("data")
        for fields in (error, data if isinstance(data, dict) else {}):
            if fields.get("statusCode") == 401 or fields.get("status_code") == 401:
                return AUTH_FAILED
            if fields.get("statusCode") == 403 or fields.get("status_code") == 403:
                forbidden = True
    return AUTH_ACCESS_DENIED if forbidden and authentication_failed else None


def _rows(path):
    with path.open(errors="replace") as stream:
        for line in stream:
            try:
                yield json.loads(line)
            except ValueError:
                continue


def reason(path) -> str | None:
    """Explain a structured rejection or trailing helper timeout without echoing credentials."""
    path = Path(path)
    if path.is_file() and (auth := authentication_reason(_rows(path))):
        return auth
    lines = provider_error_lines.trailing(path)
    if not lines:
        return None
    text = "\n".join(lines)
    match = _FAILED.search(text) or _TIMEOUT.search(text)
    if not match:
        return None
    helper, millis = match.group(1), match.group(2)
    return f"{_HELPER_PREFIX}{helper} timed out after {millis} ms; {_HELPER_ADVICE}"


def exit_message(role, exit_code, events, *, refused="", hint="") -> str:
    """Stage-exit detail: optional refusal and auth timeout, then the no-replay rule."""
    auth = reason(events)
    return (
        (f"{refused}. " if refused else "")
        + (f"{auth}. " if auth else "")
        + f"{role} exited {exit_code}; reconcile {events}, no automatic replay"
        + (f". {hint}" if hint else "")
    )
