"""Names-only guidance for a provider's rejected authentication request.

This never discovers credentials or enables pass-through. The existing agent
membrane and billing-route checks remain the authority for child environments.
"""

from __future__ import annotations

import re

try:
    from .autocode_control_tokens import CONTROL_TOKEN_ENV_VARS
except ImportError:
    from autocode_control_tokens import CONTROL_TOKEN_ENV_VARS

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def withheld_hint(names) -> str:
    """Suggest an explicit opt-in only for actual withheld, non-controller names."""
    if not isinstance(names, (list, tuple)):
        return ""
    candidates = sorted(
        {
            name
            for name in names
            if isinstance(name, str) and _NAME.fullmatch(name) and name.upper() not in CONTROL_TOKEN_ENV_VARS
        }
    )
    if not candidates:
        return ""
    return (
        "Credential-like environment variables were withheld: "
        + ", ".join(candidates)
        + ". If this provider requires one, explicitly name only the required variables in "
        "AUTOCODE_PASS_ENV (comma-separated); see docs/providers.md#environment-variables-agents-see."
    )


def authentication_hint(rows, names) -> str:
    """Recognize native authentication errors, never arbitrary tool or model prose."""
    for row in rows:
        if not isinstance(row, dict) or row.get("type") != "error":
            continue
        error = row.get("error")
        if not isinstance(error, dict) or error.get("name") != "APIError":
            continue
        data = error.get("data")
        code = data.get("statusCode") if isinstance(data, dict) else None
        if type(code) is int and code in (401, 403):
            return withheld_hint(names)
    return ""
