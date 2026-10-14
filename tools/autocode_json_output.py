"""--json: stdout carries exactly one JSON object; everything else an invocation prints goes to stderr (#714).

Once the main parser has read ``--json``, begin() sends whatever the invocation prints to stderr,
keeping a copy of it, and remembers the real stdout. The one object written there is:

- for a run that completes or pauses, the ``--status`` payload (status(), from print_completion and
  print_pause, #885);
- for a user action that ends the invocation (an approval, answer, feedback, resolver response...),
  that payload plus ``result`` (action());
- for any other ending, such as a refusal before the run is loaded, ``{"result": ...}`` alone
  (finish()).

``result`` holds the exit code, whether the action was saved, the text a person would have read on
stdout, and ``error``: ``{"code": "input_rejected", "message": ...}`` when the input was refused,
else null. A refusal also ends stderr with ``{"error": ...}`` on its own line. Exit codes are
unchanged: 0 saved or complete, 2 waiting or refused (#885). The read commands (--status, --explain,
--dry-run) print their own output and are left alone.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys

try:
    from . import autocode_status_command as status_command
except ImportError:
    import autocode_status_command as status_command

REFUSED = "Input rejected:"


class _Copy(io.TextIOBase):
    """A text stream that writes through to ``target`` and keeps a copy."""

    def __init__(self, target, copy: io.StringIO):
        self.target, self.copy = target, copy

    def write(self, text):
        self.target.write(text)
        self.copy.write(text)
        return len(text)

    def flush(self):
        self.target.flush()

    def isatty(self):
        return self.target.isatty()

    def fileno(self):
        return self.target.fileno()

    @property
    def encoding(self):
        return getattr(self.target, "encoding", "utf-8")


_active: dict | None = None


def begin(args) -> None:
    """From here on, under --json, everything printed goes to stderr (with a copy kept for ``result``)."""
    global _active
    reads = (getattr(args, flag, False) for flag in ("status", "explain", "dry_run"))
    if not getattr(args, "json", False) or _active is not None or any(reads):
        return
    printed, errors = io.StringIO(), io.StringIO()
    _active = {"stdout": sys.stdout, "stderr": sys.stderr, "printed": printed, "errors": errors, "emitted": False}
    sys.stdout, sys.stderr = _Copy(sys.stderr, printed), _Copy(sys.stderr, errors)


def restore() -> None:
    """Put stdout and stderr back (main's exit; main can run more than once in a process)."""
    global _active
    if _active is not None:
        sys.stdout, sys.stderr = _active["stdout"], _active["stderr"]
        _active = None


def status(runner, state, args, workspace, run_dir) -> None:
    """Print the ``--status`` payload on the real stdout."""
    with contextlib.redirect_stdout(_active["stdout"] if _active else sys.stdout):
        status_command.render(runner, state, args, workspace, run_dir)
    if _active is not None:
        _active["emitted"] = True


def action(runner, args, state, workspace, run_dir, code) -> None:
    """A user action ended the invocation with ``code``: print the payload with its ``result``."""
    if _active is None:
        return
    payload = io.StringIO()
    # The status hints (the cost line) go to the real stderr, never into the refusal message _result reads.
    with contextlib.redirect_stdout(payload), contextlib.redirect_stderr(_active["stderr"]):
        status_command.render(runner, state, args, workspace, run_dir)
    _write({**json.loads(payload.getvalue()), "result": _result(code)})


def finish(code):
    """main's return value: an invocation under --json that printed no object prints its result alone."""
    if _active is not None and not _active["emitted"]:
        _write({"result": _result(code)})
    return code


def _write(value) -> None:
    assert _active is not None
    print(json.dumps(value, indent=2), file=_active["stdout"], flush=True)
    _active["emitted"] = True


def _result(code) -> dict:
    assert _active is not None
    errors = _active["errors"].getvalue()
    start = errors.find(REFUSED)
    error = None
    if start >= 0:
        error = {"code": "input_rejected", "message": errors[start + len(REFUSED) :].strip()}
        print(json.dumps({"error": error}), file=_active["stderr"], flush=True)
    return {"exit_code": code, "saved": code == 0, "message": _active["printed"].getvalue().strip(), "error": error}
