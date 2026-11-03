"""Readable command diagnostics, used only for display and never as authority."""

import json

try:
    from .autocode_util import redact
except ImportError:
    from autocode_util import redact


def command_error(output: str, *, secret_values=()) -> str:
    """Unwrap a core JSON error while retaining unknown output and redaction."""
    try:
        value = json.loads(output)
    except ValueError:
        value = None
    if isinstance(value, dict) and isinstance(value.get("error"), str) and value["error"].strip():
        output = value["error"]
    return redact(output, secret_values=secret_values).strip()[-2000:]
