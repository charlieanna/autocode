"""A provider's content filter refused a stage's response: recognize the refusal and name it.

A content-filter refusal is the provider's verdict on this request with this model, so
replaying the same model is likely to be refused again. The runner types the stop
(``PAUSED_CONTENT_FILTER``) instead of leaving it an uncertain exit, says which model was
refused, and asks a person to name another model for the role (``autocode_quota_route``).
Nothing retries the refused model on its own.

Only the provider's error events classify (``error`` and ``turn.failed`` rows, as
``autocode_support.events`` returns them): an OpenCode ``ContentFilterError``, a
``content_filter`` code or type, or an error message naming the content filter. A stream
whose final step finished with reason ``content-filter`` classifies too, with or without an
error event, because the OpenCode adapter normalizes that finish into a ``turn.failed`` row
with code ``content_filter`` (``providers.opencode.normalized_events``). The model's own
text ("The request was rejected ...") never does. Pure functions over event rows; imports
nothing from AutoCode.
"""

from __future__ import annotations

STATUS = "PAUSED_CONTENT_FILTER"
_TYPED = ("contentfiltererror", "content_filter")
_WORDS = ("content filter", "content_filter", "content-filter")


def _typed(value) -> bool:
    return isinstance(value, str) and value.strip().lower().replace("-", "_") in _TYPED


def refusal(rows) -> dict | None:
    """``{"error": name, "message": text}`` for the provider's content-filter refusal in ``rows``, or None."""
    for row in rows or ():
        if not isinstance(row, dict) or row.get("type") not in ("error", "turn.failed"):
            continue
        error = row.get("error")
        if not isinstance(error, dict):
            error = {"message": error if isinstance(error, str) else row.get("message")}
        data = error.get("data")
        if not isinstance(data, dict):
            data = {}
        message = error.get("message") or data.get("message")
        message = message.strip() if isinstance(message, str) else ""
        name = next((error[key] for key in ("name", "code", "type") if _typed(error.get(key))), None)
        if name or any(word in message.lower() for word in _WORDS):
            return {"error": name or "content_filter", "message": message}
    return None


def explain(rows, *, job: str, model: str | None) -> str | None:
    """The stop's first sentence: whose response was refused, on which model, in the provider's words."""
    found = refusal(rows)
    if not found:
        return None
    detail = found["error"] + (": " + found["message"] if found["message"] else "")
    return (
        f"{job}: the provider's content filter refused the response on {model or 'its configured model'} "
        f"({detail}); the same model is likely to refuse it again"
    )
