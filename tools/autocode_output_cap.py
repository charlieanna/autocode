"""OpenCode's requested output-limit setting and AutoCode's launch value.

OpenCode derives a per-response limit from the model's listed output limit and
``OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX``, with a 32000-token default. Enforcement
depends on the provider route: OpenCode 1.18.33's OpenAI Codex plugin clears this
request parameter. A recorded setting therefore cannot establish the effective limit.
On routes that honor it, reasoning counts against the same limit. A large planning
report can therefore stop with finish reason "length" and truncated JSON.

AutoCode launches OpenCode with the variable at DEFAULT_TOKENS unless the operator set
a positive whole number, which wins. Not the model's whole listed limit: OpenCode
compacts a session once its context reaches the window minus this cap, and an
OpenAI-compatible API may refuse a request whose input plus maximum output exceeds the
window. Each stage record keeps the requested setting (or OpenCode's default when
absent) under ``output_token_cap``; the runner names it separately from observed usage
when a stage stops with a length finish.

Pure functions over mappings; imports nothing from AutoCode.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, MutableMapping

STATUS = "PAUSED_OUTPUT_CAP"
VARIABLE = "OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX"
OPENCODE_DEFAULT = 32000
DEFAULT_TOKENS = 64000
# How a provider's length stop starts; explain() adds the cap to exactly these reasons.
LENGTH_STOP = "OpenCode exhausted its output token limit (finish reason: length)"
SET_BY = {
    "operator": "set by the operator",
    "autocode": "AutoCode's default",
    "opencode": "OpenCode's default; the variable did not reach it",
}
_WHOLE_NUMBER = re.compile(r"[0-9]{1,9}")


def configured(env: Mapping[str, str]) -> int | None:
    """The requested limit ``env`` sets, or None for OpenCode's default setting."""
    value = env.get(VARIABLE, "")
    return int(value) if _WHOLE_NUMBER.fullmatch(value) and int(value) > 0 else None


def apply(child: MutableMapping[str, str]) -> None:
    """Set AutoCode's requested limit unless the launch already holds a usable one."""
    if configured(child) is None:
        child[VARIABLE] = str(DEFAULT_TOKENS)


def recorded(operator: Mapping[str, str], child: Mapping[str, str]) -> dict:
    """The launch setting and who chose it, without claiming provider enforcement."""
    tokens = configured(child)
    if tokens is None:
        return {"tokens": OPENCODE_DEFAULT, "set_by": "opencode"}
    return {"tokens": tokens, "set_by": "operator" if configured(operator) == tokens else "autocode"}


def length_stop(tokens) -> str:
    """The failure a "length" finish reports, with what its last response used when known."""
    used = ""
    if isinstance(tokens, dict) and all(
        type(tokens.get(key)) is int and tokens[key] >= 0 for key in ("output", "reasoning")
    ):
        used = (
            f" after {tokens['output'] + tokens['reasoning']} output tokens in one response, "
            f"{tokens['reasoning']} of them reasoning"
        )
    return LENGTH_STOP + used + ". The attempt is incomplete; review saved work before recovery."


def explain(reason: str | None, cap: Mapping | None) -> str | None:
    """Name the requested setting and qualify advice to increase it after a length stop."""
    if not reason or not reason.startswith(LENGTH_STOP) or not isinstance(cap, Mapping):
        return reason
    return (
        f"{reason.rstrip('.')}. The recorded output-limit setting is {cap.get('tokens')} tokens, "
        f"reasoning included ({VARIABLE}, {SET_BY.get(cap.get('set_by') or '') or cap.get('set_by') or VARIABLE}); "
        f"the provider's effective limit may differ, and some routes ignore this requested setting. "
        f"On routes that honor it, set {VARIABLE} to a larger number of tokens before resuming; "
        f"model and provider limits still apply."
    )


def exhausted(rows) -> bool:
    """Only a terminal structured length failure, never model text or an unfinished stream."""
    if not isinstance(rows, list) or not rows or not isinstance(rows[-1], dict):
        return False
    terminal = rows[-1]
    error = terminal.get("error")
    return (
        terminal.get("type") == "turn.failed"
        and isinstance(error, dict)
        and error.get("code") == "output_token_limit"
        and not any(row.get("type") in ("error", "turn.completed") for row in rows if isinstance(row, dict))
    )


def authenticated(record, rows) -> bool:
    """A collected length stop from the attempt's own session; uncertainty never grants routing."""
    if (
        not isinstance(record, Mapping)
        or not exhausted(rows)
        or not record.get("finished_at")
        or type(record.get("exit_code")) is not int
        or record["exit_code"] < 0
        or "expected_session" not in record
        or any(record.get(key) for key in ("timed_out", "interrupted", "cleanup_error", "supervision_errors"))
    ):
        return False
    expected = record["expected_session"]
    if expected is not None and (not isinstance(expected, str) or not expected):
        return False
    threads = [row.get("thread_id") for row in rows if isinstance(row, dict) and row.get("type") == "thread.started"]
    return (
        bool(threads)
        and all(isinstance(thread, str) and thread for thread in threads)
        and len(set(threads)) == 1
        and (expected is None or expected in threads)
    )


def terminal_transport(text: str) -> bool:
    """Do not let normalization discard an ambiguous or trailing transport event."""
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            return False
        if not isinstance(row, dict):
            return False
        rows.append(row)
    if not rows:
        return False
    if any("sessionID" in row for row in rows):
        known = {"step_start", "step_finish", "tool_use", "text", "reasoning", "autocode_progress"}
        sessions = [row.get("sessionID") for row in rows]
        if not all(isinstance(session, str) and session for session in sessions) or len(set(sessions)) != 1:
            return False
        for row in rows:
            if row.get("type") == "autocode_progress":
                progress = row.get("progress")
                if (
                    type(row.get("version")) is not int
                    or row["version"] != 1
                    or not isinstance(progress, dict)
                    or not isinstance(progress.get("id"), str)
                    or not progress["id"]
                    or progress.get("kind") not in ("text", "reasoning")
                    or progress.get("nonwhite") is not True
                    or type(progress.get("position")) is not int
                    or not 0 < progress["position"] <= 2**53 - 1
                    or any(
                        not isinstance(progress.get(key), str) or re.fullmatch("[0-9a-f]{64}", progress[key]) is None
                        for key in ("content_hash", "delta_hash")
                    )
                ):
                    return False
                continue
            part = row.get("part")
            if not isinstance(part, dict) or ("sessionID" in part and part["sessionID"] != row["sessionID"]):
                return False
            if row.get("type") in ("step_start", "step_finish") and not isinstance(part.get("id"), str):
                return False
        terminal = rows[-1]
        part = terminal.get("part")
        return (
            all(row.get("type") in known for row in rows)
            and terminal.get("type") == "step_finish"
            and isinstance(part, dict)
            and part.get("reason") == "length"
        )
    known = {
        "thread.started",
        "turn.started",
        "item.started",
        "item.updated",
        "item.completed",
        "turn.failed",
        "usage.partial",
    }
    return all(row.get("type") in known for row in rows) and exhausted(rows)
