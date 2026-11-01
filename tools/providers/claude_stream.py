"""Translate one ``claude -p --output-format stream-json`` stage into Codex-shaped events (#717).

The built-in claude provider (``providers/claude.py``) launches every stage as::

    python -I claude_stream.py --ledger DIR --schema PATH [--raw PATH] [--resumed SESSION] -- CLAUDE -p ...

This program runs Claude Code with the stage prompt on its inherited standard input, inserts
``--json-schema <schema>`` right after ``-p`` (so the schema never lands in a stage record), and
writes only Codex rows to standard output, one JSON object per line: ``thread.started``,
``turn.started``, ``item.started``/``item.updated``/``item.completed``, ``usage.partial``, and one
terminal ``turn.completed`` or ``turn.failed`` (an ``error`` row only before an authentication
failure). The runner's Codex readers (sessions, usage, command evidence, the output cap, the idle
watchdog) then apply unchanged.

- A Bash call is a ``command_execution`` item with Claude Code's real exit code. An exit code
  Claude Code did not report (a backgrounded, interrupted, timed-out or denied call, an unknown
  interpretation) is ``null``, which no evidence check accepts.
- The report is the StructuredOutput input Claude Code accepted, written as the
  ``structured_output`` item; ``providers.claude.final_report`` reads it.
- ``total_cost_usd`` is cumulative per Claude session, so a resumed call reports only its own
  share through a small ledger of session totals in ``--ledger``.
- An init row naming an API key source (``apiKeySource`` other than ``none``) stops Claude Code at
  once and ends the stage ``turn.failed{code: "billing_route"}`` (``PAUSED_BILLING_ROUTE``).
- A ``compact_boundary`` system row (Claude Code summarized the conversation) ends the stage
  ``turn.failed{code: "context_compacted"}`` (``PAUSED_CONTEXT_WINDOW``), whatever followed it: the
  response was not produced from the whole handoff, so no report is accepted from that call.
- Nothing is ever written to standard error: the runner merges it into the stage's event log,
  where one non-JSON line would defeat the output-cap check.

Standard library only; it imports nothing from AutoCode, so it runs under ``python -I``.
Reviewer rule: a change to what these rows mean bumps ``providers.claude.ADAPTER``.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import math
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPORT_ITEM = "structured_output"
BILLING_ROUTE = "billing_route"  # the terminal code autocode_support.failure_status reads as PAUSED_BILLING_ROUTE
COMPACTED = "context_compacted"  # autocode_context_window.COMPACTED_CODE, read as PAUSED_CONTEXT_WINDOW
PARTIAL_ITEM = "structured_output_partial"
REJECTED_ITEM = "structured_output_rejected"
RESULT_TEXT_ITEM = "result_text"
ITEM_PREFIX = "item_"
THROTTLE_SECONDS = 2.0
ITEM_TEXT_LIMIT = 16 * 1024
OUTPUT_LIMIT = 64 * 1024  # head and tail of a command's output each
PARTIAL_LIMIT = 128 * 1024
REJECTED_LIMIT = 64 * 1024
RESULT_TEXT_LIMIT = 64 * 1024
SCHEMA_LIMIT = 120_000  # Linux MAX_ARG_STRLEN is 128 KiB per argv string
RAW_LIMIT = 64 * 1024 * 1024
STDERR_LIMIT = 16 * 1024
# Claude Code 2.1.299 reports exit code 1 of grep/rg/find/diff/test as a non-error with one of
# these messages (its interpretation map marks only >= 2 as an error); any other message is unknown.
LINUX_INTERPRETATIONS = frozenset(
    {"No matches found", "Some directories were inaccessible", "Files differ", "Condition is false"}
)
ROW_TYPES = frozenset(
    {
        "thread.started",
        "turn.started",
        "item.started",
        "item.updated",
        "item.completed",
        "usage.partial",
        "turn.completed",
        "turn.failed",
        "error",
    }
)
BACKGROUND_KEYS = (
    "backgroundTaskId",
    "backgroundedByUser",
    "backgroundedByTurnAbort",
    "backgroundedToDeliverMessage",
    "timedOutAfterMs",
)
FILE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
USAGE_LIMIT_ERRORS = ("usage_limit_reached", "model_requires_usage_credits")
AUTH_ERRORS = (
    "authentication_failed",
    "oauth_org_not_allowed",
    "account_on_hold",
    "verification_required",
    "cloud_credential_error",
)
FAILED_DIAGNOSTICS = ("subtype", "terminal_reason", "api_error", "api_error_status", "stop_reason", "exit_code")
RAW_INIT_KEYS = (
    "type",
    "subtype",
    "session_id",
    "model",
    "tools",
    "permissionMode",
    "claude_code_version",
    "apiKeySource",
)
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_EXIT_LINE = re.compile(r"Exit code (\d+)")
_SESSION_NAME = re.compile(r"[A-Za-z0-9_-]{1,128}")
_UUID = re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
_LIMIT_WORDS = re.compile(r"(?i)you've hit your|spend limit|usage limit")
_TRUNCATION = "Output token limit hit"
_PLACEHOLDER = "(Bash completed with no output)"


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _money(value: Any) -> float | None:
    return float(value) if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def bound(text: str, limit: int = OUTPUT_LIMIT) -> str:
    """The first and last ``limit`` characters of ``text``, with what was left out in between."""
    if len(text) <= 2 * limit:
        return text
    return f"{text[:limit]}\n[… {len(text) - 2 * limit} characters omitted …]\n{text[-limit:]}"


def _prefix_bytes(text: str, limit: int) -> str:
    """The longest prefix of ``text`` whose UTF-8 encoding fits in ``limit`` bytes."""
    return text.encode("utf-8")[:limit].decode("utf-8", "ignore")


def content_text(block: dict) -> str:
    """A tool result's content: its string, or the joined text parts of a list."""
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part["text"] for part in content if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""


