"""The in-process CLI runner for the #704 process seam (PR #920).

``taskrun.run_process`` is the one launch point for the CLI driver; this module
is the reusable replacement a test installs so the CLI runs in THIS process —
same parser, same handlers, same state writes, same receipts — instead of one
interpreter + full import per call. The supervision the CLI itself triggers
(keeper admission, ownership receipts) still runs for real, inside main().

Usage:

    import inprocess_cli, autocode_taskrun as taskrun
    with inprocess_cli.patched():
        run = taskrun.TaskRun.start(workspace, brief)   # no interpreter spawns

Boundaries (deliberate):
- Only autocode CLI argv belongs here; it calls ``autocode.main()``, not a shell.
- ``timeout`` is accepted and ignored: there is no child to kill. A hanging CLI
  hangs the test, exactly as an equivalent in-process unit test would.
- ``env`` is applied by swapping ``os.environ`` for the duration of the call, and
  ``cwd`` by ``os.chdir``, because the CLI reads both from the process.
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
from collections.abc import Iterator
from unittest import mock

import autocode as autocode_cli


@contextlib.contextmanager
def _environment(env: dict | None) -> Iterator[None]:
    # patch.dict(clear=True) is the canonical whole-environment swap: assigning to
    # os.environ trips ruff B003 and mypy both. The caller's dict is complete —
    # _invoke merges os.environ with its own keys before passing it.
    if env is None:
        yield
        return
    with mock.patch.dict(os.environ, env, clear=True):
        yield


@contextlib.contextmanager
def _directory(cwd) -> Iterator[None]:
    if cwd is None:
        yield
        return
    saved = os.getcwd()
    try:
        os.chdir(cwd)
        yield
    finally:
        os.chdir(saved)


def run(argv, *, env=None, cwd=None, timeout=None, advancing=False) -> subprocess.CompletedProcess:
    """Run the AutoCode CLI in this process; a drop-in for ``taskrun.run_process``.

    Returns the same text ``CompletedProcess`` shape the real seam returns, with
    stdout/stderr captured from ``main()``'s writes. ``main()`` restores
    ``sys.argv`` itself; an ``SystemExit`` escaping it is converted to its code,
    matching what a process exit would surface as the returncode.
    """
    del timeout, advancing  # no child to time out or supervise; see module docstring
    out, err = io.StringIO(), io.StringIO()
    with _environment(env), _directory(cwd), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        # argv is the spawned form [interpreter, script, *args]; the CLI's argv0 is the
        # script. A real child sees sys.argv == [script, *args]; reproduce that exactly,
        # or the script path arrives as the first CLI argument.
        program, *arguments = list(argv[1:]) if len(argv) > 1 else list(argv)
        sys.argv = [str(program), *[str(a) for a in arguments]]
        try:
            code = autocode_cli.main()
        except SystemExit as exit_request:  # cli() raises it; main() should not
            code = exit_request.code if isinstance(exit_request.code, int) else 1
    return subprocess.CompletedProcess(list(argv), code, out.getvalue(), err.getvalue())


@contextlib.contextmanager
def patched():
    """Install the in-process runner on the #704 seam for the duration."""
    import autocode_taskrun as taskrun

    with mock.patch.object(taskrun, "run_process", run):
        yield
