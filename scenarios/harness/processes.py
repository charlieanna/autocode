"""Bounded CLI calls that retain ownership when a provider starts a new session."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import time
import uuid

from . import attempts

try:
    import psutil
except ModuleNotFoundError as error:
    if error.name != "psutil":
        raise
    psutil = None


class SupervisionUnavailable(RuntimeError):
    """A CLI must not launch without the dependency needed to stop its workers."""


class CallTimeout(subprocess.TimeoutExpired):
    """A timed-out invocation, including any uncertainty about worker cleanup."""

    def __init__(self, command, timeout, errors, output=None, stderr=None):
        super().__init__(command, timeout, output=output, stderr=stderr)
        self.cleanup_errors = errors


def _live(process):
    # Process objects retain birth identity; is_running rejects reused PIDs.
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _capture(process, owned, errors):
    try:
        if process.is_running():
            for child in process.children(recursive=True):
                if child not in owned:
                    owned.append(child)
    except psutil.NoSuchProcess:
        pass
    except (psutil.Error, OSError) as error:
        errors.append(f"cannot capture descendants of PID {process.pid}: {error}")


def _signal(process, method, errors):
    try:
        if _live(process):
            # psutil checks the saved identity again before sending the signal.
            getattr(process, method)()
    except psutil.NoSuchProcess:
        pass
    except (psutil.Error, OSError) as error:
        errors.append(f"cannot {method} owned PID {process.pid}: {error}")


def _stop(child, parent, identity_error, output):
    errors = []
    owned = [parent] if parent else []
    if parent:
        # A detached session is still a descendant until the CLI dies.
        _capture(parent, owned, errors)
        _signal(parent, "terminate", errors)
    else:
        errors.append(identity_error)
        # Popen still owns its unreaped direct child, even without psutil metadata.
        try:
            child.terminate()
        except ProcessLookupError:
            pass
        except OSError as error:
            errors.append(f"cannot terminate CLI PID {child.pid}: {error}")
    try:
        stdout, stderr = child.communicate(timeout=5)
        output.update(stdout=stdout, stderr=stderr)
    except subprocess.TimeoutExpired as error:
        if error.stdout is not None:
            output["stdout"] = error.stdout
        if error.stderr is not None:
            output["stderr"] = error.stderr
    except BaseException as error:
        # Decoding failures and a second interrupt must not skip worker cleanup.
        errors.append(f"cannot collect CLI output during graceful cleanup: {type(error).__name__}: {error}")
    # Retain new descendants created during graceful termination as well.
    for process in list(owned):
        _capture(process, owned, errors)
    for process in reversed(owned):
        _signal(process, "kill", errors)
    if not parent:
        try:
            child.kill()
        except ProcessLookupError:
            pass
        except OSError as error:
            errors.append(f"cannot kill CLI PID {child.pid}: {error}")
    try:
        _, alive = psutil.wait_procs(owned, timeout=2)
        for process in alive:
            if _live(process):
                errors.append(f"owned PID {process.pid} remains alive after cleanup")
    except (psutil.Error, OSError) as error:
        errors.append(f"cannot verify owned worker cleanup: {error}")
    try:
        stdout, stderr = child.communicate(timeout=2)
        output.update(stdout=stdout, stderr=stderr)
    except subprocess.TimeoutExpired as error:
        if error.stdout is not None:
            output["stdout"] = error.stdout
        if error.stderr is not None:
            output["stderr"] = error.stderr
        errors.append("CLI output pipes remain open after cleanup; an uncaptured worker may remain")
    except BaseException as error:
        errors.append(f"cannot collect CLI output after cleanup: {type(error).__name__}: {error}")
    finally:
        for stream in (child.stdout, child.stderr):
            if stream:
                try:
                    stream.close()
                except OSError as error:
                    errors.append(f"cannot close CLI output pipe: {error}")
    return errors


def run_cli(command, *, env, cwd, timeout, lifeline=None):
    """Run one CLI; on timeout stop only processes captured from its ancestry."""
    if psutil is None:
        raise SupervisionUnavailable("CLI process supervision requires psutil in the harness interpreter, "
                                     "including with --autocode; run scenarios/run.py with the project's "
                                     "virtualenv Python (.venv/bin/python)")
    read_fd = write_fd = None
    call_path = call_record = None
    child = parent = None
    identity_error = "CLI process birth identity capture did not finish"
    try:
        if lifeline is not None:
            deadline = lifeline["deadline"]
            if (type(deadline) not in (int, float) or not math.isfinite(deadline)
                    or deadline <= time.monotonic()):
                raise CallTimeout(command, timeout, [])
            root = Path(lifeline["root"]).resolve()
            nonce = uuid.uuid4().hex
            wire = {"schema": 1, "nonce": nonce, "owner": attempts.identity(),
                    "deadline": deadline, "timeout_seconds": min(timeout, deadline - time.monotonic()),
                    "receipt": str(root / (nonce + "-supervision.json"))}
            call_path = root / (nonce + ".json")
            call_record = {**wire, "kind": "cli_call", "action": lifeline["kind"],
                           "phase": "pending", "started_at": time.time(), "usage_status": "unknown"}
            # Admission precedes launch, so SIGKILL of this harness cannot hide it.
            attempts.atomic_json(call_path, call_record)
            data = (json.dumps(wire, separators=(",", ":")) + "\n").encode()
            if len(data) > 4096:
                raise SupervisionUnavailable("CLI owner lifeline exceeded its bounded admission message")
            read_fd, write_fd = os.pipe()
            if os.write(write_fd, data) != len(data):
                raise SupervisionUnavailable("CLI owner lifeline admission was incomplete")
            actual_command = [*command, "--owner-lifeline-fd", str(read_fd)]
            # Its own session: every child that does not detach keeps the CLI's
            # group, which the CLI's keeper still owns after a CLI SIGKILL.
            child = subprocess.Popen(actual_command, env=env, cwd=cwd, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, close_fds=True, pass_fds=(read_fd,),
                                     start_new_session=True)
            os.close(read_fd)
            read_fd = None
        else:
            child = subprocess.Popen(command, env=env, cwd=cwd, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True)
        try:
            parent = psutil.Process(child.pid)
        except (psutil.Error, OSError) as error:
            identity_error = f"cannot capture CLI PID {child.pid} identity: {error}"
        if call_record is not None:
            call_record.update(cli=attempts.identity(child.pid), phase="running")
            attempts.atomic_json(call_path, call_record)
        remaining = min(timeout, lifeline["deadline"] - time.monotonic()) if lifeline else timeout
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command, timeout)
        stdout, stderr = child.communicate(timeout=remaining)
        if call_record is not None:
            call_record.update(phase="interrupted" if child.returncode < 0 else "returned",
                               exit=child.returncode, finished_at=time.time())
            attempts.atomic_json(call_path, call_record)
        return subprocess.CompletedProcess(command, child.returncode, stdout, stderr)
    except BaseException as error:
        # EOF reaches the independent CLI keeper even if cleanup is interrupted.
        if write_fd is not None:
            os.close(write_fd)
            write_fd = None
        output = {"stdout": getattr(error, "stdout", None), "stderr": getattr(error, "stderr", None)}
        errors = _stop(child, parent, identity_error, output) if child is not None else []
        if call_record is not None:
            call_record.update(phase="interrupted", exit=child.returncode if child is not None else None,
                               finished_at=time.time(), interruption=type(error).__name__, cleanup_errors=errors)
            try:
                attempts.atomic_json(call_path, call_record)
            except (OSError, ValueError) as save_error:
                errors.append(f"cannot retain interrupted CLI admission: {save_error}")
        if isinstance(error, subprocess.TimeoutExpired):
            raise CallTimeout(command, timeout, errors, output["stdout"], output["stderr"]) from None
        if errors:
            error.add_note("CLI cleanup incomplete: " + "; ".join(errors))
        raise
    finally:
        if read_fd is not None:
            os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)
