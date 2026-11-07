"""A program in a real terminal, for tests that assert what a person sees (#1646).

The full-screen chat draws with curses. A stand-in screen checks what it asks
curses to draw; a terminal checks what arrives: scrolling regions, the alternate
screen, bracketed paste, key timing and resizes. This helper runs one command in
a private tmux server (its own socket, no user configuration, a fixed size) and
reads the screen back with ``capture-pane``. Waits poll the screen for a
condition; nothing sleeps a fixed time.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

POLL_SECONDS = 0.1
# Runs the program as the terminal's foreground job, records its exit status and keeps
# the terminal open. A pane whose program has exited is no good to read: tmux (3.6a)
# drops the first line the program printed, which is where a closing message lands.
HOLD = """\
import signal, subprocess, sys
signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ctrl-C is for the program, not for this holder
code = subprocess.call(sys.argv[2:], preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))
with open(sys.argv[1], "w", encoding="utf-8") as status:
    status.write(str(code))
signal.pause()
"""
CONFIG = "set -g status off\nset -g history-limit 2000\nset -g escape-time 0\n"


def require_tmux(test: unittest.TestCase) -> str:
    """tmux's path; skips without it locally, fails under CI so the screen tests cannot silently stop."""
    if os.name != "posix":
        raise unittest.SkipTest("the full-screen chat needs curses and a POSIX terminal")
    tmux = shutil.which("tmux")
    if tmux:
        return tmux
    if os.environ.get("CI"):
        test.fail("tmux is required for the terminal screen tests in CI")
    raise unittest.SkipTest("tmux is not installed")


class TerminalScreen:
    """One command in a ``cols`` by ``rows`` terminal; close() always tears the server down."""

    def __init__(self, argv, *, cwd, env, cols=100, rows=30, tmux="tmux"):
        self._dir = tempfile.TemporaryDirectory(prefix="screen-")
        root = Path(self._dir.name)
        (root / "tmux.conf").write_text(CONFIG, encoding="utf-8")
        (root / "hold.py").write_text(HOLD, encoding="utf-8")
        self._status = root / "status"
        self._tmux = [tmux, "-u", "-S", str(root / "socket"), "-f", str(root / "tmux.conf")]
        self._root = root
        # The server is started by this call, so the pane inherits exactly ``env``.
        self._run(
            "new-session",
            "-d",
            "-s",
            "t",
            "-x",
            str(cols),
            "-y",
            str(rows),
            "-c",
            str(cwd),
            "--",
            sys.executable,
            str(root / "hold.py"),
            str(self._status),
            *argv,
            env=env,
        )

    # ---- reading ---------------------------------------------------------------------------

    def text(self) -> str:
        """The visible screen, one line per row, trailing spaces removed."""
        shown = self._run("capture-pane", "-p", "-t", "t").stdout
        return "\n".join(line.rstrip() for line in shown.splitlines())

    def wait_for(self, condition, timeout=60.0, what=None) -> str:
        """The screen once ``condition`` (text, or a function of the screen) holds; fails with it otherwise."""
        check = condition if callable(condition) else (lambda screen: condition in screen)
        deadline = time.monotonic() + timeout
        while True:
            screen = self.text()
            if check(screen):
                return screen
            if time.monotonic() > deadline:
                raise AssertionError(f"timed out waiting for {what or condition!r}; the screen:\n{screen}")
            time.sleep(POLL_SECONDS)

    def exit_status(self, timeout=60.0) -> int:
        """The program's exit status once it has exited."""
        deadline = time.monotonic() + timeout
        while True:
            if self._status.exists() and (status := self._status.read_text(encoding="utf-8")):
                return int(status)
            if time.monotonic() > deadline:
                raise AssertionError(f"the program did not exit; the screen:\n{self.text()}")
            time.sleep(POLL_SECONDS)

    # ---- the keyboard ----------------------------------------------------------------------

    def type(self, text: str) -> None:
        """Type ``text`` literally, as keystrokes."""
        self._run("send-keys", "-t", "t", "-l", text)

    def keys(self, *names: str) -> None:
        """Press named keys: Enter, Escape, C-c, C-d, C-u, PPage, NPage, BSpace…"""
        self._run("send-keys", "-t", "t", *names)

    def paste(self, text: str) -> None:
        """Paste ``text`` as a terminal does: bracketed when the program asked for it."""
        source = self._root / "paste.txt"
        source.write_text(text, encoding="utf-8")
        self._run("load-buffer", "-b", "p", str(source))
        self._run("paste-buffer", "-p", "-d", "-b", "p", "-t", "t")

    def resize(self, cols: int, rows: int) -> None:
        self._run("resize-window", "-t", "t", "-x", str(cols), "-y", str(rows))

    # ---- lifecycle -------------------------------------------------------------------------

    def close(self) -> None:
        subprocess.run([*self._tmux, "kill-server"], capture_output=True, check=False)
        self._dir.cleanup()

    def __enter__(self) -> TerminalScreen:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _run(self, *args, env=None) -> subprocess.CompletedProcess:
        return subprocess.run([*self._tmux, *args], capture_output=True, text=True, check=True, env=env)
