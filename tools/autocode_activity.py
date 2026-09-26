"""Bounded, read-only liveness tracking for raw provider JSONL event streams.

Liveness is evidence of activity, never evidence that the task is correct.  Tool
output and repeated events do not buy more execution time.  Process fallback is
deliberately conservative: identity metadata cannot distinguish a quiet tool
from a persistent provider wrapper. Recognizable MCP server executables are not
tool evidence on their own. The bounded interval renews only when a new tool
completion arrives; unknown concurrent tools cannot be timed individually.
"""
from __future__ import annotations

import codecs
import hashlib
import json
import os
from pathlib import Path
import threading
import time


class _ProjectedJsonLine:
    """Validate a JSONL record while bounding retained string-token contents.

    Oversized string *tokens* become null. Quoted output stays opaque, including
    embedded braces and escaped quotes. Full lexical validation still covers
    discarded contents; json.loads then validates the retained JSON structure.
    A truncated object key cannot acquire another key's meaning: null keys are
    invalid JSON, so that whole record is rejected.
    """
    def __init__(self, max_bytes, max_string_bytes):
        self.data = bytearray()
        self.max_bytes = max_bytes
        self.max_string_bytes = max_string_bytes
        self.decoder = codecs.getincrementaldecoder('utf-8')('strict')
        self.invalid = False
        self.in_string = False
        self.escaped = False
        self.unicode_digits = 0
        self.string_start = 0
        self.string_bytes = 0
        self.string_discarded = False

    def feed(self, fragment):
        if self.invalid:
            return
        try:
            self.decoder.decode(fragment)
        except UnicodeError:
            self.invalid = True
            return
        for byte in fragment:
            if self.in_string:
                self.string_bytes += 1
                if self.unicode_digits:
                    if byte not in b'0123456789abcdefABCDEF':
                        self.invalid = True
                        return
                    self.unicode_digits -= 1
                elif self.escaped:
                    self.escaped = False
                    if byte == 117:  # u
                        self.unicode_digits = 4
                    elif byte not in b'"\\/bfnrt':
                        self.invalid = True
                        return
                elif byte == 92:  # backslash
                    self.escaped = True
                elif byte == 34:  # quote
                    self.in_string = False
                elif byte < 32:
                    self.invalid = True
                    return
                if not self.string_discarded and self.string_bytes > self.max_string_bytes:
                    del self.data[self.string_start:]
                    self.data.extend(b'null')
                    self.string_discarded = True
                elif not self.string_discarded:
                    self.data.append(byte)
            else:
                if byte == 34:
                    self.in_string = True
                    self.string_start = len(self.data)
                    self.string_bytes = 1
                    self.string_discarded = False
                self.data.append(byte)
            if len(self.data) > self.max_bytes:
                self.data.clear()
                self.invalid = True
                return

    def finish(self):
        if self.invalid or self.in_string:
            return None
        try:
            self.decoder.decode(b'', final=True)
            return json.loads(self.data)
        except (ValueError, UnicodeError, RecursionError):
            return None


