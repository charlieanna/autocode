"""A stage's context window ran out, or the provider compacted the conversation: name the stop.

A stage must see its contract, criteria and findings whole (#214). When a request no longer fits
the model's context window, the provider either rejects it (OpenCode and Kilo: a
``ContextOverflowError`` with code ``context_length_exceeded``; Claude Code: ``prompt_too_long``)
or summarizes the conversation and carries on. Kilo does both: after an overflow it compacts and
continues, and before one it compacts proactively unless ``compaction.auto`` is off. Either way
the response that follows was produced from a summary, so the runner refuses it and pauses
``PAUSED_CONTEXT_WINDOW`` (#1942), a model stop like a content-filter refusal: a person may name
a model with a larger window (``autocode_quota_route``), or give the run a smaller task.

What classifies, in the stage's event rows as ``autocode_support.events`` returns them:

- an ``error`` or ``turn.failed`` row typed as an overflow (``overflow``);
- a ``turn.failed`` row with code ``context_compacted``, which ``providers.opencode_events``
  emits for an ``autocode_compaction`` row (the activity plugin saw a compaction part) or for a
  request whose input shrank far below the previous request's in the same session, when nothing
  else explains the stop: a conversation only grows unless something summarized it (``compacted``);
- Claude Code's ``compact_boundary`` row, which its translator turns into the same code.

The model's own words never classify. Pure functions over event rows; imports nothing from AutoCode.
"""

from __future__ import annotations

import json

STATUS = "PAUSED_CONTEXT_WINDOW"
OVERFLOW_CODE = "context_window_exceeded"
COMPACTED_CODE = "context_compacted"
PLUGIN_ROW = "autocode_compaction"  # written by providers/opencode_activity.mjs
# A later request in one session holding less than half of the previous one, which held at
# least this much, is a compaction signature; smaller contexts never trip it.
SHRINK_RATIO = 0.5
SHRINK_FLOOR = 32_000
_TYPED = ("contextoverflowerror", "context_length_exceeded", OVERFLOW_CODE, "prompt_too_long")
# Phrases that say this request did not fit. A quota error often quotes the model's window size in the
# same sentence ("...limit exhausted; its context window is 200000 tokens"), so a bare mention of the
# window is not evidence of an overflow: the wording has to say the input exceeded it.
_WORDS = ("context_length_exceeded", "prompt is too long", "input is too long", "exceeds the context window")


def _typed(value) -> bool:
    return isinstance(value, str) and value.strip().lower().replace("-", "_") in _TYPED


def _error(row) -> dict | None:
    if not isinstance(row, dict) or row.get("type") not in ("error", "turn.failed"):
        return None
    error = row.get("error")
    if not isinstance(error, dict):
        error = {"message": error if isinstance(error, str) else row.get("message")}
    return error


def overflow(rows) -> dict | None:
    """``{"error": name, "message": text}`` for the provider's context-window rejection in ``rows``, or None."""
    for row in rows or ():
        error = _error(row)
        if error is None or error.get("code") == COMPACTED_CODE:
            continue
        data = error.get("data")
        if not isinstance(data, dict):
            data = {}
        message = error.get("message") or data.get("message")
        message = message.strip() if isinstance(message, str) else ""
        name = next((error[key] for key in ("name", "code", "type") if _typed(error.get(key))), None)
        if name is None and isinstance(data.get("responseBody"), str):
            # OpenCode keeps the API's own error under the row; its code is the typed authority.
            try:
                body = json.loads(data["responseBody"])
            except ValueError:
                body = None
            code = (body.get("error") or {}).get("code") if isinstance(body, dict) else None
            name = code if _typed(code) else None
        if name or any(word in message.lower() for word in _WORDS):
            return {"error": name or OVERFLOW_CODE, "message": message}
    return None


