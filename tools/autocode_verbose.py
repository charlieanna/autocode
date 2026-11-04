"""Console stream of what a provider model is doing during a stage.

On by default; --no-verbose or AUTOCODE_VERBOSE=0 (also false, off, no) turns it
off, and --verbose turns it back on over the variable. When on, the stage's
ActivityMonitor reports tool starts/finishes and new provider text as one
bounded line each to stderr, so an operator can see live activity without
opening the stage's JSONL event log. Printing is best-effort: a broken stderr
never fails the stage or the monitor.

Heartbeat lines that repeat verbatim (for example the per-chunk
"native provider stream advanced" progress) are coalesced like text: the first
prints at once, further identical repeats within the interval are counted
silently, and the next line to print carries the count as "(+N similar)".
"""

from __future__ import annotations

import os
import sys
import time

ENV = "AUTOCODE_VERBOSE"
MAX_TEXT = 200
MIN_TEXT_INTERVAL_SECONDS = 1.0
OFF = ("0", "false", "off", "no")


def enable() -> None:
    os.environ[ENV] = "1"


def disable() -> None:
    os.environ[ENV] = "0"


def enabled() -> bool:
    """On unless AUTOCODE_VERBOSE names an off value; unset or empty means on."""
    return os.environ.get(ENV, "").strip().lower() not in OFF


def reporter(stage: str, model: str | None, *, stream=None, clock=None):
    """Return an ActivityMonitor reporter, or None when verbose is turned off."""
    if not enabled():
        return None
    return _Reporter(stage, model, stream=stream, clock=clock)


class _Reporter:
    def __init__(self, stage, model, *, stream=None, clock=None):
        self.prefix = f"{stage}/{model}" if model else str(stage)
        self.stream = stream if stream is not None else sys.stderr
        self.clock = clock or time.monotonic
        self._last_text_at = None
        self._last_line = None
        self._last_line_at = None
        self._suppressed = 0

    def __call__(self, kind, detail):
        try:
            if kind == "text":
                now = self.clock()
                if self._last_text_at is not None and now - self._last_text_at < MIN_TEXT_INTERVAL_SECONDS:
                    return
                self._last_text_at = now
                text = " ".join(str(detail or "").split())
                if not text:
                    return
                if len(text) > MAX_TEXT:
                    text = text[:MAX_TEXT] + "…"
                self._line(text)
                return
            base = f"{kind}: {detail}"
            now = self.clock()
            if (
                base == self._last_line
                and self._last_line_at is not None
                and now - self._last_line_at < MIN_TEXT_INTERVAL_SECONDS
            ):
                self._suppressed += 1
                return
            line = f"{base} (+{self._suppressed} similar)" if self._suppressed else base
            self._suppressed = 0
            self._last_line = base
            self._last_line_at = now
            self._line(line)
        except Exception:
            pass

    def _line(self, text):
        print(f"{self.prefix}: {text}", file=self.stream, flush=True)
