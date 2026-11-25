"""Translate one headless ``gemini --output-format stream-json`` stage into Codex-shaped events.

The built-in gemini provider (``providers/gemini.py``) launches every stage as::

    python -I gemini_stream.py [--raw PATH] [--report PATH] -- GEMINI --model MODEL --output-format stream-json ...

This program runs Gemini CLI with the stage prompt on its inherited standard input and writes only
Codex rows to standard output, one JSON object per line: ``thread.started``, ``turn.started``,
``item.started``/``item.updated``/``item.completed`` and one terminal ``turn.completed`` or
``turn.failed``. The runner's Codex readers (sessions, usage, command evidence, the output cap, the
idle watchdog) then apply unchanged, as they do for the claude provider's translator.

- Gemini CLI's stream rows are ``init``, ``message`` (the user's prompt, then the assistant's text in
  ``delta`` pieces), ``tool_use``, ``tool_result``, ``error`` and one ``result`` with the call's token
  counts (Gemini CLI 0.71).
- A ``run_shell_command`` call is a ``command_execution`` item whose exit code is ``null``: Gemini CLI
  reports a command that exits 3 as ``status: "success"`` with its output and no code, so no exit code
  can be read from the stream, and no evidence check accepts a null one. Evidence comes from the
  ``capture_command`` receipts, which record the exit code themselves.
- The report is the assistant's last message, the text after its last tool call: when it is one JSON
  object (bare, or the whole message in one fenced block) it is written as the ``structured_output``
  item ``providers.gemini.final_report`` reads; any other text is kept as ``result_text`` so the
  runner's format correction sees what the model said.
- ``--report PATH`` receives that last message once the turn completes (the compact JSON object, or
  the text as given). Gemini CLI has no session the runner resumes, so the runner reads a stage as
  finished by its report file, as it does for a configured tool; the report it admits is still the
  one read back from these events.
- Gemini CLI exits 41 when it has no usable login (and prints why on standard error). That becomes
  ``turn.failed{code: "authentication_failed"}`` with the reason, including the Workspace account that
  needs ``GOOGLE_CLOUD_PROJECT``.
- Nothing is ever written to standard error: the runner merges it into the stage's event log, where
  one non-JSON line would defeat the output-cap check.

Standard library only; it imports nothing from AutoCode, so it runs under ``python -I``.
Reviewer rule: a change to what these rows mean bumps ``providers.gemini.ADAPTER``.
"""

from __future__ import annotations

import contextlib
import json
import re
import signal
import subprocess
import sys
import threading
import time
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPORT_ITEM = "structured_output"
RESULT_TEXT_ITEM = "result_text"
ITEM_PREFIX = "item_"
THROTTLE_SECONDS = 2.0
ITEM_TEXT_LIMIT = 16 * 1024
OUTPUT_LIMIT = 64 * 1024  # head and tail of a command's output each
RESULT_TEXT_LIMIT = 64 * 1024
RAW_LIMIT = 64 * 1024 * 1024
STDERR_LIMIT = 16 * 1024
SHELL_TOOL = "run_shell_command"
FILE_TOOLS = {"write_file": "add", "replace": "update"}
DISPLAY_INPUTS = ("file_path", "dir_path", "path", "pattern", "query", "url", "description")
DISPLAY_LIMIT = 200
# Gemini CLI's exit code for a missing or unusable login (FatalAuthenticationError, 0.71).
AUTH_EXIT = 41
UNKNOWN_EXIT = "Gemini CLI does not report a command's exit code in its stream"
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_UUID = re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
_FENCED = re.compile(r"\A```[A-Za-z]*\s*\n(.*)\n```\Z", re.DOTALL)
# Gemini CLI's own words for a used-up allowance; RESOURCE_EXHAUSTED alone reads as capacity to the runner.
_QUOTA = re.compile(r"(?i)quota|exhausted your capacity|usage limit")
_PROJECT = re.compile(r"GOOGLE_CLOUD_PROJECT")
WORKSPACE_HINT = (
    "Your Google account is a Workspace account, which Gemini Code Assist bills to a Google Cloud project: "
    'add `export GOOGLE_CLOUD_PROJECT="your-project-id"` to your shell profile, open a new terminal and run '
    "`gemini` once to sign in"
)
# Startup notices Gemini CLI prints on standard error before any failure; never a reason.
_NOTICES = re.compile(r"(?i)^(warning: 256-color|yolo mode is enabled|loaded cached credentials)")


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def bound(text: str, limit: int = OUTPUT_LIMIT) -> str:
    """The first and last ``limit`` characters of ``text``, with what was left out in between."""
    if len(text) <= 2 * limit:
        return text
    return f"{text[:limit]}\n[… {len(text) - 2 * limit} characters omitted …]\n{text[-limit:]}"


