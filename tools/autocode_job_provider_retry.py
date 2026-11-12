"""A workflow job whose provider ended the stream on an API error retries itself once (#3233 rerun).

A live Tester writing acceptance tests on MiMo ran for 38 minutes, then the provider's gateway
answered one request with ``400 Request failed: Connection prematurely closed BEFORE response``
and the stream ended. Nothing about the job's route or its work needs deciding: the same request
on the same model is the obvious next step, and the person was asked to "inspect the failed job,
then retry". So a job whose provider stream ended on the provider's own ``APIError`` row, and that
no other classification claimed (quota, rate limit, capacity, refusal, output or context limit,
authentication), is retried once more on the same route and deadline, at most ``MAX_RETRIES``
times for that job in a run. The run's money and time caps still apply before the launch. Each
retry is recorded as a runner ``user_events`` entry (``EVENT``) with its reason, so the report and
the verification view show the decision.

``provider_error`` and ``plan`` are pure; autocode_job_failure applies them. Imports nothing from AutoCode.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

MAX_RETRIES = 1
EVENT = "automatic_job_provider_error_retry"
KIND = "provider_error"
MAX_MESSAGE = 300


def provider_error(raw: str) -> str | None:
    """The provider's message when its stream ended on its own API error row, else None.

    Reads the last JSON object line: OpenCode's ``{"type": "error", "error": {"name": "APIError", ...}}``
    or the command provider's bare ``{"name": "APIError", "data": ...}`` (#1001). Any later event, or
    an error of another name, is not this stop.
    """
    for line in reversed(raw.splitlines()):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        error = row.get("error") if row.get("type") == "error" else row if "type" not in row else None
        if not isinstance(error, dict) or error.get("name") != "APIError" or not isinstance(error.get("data"), dict):
            return None
        data = error["data"]
        message = data.get("message") if isinstance(data.get("message"), str) else ""
        body = data.get("responseBody") if isinstance(data.get("responseBody"), str) else ""
        try:
            detail = (json.loads(body).get("error") or {}).get("param") if body else ""
        except (ValueError, AttributeError):
            detail = ""
        text = ": ".join(part.strip() for part in (message, detail if isinstance(detail, str) else "") if part.strip())
        status = data.get("statusCode")
        if type(status) is int:
            text = f"{status} {text}".strip()
        return (text or "APIError")[:MAX_MESSAGE]
    return None


def used(state: RunState, stage) -> int:
    """Automatic provider-error retries already spent on ``stage`` in this run."""
    return sum(
        1
        for event in state.get("user_events") or []
        if isinstance(event, dict) and event.get("kind") == EVENT and event.get("stage") == stage
    )


def plan(state: RunState, failure: dict, *, abandoned: bool) -> dict | None:
    """The retry to authorize for this stopped job, or None when the stop is not one AutoCode settles."""
    if abandoned or failure.get("kind") != KIND or failure.get("unrestored"):
        return None
    spent = used(state, failure.get("stage"))
    if spent >= MAX_RETRIES:
        return None
    return {"retry": spent + 1, "of": MAX_RETRIES}


def reason(job: str, retry: dict) -> str:
    """What AutoCode decided, for the stop record and the report."""
    return (
        f"AutoCode retries the {job} once on the same model and deadline (retry {retry['retry']} of {retry['of']}), "
        "within the run's time and cost caps"
    )


def exhausted(job: str) -> str:
    """Why a job that hit a provider error again paused after AutoCode's own retry."""
    return f"AutoCode already retried the {job} after a provider error on the same model, so it now waits for its exact retry"
