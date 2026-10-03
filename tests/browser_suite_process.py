"""Bounded browser catalogue commands with receipts and owned-tree cleanup."""
import subprocess
import tempfile

import autocode_process as processes


def run(command, *, cwd, env, timeout):
    """Run one command; timeout/interruption remain failures with attached evidence.

    File-backed output avoids pipe deadlocks when a detached fixture inherits
    stdout. The existing supervisor records birth identities and stops only
    this invocation's process tree, including previously observed detached tools.
    """
    owned = []
    child = None
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        def receipt(reason, error=None):
            stdout.seek(0)
            stderr.seek(0)
            saved = getattr(error, "processes", None) or owned
            cleanup = {"checked": True, "owned": saved, "live_pids": [],
                       "root_reaped": child is None or child.poll() is not None}
            try:
                cleanup["live_pids"] = [p["pid"] for p in processes.live_processes(saved)]
            except processes.ProcessError as inspection:
                cleanup.update(checked=False, error=str(inspection))
            return {
                "command": list(command), "cwd": str(cwd), "timeout_seconds": timeout,
                "reason": reason, "returncode": child.returncode if child else None,
                "stdout": stdout.read().decode("utf-8", errors="replace"),
                "stderr": stderr.read().decode("utf-8", errors="replace"),
                "cleanup": cleanup,
            }

        try:
            with processes.interruption_handler():
                child = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                         stdout=stdout, stderr=stderr, start_new_session=True)
                try:
                    code, expired = processes.wait_for_stage(
                        child, timeout, lambda rows: owned.__setitem__(slice(None), rows))
                finally:
                    # Also cover an interrupt just before supervision starts.
                    if child.poll() is None:
                        tree = processes.ProcessTree(child.pid, lambda rows: None)
                        tree.known = {row["pid"]: row for row in owned}
                        tree.stop(child)
                if expired:
                    raise subprocess.TimeoutExpired(command, timeout)
        except BaseException as error:
            reason = ("timeout" if isinstance(error, subprocess.TimeoutExpired) else
                      "interrupted" if isinstance(error, KeyboardInterrupt) else
                      "setup_error" if child is None else "supervision_error")
            error.receipt = receipt(reason, error)
            error.receipt["error"] = str(error)
            if isinstance(error, subprocess.TimeoutExpired):
                error.output = error.receipt["stdout"]
                error.stderr = error.receipt["stderr"]
            raise
        evidence = receipt("completed" if code == 0 else "exit_failure")
        completed = subprocess.CompletedProcess(command, code, evidence["stdout"], evidence["stderr"])
        completed.receipt = evidence
        return completed
