"""Capture a CLI invocation only after its owned process tree has stopped."""
from contextlib import ExitStack
import subprocess
import tempfile
import threading
import psutil

try:
    from . import autocode_grader_process as supervisor
    from .autocode_process import ProcessError, interruption_handler, preflight
except ImportError:
    import autocode_grader_process as supervisor
    from autocode_process import ProcessError, interruption_handler, preflight


def run(command, *, timeout=None, env=None, cwd=None):
    """Return a text CompletedProcess, or raise after verified timeout cleanup.

    File streams cannot fill a pipe or stay open because an owned descendant
    inherited stdout. The existing supervisor records descendants across private
    sessions, checks their birth identities, and cleans up before reaping the CLI.
    """
    preflight()
    with ExitStack() as stack:
        if threading.current_thread() is threading.main_thread():
            stack.enter_context(interruption_handler())
        output = stack.enter_context(tempfile.TemporaryFile(mode="w+t"))
        errors = stack.enter_context(tempfile.TemporaryFile(mode="w+t"))
        child = subprocess.Popen(command, cwd=cwd, env=env, stdout=output,
                                 stderr=errors, start_new_session=True)
        try:
            code, expired, _ = supervisor.wait(child, float("inf") if timeout is None else timeout)
        except psutil.Error as error:
            raise ProcessError(f"Cannot supervise CLI process: {error}") from error
        output.seek(0)
        errors.seek(0)
        stdout, stderr = output.read(), errors.read()
        if expired:
            raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr)
        return subprocess.CompletedProcess(command, code, stdout, stderr)