def _prefix_bytes(text: str, limit: int) -> str:
    return text.encode("utf-8")[:limit].decode("utf-8", "ignore")


def _compact(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"))


def _completed(identifier: str, kind: str, text: str) -> dict:
    return {"type": "item.completed", "item": {"id": identifier, "type": kind, "text": text}}


def report_object(text: str) -> dict | None:
    """The report in a final message: one JSON object, bare or as the whole message in one fenced block."""
    body = text.strip()
    fenced = _FENCED.match(body)
    if fenced:
        body = fenced.group(1).strip()
    if not body.startswith("{"):
        return None
    try:
        value = json.loads(body)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def usage_of(stats: Any) -> dict | None:
    """Gemini CLI's ``result.stats`` in Codex terms; a key whose input is not a whole number is left out."""
    if not isinstance(stats, dict):
        return None
    prompt, cached, out = (_count(stats.get(key)) for key in ("input_tokens", "cached", "output_tokens"))
    usage = {}
    if prompt is not None:
        usage["input_tokens"] = prompt
        if cached is not None and cached <= prompt:
            usage["cached_input_tokens"] = cached
            usage["fresh_input_tokens"] = prompt - cached
    if out is not None:
        usage["output_tokens"] = out
    return usage or None


def stderr_reason(tail: str) -> str:
    """What Gemini CLI said on standard error, without its startup notices, stack frames or session ids."""
    lines = []
    for line in _ANSI.sub("", tail or "").splitlines():
        stripped = line.strip()
        if not stripped or _NOTICES.match(stripped) or stripped.startswith("at "):
            continue
        lines.append(stripped)
    return _UUID.sub("<session>", " ".join(lines))[-500:]


class Translator:
    """Gemini CLI stream-json rows in, Codex rows out. Tests drive it without processes."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.session_id: str | None = None
        self.ignored_lines = 0
        self.answer: str | None = None  # the completed last message, once the turn succeeds
        self._next = 0
        self._mixed = False
        self._result: dict | None = None
        self._errors: list[str] = []
        self._text: dict = self._new_text()
        self._final = ""
        self._tools: dict[str, dict] = {}

    # -- input ---------------------------------------------------------------------------------

    def feed_line(self, line: str) -> list[dict]:
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
        if kind == "init":
            return self._on_init(row)
        if kind == "message":
            return self._on_message(row)
        if kind == "tool_use":
            return self._on_tool_use(row)
        if kind == "tool_result":
            return self._on_tool_result(row)
        if kind == "error":
            message = row.get("message")
            if isinstance(message, str) and message.strip():
                self._errors.append(message.strip()[:500])
            return []
        if kind == "result":
            self._result = row
            return []
        return []

    def _new_id(self) -> str:
        identifier = f"{ITEM_PREFIX}{self._next}"
        self._next += 1
        return identifier

    @staticmethod
    def _new_text() -> dict:
        return {"id": None, "text": "", "sent": 0, "at": None}

    def _on_init(self, row: dict) -> list[dict]:
        session = row.get("session_id")
        if not isinstance(session, str) or not session:
            return []
        if self.session_id is None:
            self.session_id = session
            return [{"type": "thread.started", "thread_id": session}, {"type": "turn.started"}]
        if session != self.session_id:
            self._mixed = True
        return []

    def _on_message(self, row: dict) -> list[dict]:
        if row.get("role") != "assistant":
            return []
        piece = row.get("content")
        if not isinstance(piece, str) or not piece:
            return []
        if row.get("delta") is not True and self._text["text"]:
            # A whole message after streamed pieces starts a new one.
            rows = self._close_text()
        else:
            rows = []
        return rows + self._grow(piece)

    def _grow(self, piece: str) -> list[dict]:
        entry = self._text
        entry["text"] += piece
        self._final += piece
        rows: list[dict] = []
        while len(entry["text"].encode("utf-8")) > ITEM_TEXT_LIMIT:
            head = _prefix_bytes(entry["text"], ITEM_TEXT_LIMIT)
            rows.append(_completed(entry["id"] or self._new_id(), "agent_message", head))
            entry.update(id=None, text=entry["text"][len(head) :], sent=0, at=None)
        if entry["text"] and entry["id"] is None:
            entry["id"] = self._new_id()
        now = self.clock()
        if (
            entry["id"]
            and len(entry["text"]) > entry["sent"]
            and (entry["at"] is None or now - entry["at"] >= THROTTLE_SECONDS)
        ):
            entry["at"], entry["sent"] = now, len(entry["text"])
            rows.append(
                {"type": "item.updated", "item": {"id": entry["id"], "type": "agent_message", "text": entry["text"]}}
            )
        return rows

    def _close_text(self) -> list[dict]:
        entry, self._text = self._text, self._new_text()
        if not entry["text"]:
            return []
        return [_completed(entry["id"] or self._new_id(), "agent_message", entry["text"])]

    def _on_tool_use(self, row: dict) -> list[dict]:
        tool_id = row.get("tool_id")
        if not isinstance(tool_id, str) or not tool_id or tool_id in self._tools:
            return []
        # The report is the text after the last tool call: anything said before a call is not it.
        rows = self._close_text()
        self._final = ""
        name = str(row.get("tool_name") or "tool")
        arguments = _dict(row.get("parameters"))
        identifier = self._new_id()
        if name == SHELL_TOOL:
            command = arguments.get("command") if isinstance(arguments.get("command"), str) else ""
            tool = {"id": identifier, "type": "command_execution", "command": command}
        elif name in FILE_TOOLS:
            path = arguments.get("file_path") or arguments.get("path") or ""
            tool = {"id": identifier, "type": "file_change", "changes": [{"path": str(path), "kind": FILE_TOOLS[name]}]}
        else:
            tool = {"id": identifier, "type": "tool_call", "name": name}
            shown = {
                key: value[:DISPLAY_LIMIT]
                for key in DISPLAY_INPUTS
                if isinstance(value := arguments.get(key), str) and value.strip()
            }
            if shown:
                tool["display_input"] = shown
        self._tools[tool_id] = tool
        return rows + [{"type": "item.started", "item": {**tool, "status": "in_progress"}}]

    def _on_tool_result(self, row: dict) -> list[dict]:
        tool_id = row.get("tool_id")
        tool = self._tools.pop(tool_id, None) if isinstance(tool_id, str) else None
        failed = row.get("status") != "success"
        if tool is None:
            return [{"type": "item.completed", "item": {"id": self._new_id(), "type": "tool_call", "status": "failed"}}]
        if tool["type"] == "command_execution":
            output = row.get("output") if isinstance(row.get("output"), str) else ""
            error = _dict(row.get("error")).get("message")
            if failed and isinstance(error, str) and error and error not in output:
                output = f"{output}\n{error}" if output else error
            item = {
                **tool,
                "exit_code": None,
                "exit_code_unknown": UNKNOWN_EXIT,
                "aggregated_output": bound(output),
                "status": "failed" if failed else "completed",
            }
            return [{"type": "item.completed", "item": item}]
        return [{"type": "item.completed", "item": {**tool, "status": "failed" if failed else "completed"}}]

    # -- the terminal row ----------------------------------------------------------------------

    def _flush(self) -> list[dict]:
        rows = self._close_text()
        for tool in self._tools.values():
            rows.append({"type": "item.completed", "item": {**tool, "status": "failed"}})
        self._tools.clear()
        return rows

    def _terminal(self, exit_code: int, error: dict | None = None) -> dict:
        stats = (self._result or {}).get("stats")
        values = {
            "type": "turn.failed" if error else "turn.completed",
            "error": error,
            "usage": usage_of(stats),
            "gemini": {
                key: value
                for key, value in {
                    "session_id": self.session_id,
                    "status": (self._result or {}).get("status"),
                    "tool_calls": _count(_dict(stats).get("tool_calls")),
                    "duration_ms": _count(_dict(stats).get("duration_ms")),
                    "exit_code": exit_code,
                }.items()
                if value is not None and (not error or key in ("status", "exit_code"))
            },
        }
        return {key: value for key, value in values.items() if value is not None}

    def start_failure(self, executable: str, error: BaseException) -> dict:
        message = f"Gemini CLI could not start at {executable}: {error}"[:500]
        return {"type": "turn.failed", "error": {"code": "provider_start_failed", "message": message}}

    def finish(self, exit_code: int, *, stderr_tail: str = "", terminated: bool = False) -> tuple[list[dict], int]:
        """The rows that end the stage and the translator's exit code (first match wins)."""
        failing = exit_code or 1
        said = stderr_reason(stderr_tail)
        result = self._result
        if exit_code == AUTH_EXIT or (result is None and self.session_id is None and _PROJECT.search(said)):
            if _PROJECT.search(said):
                message = f"Gemini CLI cannot sign in: {WORKSPACE_HINT}. No stage work was done"
            else:
                message = (
                    f"Gemini CLI has no usable login ({said or 'no reason given'}); run `gemini` in a terminal and "
                    "choose Sign in with Google, then resume"
                )
            failure = {"code": "authentication_failed", "message": message}
            # The runner reads a provider-owned 401 as a login to renew (autocode_provider_auth), as for Claude Code.
            rejected = {
                "type": "error",
                "error": {"name": "AuthError", "message": message, "data": {"statusCode": 401}},
            }
            return self._flush() + [rejected, self._terminal(exit_code, failure)], failing
        if self._mixed:
            message = "Gemini CLI reported more than one session; the stage is uncertain"
            return self._flush() + [self._terminal(1, {"code": "session_changed", "message": message})], 1
        if self.session_id is None and result is None:
            message = f"Gemini CLI exited {exit_code} before starting a session: {said or 'no output'}"
            return [self._terminal(exit_code, {"code": "provider_start_failed", "message": message})], failing
        if result is not None and result.get("status") == "success":
            final = self._final
            rows = self._flush()
            report = report_object(final)
            if report is not None:
                self.answer = _compact(report)
                rows.append(_completed(REPORT_ITEM, "agent_message", self.answer))
            elif final.strip():
                self.answer = final[:RESULT_TEXT_LIMIT]
                rows.append(_completed(RESULT_TEXT_ITEM, "agent_message", self.answer))
            return rows + [self._terminal(exit_code)], 0
        if result is not None:
            error = _dict(result.get("error"))
            text = str(error.get("message") or "; ".join(self._errors) or said or "no reason given")[:1000]
            if _QUOTA.search(text):
                # Keep the runner's quota reading: RESOURCE_EXHAUSTED alone reads as model capacity.
                cleaned = re.sub(r"(?i)resource[_ ]exhausted", "", text)
                failure = {"code": "usage_limit_reached", "message": f"Gemini quota reached: {cleaned}"}
            elif re.search(r"\b429\b", text):
                failure = {"code": "rate_limit_exceeded", "message": f"Gemini API rate limit (429): {text}"}
            elif re.search(r"\b(401|403)\b|UNAUTHENTICATED|PERMISSION_DENIED", text):
                failure = {"code": "authentication_failed", "message": f"Gemini CLI authentication failed: {text}"}
            else:
                failure = {"code": "provider_error", "message": f"Gemini CLI {error.get('type') or 'error'}: {text}"}
            return self._flush() + [self._terminal(exit_code, failure)], failing
        message = (
            f"Gemini CLI stream ended without a result row (exit {exit_code}); the attempt is incomplete; "
            "review saved work before recovery."
        )
        rows = self._flush()
        rows.append(self._terminal(exit_code, {"code": "incomplete_turn", "message": message}))
        return rows, 143 if terminated else failing


# -- the process ---------------------------------------------------------------------------------


class _ArgumentError(Exception):
    pass


class _Sidecar:
    """The raw Gemini rows, for people reading a stage after the fact."""

    def __init__(self, path: str | None) -> None:
        self.path = Path(path) if path else None
        self.size = 0
        self.handle: Any = None

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
        raise _ArgumentError("missing -- before the gemini command line")
    split = arguments.index("--")
    head, gemini = arguments[:split], arguments[split + 1 :]
    options: dict[str, str] = {}
    index = 0
    while index < len(head):
        name = head[index]
        if name not in ("--raw", "--report") or name in options or index + 1 >= len(head):
            raise _ArgumentError(f"unexpected argument {name!r}")
        options[name] = head[index + 1]
        index += 2
    if not gemini or "stream-json" not in gemini:
        raise _ArgumentError("the gemini command line must ask for --output-format stream-json")
    return options, gemini


def _run(arguments: list[str]) -> int:
    options, command = _parse(arguments)
    translator = Translator()
    raw = _Sidecar(options.get("--raw"))
    try:
        try:
            child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as error:
            _emit(translator.start_failure(command[0], error))
            raw.write({"type": "exit", "code": None, "error": str(error)[:500]})
            return 127 if isinstance(error, FileNotFoundError) else 126
        code = _supervise(child, translator, raw)
        if code == 0 and translator.answer is not None and options.get("--report"):
            _write_report(Path(options["--report"]), translator.answer)
        return code
    finally:
        raw.close()


def _write_report(path: Path, text: str) -> None:
    """The completed answer, replaced whole so a reader never sees half of it."""
    partial = path.with_name(f".{path.name}.partial")
    partial.write_text(text + "\n", encoding="utf-8")
    partial.replace(path)


def _supervise(child: subprocess.Popen, translator: Translator, raw: _Sidecar) -> int:
    tail = bytearray()
    terminated: list[int] = []
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
            raw.write(row)
            for event in translator.feed(row):
                _emit(event)
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
    return 143 if terminated else exit_code


def main(argv: list[str] | None = None) -> int:
    """Run one stage; every outcome, including this program's own failure, ends in one terminal row."""
    warnings.simplefilter("ignore")
    arguments = sys.argv[1:] if argv is None else list(argv)
    try:
        return _run(arguments)
    except _ArgumentError as error:
        code, message = 2, f"gemini_stream: {error}"
    except BaseException as error:  # noqa: BLE001 - one terminal row, never a traceback on stderr
        code, message = 70, f"gemini_stream: {type(error).__name__}: {error}"
    with contextlib.suppress(BaseException):
        _emit({"type": "turn.failed", "error": {"code": "adapter_error", "message": message[:500]}})
    return code


if __name__ == "__main__":
    sys.exit(main())