def _output(meta: Any, text: str) -> str:
    stdout = meta.get("stdout") if isinstance(meta, dict) else None
    if isinstance(stdout, str):
        stderr = meta.get("stderr")
        return bound(stdout + ("\n" + stderr if isinstance(stderr, str) and stderr else ""))
    return "" if text == _PLACEHOLDER else bound(text)


def bash_exit(block: dict, meta: Any) -> tuple[int | None, str, str | None]:
    """(exit code, output, why the exit code is unknown) of one Bash ``tool_result``.

    ``meta`` is the row's ``tool_use_result``: a dict for a non-error result, the error text for
    an error result. Claude Code writes the first line ``Exit code N`` itself, and only when the
    command failed, so the code is taken only when that line and ``meta`` agree.
    """
    text = content_text(block)
    if isinstance(meta, dict):
        if meta.get("interrupted") is True:
            return None, _output(meta, text), "interrupted"
        if any(meta.get(key) for key in BACKGROUND_KEYS):
            return None, _output(meta, text), "moved to the background"
    if block.get("is_error") is True:
        first, _, rest = text.partition("\n")
        match = _EXIT_LINE.fullmatch(first)
        if (
            match
            and int(match[1]) > 0
            and isinstance(meta, str)
            and (meta == f"Error: Exit code {match[1]}" or meta.startswith(f"Error: Exit code {match[1]}\n"))
        ):
            return int(match[1]), bound(rest), None
        return None, bound(text), "error without an exit code"
    if isinstance(meta, dict):
        interpretation = meta.get("returnCodeInterpretation")
        if not interpretation:
            return 0, _output(meta, text), None
        if interpretation in LINUX_INTERPRETATIONS:
            return 1, _output(meta, text), None
        return None, _output(meta, text), "unrecognized return code interpretation"
    return None, bound(text), "unrecognized result"


def usage_of(raw: Any) -> dict | None:
    """Claude usage in Codex terms; a key whose inputs are not all whole numbers is left out."""
    if not isinstance(raw, dict):
        return None
    fresh, created, read, out = (
        _count(raw.get(key))
        for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
    )
    details = raw.get("output_tokens_details")
    thinking = _count(details.get("thinking_tokens")) if isinstance(details, dict) else None
    usage = {}
    if fresh is not None and created is not None and read is not None:
        usage["input_tokens"] = fresh + created + read
    if read is not None:
        usage["cached_input_tokens"] = read
    if out is not None:
        usage["output_tokens"] = out
        if thinking is not None and thinking <= out:
            usage["reasoning_output_tokens"] = thinking
    return usage or None


def _text_of(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        part["text"]
        for part in content
        if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)
    )