class ActivityMonitor:
    MAX_READ_BYTES = 1024 * 1024
    MAX_LINE_BYTES = 1024 * 1024
    MAX_STRING_BYTES = 65536
    MAX_SEEN = 32768
    MAX_ITEMS = 4096
    MAX_ACTIVE_TOOLS = 1024
    TOOL_TYPES = {
        "command_execution": "command",
        "mcp_tool_call": "MCP tool",
        "web_search": "web search",
        "file_change": "file changes",
        "collab_agent_tool_call": "agent tool",
        "tool_call": "tool",
        "function_call": "tool",
    }

    def __init__(self, events_path, *, idle_seconds=300, tool_seconds=1800,
                 clock=time.monotonic):
        self.path = Path(events_path)
        self.idle_limit = max(0, float(idle_seconds))
        self.tool_limit = max(0, float(tool_seconds))
        self.clock = clock
        self._lock = threading.RLock()
        self._last_activity = clock()
        self._provider_active = False
        self._offset = 0
        self._file_identity = None
        self._line = None
        self._seen = set()
        self._text_items = {}
        self._active = {}
        self._closed = set()
        self._explicit_starts = False
        self._fallback_started = None

    @staticmethod
    def _digest(value):
        return hashlib.sha256(value.encode("utf-8", errors="replace")).digest()

    def _new(self, value):
        """Never evict old IDs: log spam cannot regain credit by cache churn."""
        digest = self._digest(value)
        if digest in self._seen or len(self._seen) >= self.MAX_SEEN:
            return False
        self._seen.add(digest)
        return True

    def _activity(self, now, *, provider=False):
        self._last_activity = now
        self._provider_active = provider

    @staticmethod
    def _identifier(value):
        return value if isinstance(value, str) and value else None

    def _tool(self, key, label, phase, now):
        if phase == "start":
            # Provider wrappers may themselves be long-lived descendants. Once
            # real start events are available they supersede that approximation.
            self._explicit_starts = True
            self._fallback_started = None
            if key in self._active or key in self._closed:
                return
            if len(self._active) >= self.MAX_ACTIVE_TOOLS or not self._new("start:" + key):
                return
            self._active[key] = (now, label)
            self._activity(now)
        elif phase == "complete":
            if key in self._closed:
                return
            was_active = self._active.pop(key, None) is not None
            if len(self._closed) < self.MAX_SEEN:
                self._closed.add(key)
            if self._new("complete:" + key) or was_active:
                self._activity(now)
                if not self._explicit_starts and self._fallback_started is not None:
                    # Completion-only providers may keep helper processes alive
                    # across many tools. A real new completion proves an end to
                    # unreported activity; PID churn or duplicate output does not.
                    # With no starts, concurrent tools cannot be timed separately.
                    self._fallback_started = now

    def _text(self, key, value, now):
        if not isinstance(value, str) or not value.strip():
            return
        # Text arrives either as a whole item or as increasingly long snapshots.
        # Credit a new suffix once; repeating the same words under new IDs or
        # appending the same log line cannot continuously extend the deadline.
        previous = self._text_items.get(key)
        is_append = previous and len(value) >= previous[0] and self._digest(value[:previous[0]]) == previous[1]
        suffix = value[previous[0]:] if is_append else value
        if key in self._text_items or len(self._text_items) < self.MAX_ITEMS:
            # A length plus digest recognizes cumulative text without retaining
            # whole messages, commands, or unbounded per-item content.
            self._text_items[key] = (len(value), self._digest(value))
        full_new = self._new("text:" + value.strip())
        suffix_new = self._new("text:" + suffix.strip()) if suffix.strip() != value.strip() else full_new
        if full_new and suffix_new and suffix.strip():
            self._activity(now, provider=True)

    def _event(self, row, now):
        if not isinstance(row, dict):
            return
        event_type = row.get("type")
        item = row.get("item")
        if isinstance(item, dict) and event_type in ("item.started", "item.updated", "item.completed"):
            kind = item.get("type")
            identifier = self._identifier(item.get("id"))
            if not identifier:
                return
            key = "codex:" + self._digest(identifier).hex()
            if isinstance(kind, str) and kind in self.TOOL_TYPES:
                if event_type == "item.started":
                    self._tool(key, self.TOOL_TYPES[kind], "start", now)
                elif event_type == "item.completed":
                    self._tool(key, self.TOOL_TYPES[kind], "complete", now)
                # A running update is also a usable start when initial events
                # were omitted; later output updates leave the first start fixed.
                elif item.get("status") in ("in_progress", "running"):
                    self._tool(key, self.TOOL_TYPES[kind], "start", now)
                elif item.get("status") in ("completed", "failed"):
                    self._tool(key, self.TOOL_TYPES[kind], "complete", now)
            elif kind in ("agent_message", "reasoning"):
                self._text(key, item.get("text"), now)
            return
        part = row.get("part")
        if isinstance(part, dict):
            identifier = self._identifier(part.get("id")) or self._identifier(part.get("callID"))
            if identifier:
                identifier = self._digest(identifier).hex()
            if event_type == "tool_use" and identifier and isinstance(part.get("tool"), str):
                state = part.get("state")
                if not isinstance(state, dict):
                    return
                phase = state.get("status")
                if phase in ("pending", "running"):
                    self._tool("opencode:" + identifier, "tool", "start", now)
                elif phase in ("completed", "error"):
                    self._tool("opencode:" + identifier, "tool", "complete", now)
                return
            if event_type == "text" and identifier:
                self._text("opencode:" + identifier, part.get("text"), now)
                return
            if event_type in ("step_start", "step_finish") and identifier:
                if self._new("opencode:" + event_type + ":" + identifier):
                    self._activity(now, provider=event_type == "step_start")
                return
        if event_type in ("thread.started", "turn.started", "turn.completed", "turn.failed"):
            # Codex does not always attach an ID. A repeated bare event counts
            # once for this stage, and usage/log payload changes do not renew it.
            identifier = row.get("turn_id") or row.get("thread_id") or "stage"
            if isinstance(identifier, str) and self._new("codex:" + event_type + ":" + identifier):
                self._activity(now, provider=event_type.endswith("started"))

    def _consume(self, data, now):
        fragments = data.split(b'\n')
        for index, fragment in enumerate(fragments):
            if self._line is None:
                self._line = _ProjectedJsonLine(self.MAX_LINE_BYTES,
                                                min(self.MAX_STRING_BYTES, max(8, self.MAX_LINE_BYTES // 4)))
            self._line.feed(fragment)
            if index < len(fragments) - 1:
                self._event(self._line.finish(), now)
                self._line = None

    def _read(self, now):
        try:
            with self.path.open("rb") as stream:
                # fstat avoids a replace-between-stat-and-open race.
                metadata = os.fstat(stream.fileno())
                identity = (metadata.st_dev, metadata.st_ino)
                if identity != self._file_identity or metadata.st_size < self._offset:
                    self._offset = 0
                    self._line = None
                    self._file_identity = identity
                stream.seek(self._offset)
                data = stream.read(self.MAX_READ_BYTES)
                self._offset += len(data)
        except OSError:
            return
        self._consume(data, now)

    @staticmethod
    def _mcp_helper(row):
        # An idle MCP server can live for the whole provider session. Its
        # existence does not prove a call is running; real starts still do.
        # Unknown executables (including node-hosted servers) stay conservative.
        executable = row.get("executable")
        name = Path(executable).name if isinstance(executable, str) else ""
        return name == "mcp-server" or name.startswith("mcp-server-")

    def poll(self, processes=None, root_pid=None):
        with self._lock:
            now = self.clock()
            self._read(now)
            if processes is not None and root_pid is not None and not self._explicit_starts:
                rows = processes.values() if isinstance(processes, dict) else processes
                descendants = any(isinstance(row, dict) and row.get("pid") != root_pid
                                  and row.get("pid") is not None
                                  and not str(row.get("state", "")).startswith("Z")
                                  and not self._mcp_helper(row) for row in rows)
                if descendants and self._fallback_started is None:
                    self._fallback_started = now
                elif not descendants and self._fallback_started is not None:
                    self._fallback_started = None
                    self._activity(now)
            return self._snapshot(now)

    def _tool_elapsed(self, now):
        starts = [value[0] for value in self._active.values()]
        if self._fallback_started is not None:
            starts.append(self._fallback_started)
        return max(0, now - min(starts)) if starts else None

    def _expired(self, now):
        elapsed = self._tool_elapsed(now)
        if elapsed is not None:
            if self.tool_limit and elapsed >= self.tool_limit:
                return {"kind": "tool", "reason": "Tool execution exceeded its fixed time limit"}
        elif self.idle_limit and now - self._last_activity >= self.idle_limit:
            return {"kind": "idle", "reason": "No new provider activity within the inactivity limit"}
        return None

    def expired(self):
        with self._lock:
            return self._expired(self.clock())

    def _snapshot(self, now):
        elapsed = self._tool_elapsed(now)
        expired = self._expired(now)
        if expired:
            activity, detail = "stalled", expired["reason"]
        elif self._active:
            activity, detail = "running_tool", min(self._active.values())[1]
        elif self._fallback_started is not None:
            activity, detail = "running_tool", "provider subprocess (tool activity inferred)"
        elif self._provider_active:
            activity, detail = "provider_active", "new provider event observed"
        else:
            activity, detail = "waiting_for_provider", "waiting for new provider activity"
        return {"activity": activity, "detail": detail,
                "idle_seconds": round(max(0, now - self._last_activity), 3),
                "tool_elapsed_seconds": round(elapsed, 3) if elapsed is not None else None,
                "idle_limit_seconds": self.idle_limit, "tool_limit_seconds": self.tool_limit,
                "active_tool_count": len(self._active), "completed_tool_count": len(self._closed),
                "process_fallback": self._fallback_started is not None}

    def snapshot(self):
        with self._lock:
            return self._snapshot(self.clock())