def request_sizes(rows) -> list[int]:
    """Each completed request's input, cache reads and writes included, in stream order; unknown ones left out."""
    sizes, seen = [], set()
    for row in rows or ():
        if not isinstance(row, dict) or row.get("type") != "step_finish":
            continue
        part = row.get("part")
        if not isinstance(part, dict):
            continue
        identity = (row.get("sessionID"), part.get("id"))
        if not all(isinstance(value, str) and value for value in identity):
            continue  # an unidentified request is not measured, as autocode_request_usage leaves it
        if identity in seen:
            continue  # a repeated part update is the same request
        seen.add(identity)
        tokens = part.get("tokens")
        cache = tokens.get("cache") if isinstance(tokens, dict) else None
        if not isinstance(tokens, dict) or not isinstance(cache, dict):
            continue  # a request without an input and cache-report is not measured
        reported = [tokens.get("input"), cache.get("read"), cache.get("write")]
        counted = [value for value in reported if value is not None]
        if len(counted) == len(reported) and all(type(value) is int and value >= 0 for value in counted):
            sizes.append(sum(counted))
    return sizes


def shrank(rows) -> dict | None:
    """The first request that held less than half of the previous one's context, or None."""
    sizes = request_sizes(rows)
    for previous, current in zip(sizes, sizes[1:], strict=False):
        if previous >= SHRINK_FLOOR and current < previous * SHRINK_RATIO:
            return {"signal": "request_shrank", "from": previous, "to": current}
    return None


def compacted(rows) -> dict | None:
    """How ``rows`` show the provider compacted the conversation, or None.

    A normalized ``turn.failed`` row with code ``context_compacted`` carries the detail its emitter
    found; raw rows are read for the plugin's ``autocode_compaction`` row and the shrink signature.
    The signature counts only when nothing else failed, or the failure was the window itself (Kilo
    refuses, then summarizes and carries on): a stage that failed for another reason can send a
    smaller last request for any number of unrelated reasons.
    """
    for row in rows or ():
        error = _error(row)
        if error is not None and error.get("code") == COMPACTED_CODE:
            detail = row.get("compaction")
            return dict(detail) if isinstance(detail, dict) else {"signal": "reported"}
    for row in rows or ():
        if isinstance(row, dict) and row.get("type") == PLUGIN_ROW:
            reported = row.get("compaction")
            detail = reported if isinstance(reported, dict) else {}
            return {"signal": "compaction_part", **{k: detail[k] for k in ("auto", "overflow") if k in detail}}
    if any(_error(row) is not None for row in rows or ()) and overflow(rows) is None:
        return None
    return shrank(rows)


def found(rows) -> dict | None:
    """``{"overflow": ..., "compaction": ...}`` when the context ran out or was compacted, else None."""
    full = overflow(rows)
    summary = compacted(rows)
    if full is None and summary is None:
        return None
    return {"overflow": full, "compaction": summary}


def compaction_message(detail) -> str:
    """One clause saying how the compaction was seen."""
    if not isinstance(detail, dict):
        return "the provider compacted the conversation"
    if detail.get("signal") == "request_shrank":
        return (
            f"the provider compacted the conversation: a request held {detail.get('to')} tokens after the "
            f"previous one held {detail.get('from')}"
        )
    if detail.get("signal") == "compaction_part":
        how = "after the overflow" if detail.get("overflow") else "on its own" if detail.get("auto") else ""
        return "the provider compacted the conversation" + (f" {how}" if how else "")
    return "the provider compacted the conversation"


def explain(rows, *, job: str, model: str | None, request_tokens: int | None = None) -> str | None:
    """The stop's first sentence: the context ran out or was summarized, on which model, how large the request was."""
    result = found(rows)
    if result is None:
        return None
    on = model or "its configured model"
    parts = []
    if result["overflow"]:
        detail = result["overflow"]["error"] + (
            ": " + result["overflow"]["message"] if result["overflow"]["message"] else ""
        )
        parts.append(f"the request exceeded the model's context window on {on} ({detail})")
    if result["compaction"]:
        parts.append(compaction_message(result["compaction"]))
    size = (
        f"; the stage's last completed request held {request_tokens} input tokens"
        if type(request_tokens) is int
        else ""
    )
    return (
        f"{job}: " + ", and ".join(parts) + size + ". The response was produced from a summary, not the whole "
        "handoff, so it is not accepted; give the job a smaller task, scope the run with --package, or name a "
        "model with a larger context window"
    )
