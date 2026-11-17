"""Recognize deterministic provider rejections of an unavailable or account-unsupported model.

A model listed in a tool's static catalogue can still be rejected outright by the upstream
API for the connected account (for example HTTP 400 ``model_not_found`` with
``isRetryable: false``: ``"The 'gpt-5.3-codex-spark' model is not supported when using Codex
with a ChatGPT account."``, or HTTP 404 ``model_not_found`` / ``not_found_error``). During an
access preflight probe (``record is None``), continuing past such a deterministic rejection
wastes planning spend before the rejected role eventually fails the same way.

Transient failures (timeouts, 429 rate limits, 5xx upstream errors, ``isRetryable: true``,
connection drops) and non-model request errors (schema, parameter, reasoning-variant, auth or
paywall errors, which have their own classifications) never match here. Pure functions over
normalized provider event rows; imports nothing from AutoCode.
"""

from __future__ import annotations

import json
import re

_MODEL_CODES = frozenset(
    {
        "model_not_found",
        "model_not_supported",
        "unsupported_model",
        "invalid_model",
        "unknown_model",
        "model_decommissioned",
        "decommissioned_model",
        "model_not_available",
        "no_endpoints_found",
    }
)
_TRANSIENT_OR_OTHER_HTTP = frozenset({401, 402, 408, 409, 425, 429})
_CLIENT_REJECTION_HTTP = frozenset({400, 403, 404, 422})
_MODEL_UNAVAILABLE_TEXT = re.compile(
    r"(?:\bmodel\b[^.\n]{0,160}\b(?:is not supported|not supported|does not exist|not found|"
    r"not available|not enabled|do not have access|does not have access|"
    r"has been deprecated|has been decommissioned)\b"
    r"|\b(?:is not supported|not supported|does not exist|do not have access|does not have access|"
    r"no endpoints found)\b[^.\n]{0,160}\bmodel\b"
    r"|^no endpoints found for\s+\S+)",
    re.I,
)
_PARAMETER_TEXT = re.compile(
    r"\b(?:reasoning_effort|reasoning effort|variant|temperature|top_p|max_tokens|"
    r"response_format|json_schema|schema|tool_choice|tools?|parameter)\b",
    re.I,
)


def _norm_code(value) -> str:
    return value.strip().lower().replace("-", "_") if isinstance(value, str) else ""


def _status_code(error: dict, data: dict) -> int | None:
    for source in (error, data):
        for key in ("statusCode", "status_code"):
            value = source.get(key)
            if type(value) is int:
                return value
    return None


def _parsed_body(error: dict, data: dict) -> tuple[dict, str]:
    for source in (data, error):
        raw = source.get("responseBody") or source.get("response_body")
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            parsed = json.loads(raw)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            inner = parsed.get("error")
            inner_dict = inner if isinstance(inner, dict) else ({"message": inner} if isinstance(inner, str) else {})
            raw_detail = parsed.get("detail")
            detail = raw_detail.strip() if isinstance(raw_detail, str) else ""
            return inner_dict, detail
    return {}, ""


def unavailable(rows) -> dict | None:
    """``{"code": code, "message": text}`` for a deterministic model/account rejection, or None."""
    for row in rows or ():
        if not isinstance(row, dict) or row.get("type") not in ("error", "turn.failed"):
            continue
        raw_error = row.get("error")
        if isinstance(raw_error, dict):
            error = raw_error
        else:
            error = {"message": raw_error if isinstance(raw_error, str) else row.get("message")}
        raw_data = error.get("data")
        data = raw_data if isinstance(raw_data, dict) else {}
        if error.get("isRetryable") is True or data.get("isRetryable") is True:
            continue
        status = _status_code(error, data)
        if status in _TRANSIENT_OR_OTHER_HTTP or (status is not None and status >= 500):
            continue
        inner, body_detail = _parsed_body(error, data)
        message = next(
            (
                candidate.strip()
                for candidate in (
                    error.get("message"),
                    data.get("message"),
                    inner.get("message"),
                    body_detail,
                )
                if isinstance(candidate, str) and candidate.strip()
            ),
            "",
        )
        codes = [
            _norm_code(candidate)
            for candidate in (
                error.get("code"),
                data.get("code"),
                inner.get("code"),
                inner.get("error_type"),
                error.get("type"),
                data.get("type"),
                inner.get("type"),
            )
        ]
        matched_code = next((code for code in codes if code in _MODEL_CODES), "")
        if matched_code:
            return {"code": matched_code, "message": message}
        if "not_found_error" in codes and (status in (None, 404)) and "model" in message.lower():
            return {"code": "not_found_error", "message": message}
        non_retryable = (
            error.get("isRetryable") is False or data.get("isRetryable") is False or status in _CLIENT_REJECTION_HTTP
        )
        if (
            non_retryable
            and message
            and _MODEL_UNAVAILABLE_TEXT.search(message)
            and not _PARAMETER_TEXT.search(message)
        ):
            return {"code": "model_unavailable", "message": message}
    return None
