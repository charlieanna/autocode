"""Capture a CLI invocation only after its owned process tree has stopped."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import ExitStack
from pathlib import Path

try:
    from . import autocode_grader_process as supervisor
    from .autocode_process import ProcessError, interruption_handler, preflight
except ImportError:
    import autocode_grader_process as supervisor
    from autocode_process import ProcessError, interruption_handler, preflight


HELPER_RESERVE = 8.0


def _above_stdio(descriptor):
    """Keep capture/protocol descriptors away from possibly closed stdio slots."""
    if descriptor > 2:
        return descriptor
    import fcntl

    return fcntl.fcntl(descriptor, fcntl.F_DUPFD_CLOEXEC, 3)


def _private_fd(descriptor):
    duplicate = _above_stdio(descriptor)
    if duplicate != descriptor:
        os.close(descriptor)
    return duplicate


def _private_pipe():
    reader, writer = os.pipe()
    try:
        reader = _private_fd(reader)
        writer = _private_fd(writer)
    except BaseException:
        try:
            os.close(reader)
        finally:
            os.close(writer)
        raise
    return reader, writer


def _temporary(mode):
    stream = tempfile.TemporaryFile(mode=mode)  # noqa: SIM115 - returned to the caller's ExitStack.
    try:
        descriptor = _above_stdio(stream.fileno())
    except BaseException:
        stream.close()
        raise
    if descriptor == stream.fileno():
        return stream
    stream.close()
    return os.fdopen(descriptor, mode)


def _await_cleanup(helper, deadline):
    """Defer further user interrupts while one fixed cleanup allowance runs."""
    while True:
        try:
            helper.wait(timeout=max(0, deadline - time.monotonic() - 1))
            return
        except KeyboardInterrupt:
            if time.monotonic() < deadline - 1:
                continue
        except subprocess.TimeoutExpired:
            pass
        except OSError:
            raise ProcessError("Capture helper cleanup could not be observed") from None
        break
    # Killing an unresponsive helper does not establish family cleanup. Never
    # turn this emergency collection into a retryable command timeout.
    while time.monotonic() < deadline:
        try:
            helper.kill()
            helper.wait(timeout=max(0, deadline - time.monotonic()))
            break
        except KeyboardInterrupt:
            continue
        except (OSError, subprocess.TimeoutExpired):
            break
    raise ProcessError("Capture helper could not establish command cleanup") from None


def _sampled(command, timeout, env, cwd, output, errors, stdin):
    """Portable sampled cleanup; macOS has no Linux child-adoption mechanism."""
    child = subprocess.Popen(
        command, cwd=cwd, env=env, stdin=stdin, stdout=output, stderr=errors, start_new_session=True
    )
    try:
        code, expired, _ = supervisor.wait(child, float("inf") if timeout is None else timeout)
    except supervisor.psutil.Error as error:
        raise ProcessError(f"Cannot supervise CLI process: {error}") from error
    return code, expired


def _linux(command, timeout, env, cwd, output, errors, stdin):
    """One private subreaper owns exactly one invocation, even off the main thread."""
    deadline = None if timeout is None else time.monotonic() + timeout
    parent_deadline = None if deadline is None else deadline + HELPER_RESERVE
    nonce = uuid.uuid4().hex
    words = (
        os.fsdecode(command) if isinstance(command, (str, bytes, os.PathLike)) else [os.fsdecode(w) for w in command]
    )
    with ExitStack() as stack:
        request = stack.enter_context(_temporary("w+b"))
        result = stack.enter_context(_temporary("w+b"))
        reader, writer = _private_pipe()
        owner_read = stack.enter_context(os.fdopen(reader, "rb", buffering=0))
        owner_write = stack.enter_context(os.fdopen(writer, "wb", buffering=0))
        request.write(json.dumps({"command": words, "deadline": deadline, "nonce": nonce}).encode("utf-8"))
        request.seek(0)
        helper = subprocess.Popen(
            [
                sys.executable,
                "-I",
                str(Path(__file__).with_name("autocode_capture_reaper.py")),
                str(request.fileno()),
                str(result.fileno()),
                str(owner_read.fileno()),
            ],
            cwd=cwd,
            env=env,
            stdin=stdin,
            stdout=output,
            stderr=errors,
            start_new_session=True,
            close_fds=True,
            pass_fds=(request.fileno(), result.fileno(), owner_read.fileno()),
        )
        owner_read.close()
        interrupted = None
        try:
            # Reserve the final second for emergency helper collection; no
            # exceptional wait receives a fresh extension of this bound.
            remaining = None if parent_deadline is None else max(0, parent_deadline - time.monotonic() - 1)
            helper.wait(timeout=remaining)
        except BaseException as error:
            interrupted = error
            owner_write.close()
            until = time.monotonic() + HELPER_RESERVE
            parent_deadline = until if parent_deadline is None else min(parent_deadline, until)
            _await_cleanup(helper, parent_deadline)
        result.seek(0)
        try:
            response = json.loads(result.read(65537))
        except (ValueError, UnicodeError):
            response = None
        if (
            not isinstance(response, dict)
            or type(response.get("schema")) is not int
            or response["schema"] != 1
            or response.get("nonce") != nonce
        ):
            raise ProcessError("Capture helper returned no valid cleanup result") from None
        if response.get("clean") is not True or helper.returncode != 0:
            raise ProcessError("Capture helper could not verify command cleanup") from None
        if interrupted is not None:
            if isinstance(interrupted, subprocess.TimeoutExpired):
                raise ProcessError("Capture helper exceeded its cleanup allowance") from None
            raise interrupted
        started, code, expired = response.get("started"), response.get("returncode"), response.get("expired")
        start_errno = response.get("start_errno")
        if (
            type(started) is not bool
            or type(expired) is not bool
            or (started and type(code) is not int)
            or (not started and code is not None)
            or ("start_errno" in response and (type(start_errno) is not int or start_errno <= 0 or started or expired))
            or "error" in response
        ):
            raise ProcessError("Capture helper returned an invalid command result")
        if start_errno is not None:
            raise OSError(start_errno, os.strerror(start_errno), words[0] if isinstance(words, list) else words)
        if not started and not expired:
            raise ProcessError("Captured command was cancelled before launch")
        return code, expired


def run(command, *, timeout=None, env=None, cwd=None, capture_output=True, text=True, check=False, stdin=None):
    """Return a text CompletedProcess, or raise after verified timeout cleanup.

    Linux uses an invocation-local subreaper and collects its complete child
    family before publication. macOS retains sampled descendant cleanup, which
    cannot prove the absence of a helper that detached between observations.
    Private file streams cannot fill a pipe or be held open by a descendant.
    """
    if capture_output is not True or text is not True:
        raise ValueError("Captured commands require capture_output=True and text=True")
    preflight()
    with ExitStack() as stack:
        if threading.current_thread() is threading.main_thread():
            stack.enter_context(interruption_handler())
        output = stack.enter_context(_temporary("w+t"))
        errors = stack.enter_context(_temporary("w+t"))
        execute = _linux if sys.platform == "linux" else _sampled
        code, expired = execute(command, timeout, env, cwd, output, errors, stdin)
        output.seek(0)
        errors.seek(0)
        stdout, stderr = output.read(), errors.read()
        if expired:
            raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr)
        if check and code:
            raise subprocess.CalledProcessError(code, command, output=stdout, stderr=stderr)
        return subprocess.CompletedProcess(command, code, stdout, stderr)