def _compact(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"))


def _completed(identifier: str, kind: str, text: str) -> dict:
    return {"type": "item.completed", "item": {"id": identifier, "type": kind, "text": text}}


class Translator:
    """Claude stream-json rows in, Codex rows out. Pure: tests drive it without processes."""

    def __init__(
        self,
        *,
        resumed: str | None = None,
        previous_cost: float | None = 0.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.resumed = resumed
        self.previous_cost = previous_cost
        self.clock = clock
        self.session_id: str | None = None
        self.billing_source: str | None = None
        self.total_cost_usd: float | None = None
        self.ignored_lines = 0
        self._next = 0
        self._mixed = False
        self._result: dict | None = None
        self._api_error: dict | None = None
        self._refused = False
        self._continuations = 0
        self._rate_limit: dict = {}
        self._message: str | None = None
        self._streamed: set[str] = set()
        self._usage: dict[str, dict] = {}
        self._blocks: dict[tuple, dict] = {}
        self._progress: dict[Any, dict] = {}
        self._tools: dict[str, dict] = {}
        self._offered: dict[str, Any] = {}
        self._accepted: Any = None
        self._rejected: Any = None
        self._partial: str | None = None
        self._thinking: int | None = None
        self._compacted: dict | None = None

    # -- input ---------------------------------------------------------------------------------

    def feed_line(self, line: str) -> list[dict]:
        """One line of Claude's standard output; a line that is not a JSON object is counted and ignored."""
        if not line.strip():
            return []
        try:
            row = json.loads(line)
        except ValueError:
            self.ignored_lines += 1
            return []
        if not isinstance(row, dict):
            self.ignored_lines += 1
            return []
        return self.feed(row)

    def feed(self, row: dict) -> list[dict]:
        kind = row.get("type")
        if kind == "result":
            return self._on_result(row)
        if self._result is not None:
            return []  # task summaries after the result describe nothing new
        if kind == "system":
            return self._on_system(row)
        if kind == "stream_event":
            return self._on_stream(row)
        if kind == "assistant":
            return self._on_assistant(row)
        if kind == "user":
            return self._on_user(row)
        if kind == "rate_limit_event":
            self._on_rate_limit(row)
        return []

    def _new_id(self) -> str:
        identifier = f"{ITEM_PREFIX}{self._next}"
        self._next += 1
        return identifier

    def _on_system(self, row: dict) -> list[dict]:
        subtype = row.get("subtype")
        if subtype == "init":
            source = row.get("apiKeySource")
            if isinstance(source, str) and source not in ("", "none"):
                self.billing_source = source[:100]  # an API key would bill this call: stop it (finish)
            session = row.get("session_id")
            if not isinstance(session, str) or not session:
                return []
            if self.session_id is None:
                self.session_id = session
                if self.resumed is not None and session != self.resumed:
                    self.previous_cost = None
                return [{"type": "thread.started", "thread_id": session}, {"type": "turn.started"}]
            if session != self.session_id:
                self._mixed = True
            return []
        if subtype == "thinking_tokens":
            tokens = _count(row.get("estimated_tokens"))
            if tokens is not None and tokens != self._thinking:
                self._thinking = tokens
                return self._note(self._message, f"thinking ~{tokens} tokens; ")
        if subtype == "compact_boundary":
            # Claude Code summarized the conversation; finish() fails the stage (#1942).
            meta = row.get("compact_metadata")
            self._compacted = {
                key: meta[key]
                for key in ("trigger", "pre_tokens", "post_tokens")
                if isinstance(meta, dict) and isinstance(meta.get(key), (str, int)) and not isinstance(meta[key], bool)
            }
        return []

    def _on_rate_limit(self, row: dict) -> None:
        info = row.get("rate_limit_info")
        if not isinstance(info, dict):
            return
        window = info.get("rateLimitType")
        utilization = info.get("utilization")
        windows = info.get("unifiedWindows")
        if utilization is None and isinstance(windows, dict) and isinstance(windows.get(window), dict):
            utilization = windows[window].get("utilization")
        # overageStatus is "rejected" even on a successful call; it is never read.
        values = {"status": info.get("status"), "type": window, "resets_at": info.get("resetsAt")}
        values["utilization"] = utilization
        self._rate_limit = {key: value for key, value in values.items() if value is not None}

    def _on_result(self, row: dict) -> list[dict]:
        self._result = row
        session = row.get("session_id")
        if isinstance(session, str) and session and self.session_id is not None and session != self.session_id:
            self._mixed = True
        self.total_cost_usd = _money(row.get("total_cost_usd"))
        return []

    # -- streamed text and progress ------------------------------------------------------------

    def _throttled(self, entry: dict, kind: str) -> list[dict]:
        now = self.clock()
        if len(entry["text"]) <= entry["sent"]:
            return []
        if entry["at"] is not None and now - entry["at"] < THROTTLE_SECONDS:
            return []
        entry["at"], entry["sent"] = now, len(entry["text"])
        return [{"type": "item.updated", "item": {"id": entry["id"], "type": kind, "text": entry["text"]}}]

    def _grow(self, entry: dict, kind: str, piece: str) -> list[dict]:
        """Append ``piece`` to a text item: an append-only snapshot, rolled over at ITEM_TEXT_LIMIT bytes."""
        rows: list[dict] = []
        entry["text"] += piece
        while len(entry["text"].encode("utf-8")) > ITEM_TEXT_LIMIT:
            head = _prefix_bytes(entry["text"], ITEM_TEXT_LIMIT)
            rows.append(_completed(entry["id"] or self._new_id(), kind, head))
            entry.update(id=None, text=entry["text"][len(head) :], sent=0, at=None)
        if entry["text"] and entry["id"] is None:
            entry["id"] = self._new_id()
        return rows + self._throttled(entry, kind) if entry["id"] else rows

    def _close(self, entry: dict | None, kind: str) -> list[dict]:
        if not entry or not entry["text"]:
            return []
        return [_completed(entry["id"] or self._new_id(), kind, entry["text"])]

    def _note(self, message: Any, note: str) -> list[dict]:
        entry = self._progress.setdefault(message, {"id": None, "text": "", "sent": 0, "at": None})
        return self._grow(entry, "reasoning", note)

    def _on_stream(self, row: dict) -> list[dict]:
        if row.get("parent_tool_use_id"):
            return []
        event = row.get("event")
        if not isinstance(event, dict):
            return []
        kind = event.get("type")
        if kind == "message_start":
            message = _dict(event.get("message"))
            identifier = message.get("id") or row.get("api_message_id")
            self._message = identifier if isinstance(identifier, str) else None
            if self._message:
                self._streamed.add(self._message)
                if isinstance(message.get("usage"), dict):
                    self._usage[self._message] = dict(message["usage"])
            return []
        if kind == "message_delta":
            delta = _dict(event.get("delta"))
            if delta.get("stop_reason") == "refusal":
                self._refused = True
            if self._message and isinstance(event.get("usage"), dict):
                self._usage.setdefault(self._message, {}).update(event["usage"])
            return []
        if kind == "message_stop":
            return self._close(self._progress.pop(self._message, None), "reasoning")
        key = (self._message, event.get("index"))
        if kind == "content_block_start":
            block = _dict(event.get("content_block"))
            if block.get("type") == "tool_use":
                name = str(block.get("name") or "tool")
                self._blocks[key] = {
                    "kind": "tool",
                    "tool": name,
                    "input": 0,
                    "noted": None,
                    "partial": "" if name == "StructuredOutput" else None,
                }
            elif block.get("type") in ("text", "thinking"):
                self._blocks[key] = self._text_block(block["type"])
            return []
        if kind == "content_block_stop":
            entry = self._blocks.pop(key, None)
            return self._close(entry, entry["kind"]) if entry and entry["kind"] != "tool" else []
        if kind == "content_block_delta":
            delta = _dict(event.get("delta"))
            return self._on_delta(key, delta)
        return []

    @staticmethod
    def _text_block(kind: str) -> dict:
        return {
            "kind": "agent_message" if kind == "text" else "reasoning",
            "id": None,
            "text": "",
            "sent": 0,
            "at": None,
        }

    def _on_delta(self, key: tuple, delta: dict) -> list[dict]:
        kind = delta.get("type")
        entry = self._blocks.get(key)
        if kind in ("text_delta", "thinking_delta"):
            piece = delta.get("text") if kind == "text_delta" else delta.get("thinking")
            if not isinstance(piece, str) or not piece:
                return []
            if entry is None or entry["kind"] == "tool":
                entry = self._blocks[key] = self._text_block("text" if kind == "text_delta" else "thinking")
            return self._grow(entry, entry["kind"], piece)
        if kind == "input_json_delta" and entry is not None and entry["kind"] == "tool":
            piece = delta.get("partial_json")
            if not isinstance(piece, str) or not piece:
                return []
            entry["input"] += len(piece)
            if entry["partial"] is not None:
                entry["partial"] = (entry["partial"] + piece)[:PARTIAL_LIMIT]
                self._partial = entry["partial"]
            now = self.clock()
            if entry["noted"] is None or now - entry["noted"] >= THROTTLE_SECONDS:
                entry["noted"] = now
                return self._note(self._message, f"writing {entry['tool']} input: {entry['input']} chars; ")
        return []

    # -- whole messages and tool calls ---------------------------------------------------------

    def _on_assistant(self, row: dict) -> list[dict]:
        if row.get("parent_tool_use_id"):
            return []  # a subagent's turn (Task is not in --tools)
        message = _dict(row.get("message"))
        if row.get("error") or row.get("is_api_error_message"):
            # Claude Code's own API-error message: its words, never the model's prose.
            self._api_error = {"error": str(row.get("error") or row.get("api_error") or ""), "text": _text_of(message)}
            return []
        if message.get("stop_reason") == "refusal":
            self._refused = True
        streamed = message.get("id") in self._streamed
        rows: list[dict] = []
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                rows += self._tool_use(block)
            elif block.get("type") in ("text", "thinking") and not streamed:
                # A message Claude Code did not stream (a fake, an older client): whole items, bounded.
                text = block.get("text") if block["type"] == "text" else block.get("thinking")
                kind = "agent_message" if block["type"] == "text" else "reasoning"
                while isinstance(text, str) and text.strip():
                    head = _prefix_bytes(text, ITEM_TEXT_LIMIT)
                    rows.append(_completed(self._new_id(), kind, head))
                    text = text[len(head) :]
        return rows

    def _tool_use(self, block: dict) -> list[dict]:
        tool_id = block.get("id")
        if not isinstance(tool_id, str) or not tool_id or tool_id in self._tools or tool_id in self._offered:
            return []
        name = block.get("name")
        arguments = _dict(block.get("input"))
        if name == "StructuredOutput":
            self._offered[tool_id] = arguments
            return []
        identifier = self._new_id()
        if name == "Bash":
            command = arguments.get("command") if isinstance(arguments.get("command"), str) else ""
            self._tools[tool_id] = {"id": identifier, "type": "command_execution", "command": command}
            item = {"id": identifier, "type": "command_execution", "command": command, "status": "in_progress"}
        elif name in FILE_TOOLS:
            path = arguments.get("file_path") or arguments.get("notebook_path") or ""
            changes = [{"path": str(path), "kind": "add" if name == "Write" else "update"}]
            self._tools[tool_id] = {"id": identifier, "type": "file_change", "changes": changes}
            item = {"id": identifier, "type": "file_change", "changes": changes, "status": "in_progress"}
        else:
            label = str(name or "tool")
            self._tools[tool_id] = {"id": identifier, "type": "tool_call", "name": label}
            item = {"id": identifier, "type": "tool_call", "name": label, "status": "in_progress"}
        return [{"type": "item.started", "item": item}]

    def _on_user(self, row: dict) -> list[dict]:
        if row.get("parent_tool_use_id"):
            return []
        message = _dict(row.get("message"))
        content = message.get("content")
        if row.get("isSynthetic") and _text_of(message).startswith(_TRUNCATION):
            self._continuations += 1
            return []
        if not isinstance(content, list):
            return []
        results = [block for block in content if isinstance(block, dict) and block.get("type") == "tool_result"]
        # One tool_use_result describes one result; with several in a row none can be attributed.
        meta = row.get("tool_use_result") if len(results) == 1 else None
        rows: list[dict] = []
        for block in results:
            rows += self._tool_result(block, meta)
        return rows

    def _tool_result(self, block: dict, meta: Any) -> list[dict]:
        tool_id = block.get("tool_use_id")
        if isinstance(tool_id, str) and tool_id in self._offered:
            # Live 2.1.299 sends is_error null for an accepted StructuredOutput.
            if block.get("is_error") is True:
                self._rejected = self._offered[tool_id]
            else:
                self._accepted = self._offered[tool_id]
            return []
        tool = self._tools.pop(tool_id, None) if isinstance(tool_id, str) else None
        if tool is None:
            return [{"type": "item.completed", "item": {"id": self._new_id(), "type": "tool_call", "status": "failed"}}]
        if tool["type"] == "command_execution":
            code, output, unknown = bash_exit(block, meta)
            item = {
                "id": tool["id"],
                "type": "command_execution",
                "command": tool["command"],
                "exit_code": code,
                "aggregated_output": output,
                "status": "completed" if code == 0 else "failed",
            }
            if unknown:
                item["exit_code_unknown"] = unknown
            if isinstance(meta, dict) and meta.get("persistedOutputPath"):
                item["output_truncated"] = True
            return [{"type": "item.completed", "item": item}]
        detail = {"changes": tool["changes"]} if tool["type"] == "file_change" else {"name": tool["name"]}
        outcome = {"status": "failed" if block.get("is_error") is True else "completed"}
        return [{"type": "item.completed", "item": {"id": tool["id"], "type": tool["type"], **detail, **outcome}}]

    # -- the terminal row ----------------------------------------------------------------------

    def _flush(self) -> list[dict]:
        rows: list[dict] = []
        for entry in list(self._blocks.values()):
            if entry["kind"] != "tool":
                rows += self._close(entry, entry["kind"])
        self._blocks.clear()
        for entry in list(self._progress.values()):
            rows += self._close(entry, "reasoning")
        self._progress.clear()
        return rows

    def _cost(self) -> float | None:
        if self.total_cost_usd is None or self.previous_cost is None:
            return None
        delta = round(self.total_cost_usd - self.previous_cost, 10)
        return delta if delta > 0 else None

    def _cost_state(self) -> dict | None:
        """The CLI's cumulative cost-state observation for this session (#2592).

        Saved so the stage ledger can price an interrupted call from the CLI's own accounting:
        the cumulative total, plus the previous total a resume started from. No record when the
        session total is unknown, when streams mixed sessions, or when a resume's earlier total
        belongs to another session and the delta is unknowable.
        """
        if self._mixed or self.total_cost_usd is None:
            return None
        session = self.session_id or (self._result or {}).get("session_id")
        if not isinstance(session, str) or not session:
            return None
        if self.resumed is not None and self.previous_cost is None:
            return None
        record = {"session_id": session, "total_cost_usd": self.total_cost_usd}
        if self.resumed is not None:
            record["previous_total_usd"] = self.previous_cost
        record["updated_at"] = self.clock()
        return record

    def _partial_usage(self) -> dict | None:
        mapped = [usage_of(raw) or {} for raw in self._usage.values()]
        if not mapped:
            return None
        keys = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")
        usage = {key: sum(entry[key] for entry in mapped) for key in keys if all(key in entry for entry in mapped)}
        return usage or None

    def _diagnostics(self, exit_code: int, *, failed: bool) -> dict:
        """What Claude Code said about the call. A failed row keeps only its word-valued facts: the runner
        classifies a failure by matching its whole row text, where a session id, a cost or a reset time can
        contain "429" and a "rate_limit" key would read as a rate limit (the raw sidecar keeps the rest)."""
        result = self._result or {}
        denials = result.get("permission_denials")
        values = {
            "session_id": self.session_id or result.get("session_id"),
            "subtype": result.get("subtype"),
            "terminal_reason": result.get("terminal_reason"),
            "api_error": result.get("api_error") or (self._api_error or {}).get("error") or None,
            "api_error_status": result.get("api_error_status"),
            "stop_reason": result.get("stop_reason"),
            "num_turns": result.get("num_turns"),
            "permission_denials": len(denials) if isinstance(denials, list) else None,
            "total_cost_usd": self.total_cost_usd,
            "continuations": self._continuations,
            "rate_limit": dict(self._rate_limit) or None,
            "exit_code": exit_code,
        }
        kept = FAILED_DIAGNOSTICS if failed else values
        return {key: value for key, value in values.items() if value is not None and key in kept}

    def _terminal(self, exit_code: int, error: dict | None = None) -> dict:
        result = self._result
        values = {
            "type": "turn.failed" if error else "turn.completed",
            "error": error,
            "usage": usage_of(result.get("usage")) if result else None,
            "cost_usd": self._cost(),
            "cost_state": self._cost_state(),
            "claude": self._diagnostics(exit_code, failed=bool(error)),
        }
        return {key: value for key, value in values.items() if value is not None}

    def start_failure(self, executable: str, error: BaseException) -> dict:
        """T0: Claude Code could not be spawned."""
        message = f"Claude Code could not start at {executable}: {error}"[:500]
        return {"type": "turn.failed", "error": {"code": "provider_start_failed", "message": message}}

    def finish(self, exit_code: int, *, stderr_tail: str = "", terminated: bool = False) -> tuple[list[dict], int]:
        """The rows that end the stage and the translator's exit code (SPEC #717 §3.7, first match wins)."""
        result = self._result
        api = self._api_error or {}
        failing = exit_code or 1
        tail = _ANSI.sub("", stderr_tail or "").strip()
        is_error = result is not None and result.get("is_error") is True
        kind = (result or {}).get("api_error") or api.get("error") or None
        text = (api.get("text") or (str(result.get("result") or "") if result and is_error else "")).strip()[:300]
        clean = 0 if exit_code == 0 or (exit_code == 1 and is_error) else exit_code
        report = (result or {}).get("structured_output")
        if self.billing_source is not None:
            message = (
                f"Claude Code would bill an API key ({self.billing_source}) instead of your Claude subscription "
                "login; the stage was stopped. claude subscription selection will not silently change billing routes"
            )
            failure = {"code": BILLING_ROUTE, "message": message}
            return self._flush() + [self._terminal(exit_code, failure)], 1  # stopped by this translator
        if self._mixed:
            message = "Claude Code reported more than one session; the stage is uncertain"
            return self._flush() + [self._terminal(1, {"code": "session_changed", "message": message})], 1
        if self.session_id is None and result is None:
            # The runner classifies a failure by its row text, where a session id can contain "429";
            # the raw sidecar keeps the full stderr.
            tail = _UUID.sub("<session>", tail.replace(self.resumed, "<session>") if self.resumed else tail)
            if self.resumed and "No conversation found with session ID" in tail:
                message = (
                    f"Claude Code has no saved session to resume on this machine ({tail[-300:]}); "
                    "the same-session correction could not resume it"
                )
                return [self._terminal(exit_code, {"code": "session_not_found", "message": message})], failing
            said = tail[-500:] or "no output"
            message = f"Claude Code exited {exit_code} before starting a session: {said}"
            return [self._terminal(exit_code, {"code": "provider_start_failed", "message": message})], failing
        if self._compacted is not None:
            # The conversation was summarized mid-call: whatever Claude Code produced afterwards, report
            # included, came from a summary rather than the whole handoff, so the stage is not whole (#1942).
            meta = self._compacted
            seen = ", ".join(f"{key} {meta[key]}" for key in ("trigger", "pre_tokens", "post_tokens") if key in meta)
            message = (
                "Claude Code compacted the conversation"
                + (f" ({seen})" if seen else "")
                + "; the response was produced from a summary, not the whole handoff, and is not accepted"
            )
            return self._flush() + [self._terminal(exit_code, {"code": COMPACTED, "message": message})], failing
        # Claude Code withdraws an accepted StructuredOutput when a model fallback tombstones its attempt
        # (stream-json never shows the tombstone); this result then says no report survived (T7).
        retracted = (result or {}).get("subtype") == "error_max_structured_output_retries" or (
            (result or {}).get("terminal_reason") == "structured_output_retry_exhausted"
        )
        if result is not None and (
            (not is_error and isinstance(report, dict)) or (is_error and self._accepted is not None and not retracted)
        ):
            value = report if not is_error and isinstance(report, dict) else self._accepted
            rows = self._flush() + [_completed(REPORT_ITEM, "agent_message", _compact(value))]
            return rows + [self._terminal(exit_code)], clean
        if is_error and kind == "max_output_tokens" and "context window" not in text.lower():
            rows = self._flush()
            if self._partial:
                rows.append(_completed(PARTIAL_ITEM, "agent_message", self._partial[:PARTIAL_LIMIT]))
            message = (
                f"Claude Code exhausted its output token limit ({text}); {self._continuations} automatic "
                "continuations. The attempt is incomplete; review saved work before recovery."
            )
            return rows + [self._terminal(exit_code, {"code": "output_token_limit", "message": message})], 0
        if (kind == "max_output_tokens" and "context window" in text.lower()) or (result or {}).get(
            "terminal_reason"
        ) == "prompt_too_long":
            message = f"Claude Code stopped: the model reached its context window limit ({text or 'prompt too long'})"
            failure = {"code": "context_window_exceeded", "message": message}
            return self._flush() + [self._terminal(exit_code, failure)], failing
        if self._refused:
            message = "Claude refused the response (stop reason: refusal); content filter"
            return self._flush() + [self._terminal(exit_code, {"code": "content_filter", "message": message})], failing
        if result is not None and ((not is_error and not isinstance(report, dict)) or retracted):
            rows = self._flush()
            if self._rejected is not None:
                rows.append(_completed(REJECTED_ITEM, "agent_message", _compact(self._rejected)[:REJECTED_LIMIT]))
            prose = result.get("result")
            if not is_error and isinstance(prose, str) and prose:
                rows.append(_completed(RESULT_TEXT_ITEM, "agent_message", prose[:RESULT_TEXT_LIMIT]))
            return rows + [self._terminal(exit_code)], clean
        if result is not None and is_error:
            before, failure = self._api_failure(result, api, kind, text)
            return self._flush() + before + [self._terminal(exit_code, failure)], failing
        rows = self._flush()
        partial = self._partial_usage()
        if partial:
            rows.append({"type": "usage.partial", "usage": partial})
        message = (
            f"Claude Code stream ended without a result row (exit {exit_code}); the attempt is incomplete; "
            "review saved work before recovery."
        )
        rows.append(self._terminal(exit_code, {"code": "incomplete_turn", "message": message}))
        return rows, 143 if terminated else failing

    def _api_failure(self, result: dict, api: dict, kind: Any, text: str) -> tuple[list[dict], dict]:
        """T8: Claude Code's error, worded for the runner's length → refusal → capacity → budget → rate order."""
        error = api.get("error")
        status = result.get("api_error_status")
        if (
            kind in USAGE_LIMIT_ERRORS
            or error == "billing_error"
            or (error == "rate_limit" and self._rate_limit.get("status") == "rejected")
            or _LIMIT_WORDS.search(text)
        ):
            resets = _money(self._rate_limit.get("resets_at"))
            when = ""
            if resets is not None:
                stamp = dt.datetime.fromtimestamp(resets, tz=dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                when = f" (resets {stamp})"
            return [], {
                "code": "usage_limit_reached",
                "message": f"Claude subscription usage limit reached{when}: {text}",
            }
        if error == "rate_limit" or status == 429:
            return [], {"code": "rate_limit_exceeded", "message": f"Claude API rate limit (429): {text}"}
        if error == "overloaded" or status == 529:
            return [], {"code": "model_overloaded", "message": f"Claude model_overloaded (529): {text}"}
        if error in AUTH_ERRORS or status in (401, 403):
            code = 403 if status == 403 else 401
            before = [{"type": "error", "error": {"name": "APIError", "message": text, "data": {"statusCode": code}}}]
            message = f"Claude Code authentication failed ({kind or status}): {text}; run `claude auth login`"
            return before, {"code": "authentication_failed", "message": message}
        errors = result.get("errors")
        said = "; ".join(str(entry) for entry in errors) if isinstance(errors, list) and errors else text
        message = f"Claude Code {result.get('subtype') or kind} ({status or 'no status'}): {said}"[:1000]
        return [], {"code": "provider_error", "message": message}


# -- the process ---------------------------------------------------------------------------------


class _ArgumentError(Exception):
    pass


class _Sidecar:
    """The raw Claude rows (stream deltas left out), for people reading a stage after the fact."""

    def __init__(self, path: str | None) -> None:
        self.path = Path(path) if path else None
        self.size = 0
        self.handle: Any = None

    def add(self, row: dict) -> None:
        if row.get("type") == "stream_event":
            return
        if row.get("type") == "system" and row.get("subtype") == "init":
            row = {key: row[key] for key in RAW_INIT_KEYS if key in row}
        self.write(row)

    def write(self, row: dict) -> None:
        if self.path is None:
            return
        data = (_compact(row) + "\n").encode("utf-8")
        if self.size + len(data) > RAW_LIMIT:
            self.path = None
            return
        try:
            if self.handle is None:
                self.handle = self.path.open("ab")
            self.handle.write(data)
            self.handle.flush()
        except OSError:
            self.path = None
            return
        self.size += len(data)

    def close(self) -> None:
        if self.handle is not None:
            with contextlib.suppress(OSError):
                self.handle.close()


def _emit(row: dict) -> None:
    sys.stdout.write(_compact(row) + "\n")
    sys.stdout.flush()


def _parse(arguments: list[str]) -> tuple[dict[str, str], list[str]]:
    if "--" not in arguments:
        raise _ArgumentError("missing -- before the claude command line")
    split = arguments.index("--")
    head, claude = arguments[:split], arguments[split + 1 :]
    options: dict[str, str] = {}
    index = 0
    while index < len(head):
        name = head[index]
        if name not in ("--ledger", "--schema", "--raw", "--resumed") or index + 1 >= len(head):
            raise _ArgumentError(f"unexpected argument {name!r}")
        options[name] = head[index + 1]
        index += 2
    if "--ledger" not in options or "--schema" not in options:
        raise _ArgumentError("--ledger and --schema are required")
    if len(claude) < 2 or claude[1] != "-p":
        raise _ArgumentError("the claude command line must start with CLAUDE -p")
    return options, claude


def _ledger_total(ledger: Path, session: str) -> float | None:
    if not _SESSION_NAME.fullmatch(session):
        return None
    try:
        value = json.loads((ledger / f"{session}.json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("session_id") != session:
        return None
    return _money(value.get("total_cost_usd"))


def _write_ledger(ledger: Path, translator: Translator) -> None:
    session, total = translator.session_id, translator.total_cost_usd
    if translator._mixed or session is None or total is None or not _SESSION_NAME.fullmatch(session):
        return
    record = {"session_id": session, "total_cost_usd": total, "updated_at": time.time()}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=ledger, prefix=".tmp-", suffix=".json", delete=False) as handle:
            temporary = handle.name
            handle.write(_compact(record))
        os.replace(temporary, ledger / f"{session}.json")
    except OSError:
        if temporary:
            with contextlib.suppress(OSError):
                os.unlink(temporary)


def _run(arguments: list[str]) -> int:
    options, claude = _parse(arguments)
    try:
        schema = json.loads(Path(options["--schema"]).read_text())
    except (OSError, ValueError) as error:
        raise _ArgumentError(f"cannot read the report schema: {error}") from error
    # Claude Code 2.1.314 checks --json-schema in strict mode and refuses a keyword it does not
    # know, so AutoCode's own top-level annotations (``x-`` keys, such as the bounded report-edit
    # offer's ``x-autocode-report-edits``) stay in the schema file for the runner and never reach it.
    if isinstance(schema, dict):
        schema = {key: value for key, value in schema.items() if not key.startswith("x-")}
    compact = _compact(schema)
    if len(compact.encode("utf-8")) > SCHEMA_LIMIT:
        raise _ArgumentError("report schema is larger than one argv string (128 KiB); no provider request was launched")
    command = [claude[0], "-p", "--json-schema", compact, *claude[2:]]
    ledger = Path(options["--ledger"])
    ledger.mkdir(mode=0o700, parents=True, exist_ok=True)
    resumed = options.get("--resumed")
    previous = 0.0 if resumed is None else _ledger_total(ledger, resumed)
    translator = Translator(resumed=resumed, previous_cost=previous)
    raw = _Sidecar(options.get("--raw"))
    try:
        try:
            child = subprocess.Popen(command, stdin=None, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as error:
            _emit(translator.start_failure(claude[0], error))
            raw.write({"type": "exit", "code": None, "error": str(error)[:500]})
            return 127 if isinstance(error, FileNotFoundError) else 126
        return _supervise(child, translator, raw, ledger)
    finally:
        raw.close()


def _supervise(child: subprocess.Popen, translator: Translator, raw: _Sidecar, ledger: Path) -> int:
    tail = bytearray()
    terminated: list[int] = []
    stopped: list[bool] = []

    stream = child.stderr
    assert stream is not None

    def drain() -> None:
        with contextlib.suppress(BaseException):  # a reader failure must never print to the event log
            for chunk in iter(lambda: stream.read(4096), b""):
                tail.extend(chunk)
                del tail[:-STDERR_LIMIT]

    def stop(signum: int, _frame: Any) -> None:
        terminated.append(signum)
        with contextlib.suppress(OSError):
            child.terminate()

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    for name in ("SIGTERM", "SIGINT", "SIGHUP"):
        with contextlib.suppress(AttributeError, ValueError, OSError):
            signal.signal(getattr(signal, name), stop)
    try:
        assert child.stdout is not None
        for line in child.stdout:
            text = line.decode("utf-8", "replace")
            try:
                row = json.loads(text)
            except ValueError:
                if text.strip():
                    translator.ignored_lines += 1
                continue
            if not isinstance(row, dict):
                translator.ignored_lines += 1
                continue
            raw.add(row)
            for event in translator.feed(row):
                _emit(event)
            if translator.billing_source is not None and not stopped:
                stopped.append(True)  # before Claude Code's first request, as far as the stream allows
                with contextlib.suppress(OSError):
                    child.terminate()
        code = child.wait()
    except BaseException:
        with contextlib.suppress(OSError):
            child.kill()
        raise
    reader.join(timeout=5)
    if code < 0:
        code = 128 - code
    stderr_text = _ANSI.sub("", bytes(tail).decode("utf-8", "replace"))
    rows, exit_code = translator.finish(code, stderr_tail=stderr_text, terminated=bool(terminated))
    for event in rows:
        _emit(event)
    raw.write({"type": "stderr", "text": stderr_text})
    raw.write({"type": "exit", "code": code})
    _write_ledger(ledger, translator)
    return 143 if terminated else exit_code


def main(argv: list[str] | None = None) -> int:
    """Run one stage; every outcome, including this program's own failure, ends in one terminal row."""
    warnings.simplefilter("ignore")
    arguments = sys.argv[1:] if argv is None else list(argv)
    try:
        return _run(arguments)
    except _ArgumentError as error:
        code, message = 2, f"claude_stream: {error}"
    except BaseException as error:  # noqa: BLE001 - one terminal row, never a traceback on stderr
        code, message = 70, f"claude_stream: {type(error).__name__}: {error}"
    with contextlib.suppress(BaseException):
        _emit({"type": "turn.failed", "error": {"code": "adapter_error", "message": message[:500]}})
    return code


if __name__ == "__main__":
    sys.exit(main())
