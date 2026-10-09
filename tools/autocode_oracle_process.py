"""Capture oracle I/O without reaping its leader before verified cleanup."""
import subprocess
import tempfile
from contextlib import ExitStack

try:
    from . import autocode_grader_process as supervisor
except ImportError:
    import autocode_grader_process as supervisor


def run(command, cwd, *, timeout=60, stdin=None):
    # File-backed streams cannot deadlock on full pipes or remain open because
    # a descendant inherited an output pipe. The supervisor alone reaps the
    # leader, after capturing and stopping its birth-identified descendants.
    with ExitStack() as stack:
        output = stack.enter_context(tempfile.TemporaryFile(mode='w+t', errors='replace'))
        errors = stack.enter_context(tempfile.TemporaryFile(mode='w+t', errors='replace'))
        input_file = subprocess.DEVNULL
        if stdin is not None:
            input_file = stack.enter_context(tempfile.TemporaryFile(mode='w+t'))
            input_file.write(stdin)
            input_file.seek(0)
        child = subprocess.Popen(command, cwd=cwd, stdin=input_file, stdout=output,
                                 stderr=errors, start_new_session=True)
        code, expired, _ = supervisor.wait(child, timeout)
        if expired:
            return -1, '', 'TIMEOUT'
        output.seek(0)
        errors.seek(0)
        return code, output.read(), errors.read()
