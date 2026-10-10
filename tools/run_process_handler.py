"""In-process executor for `autocode_util.run_process` (#704 slice 3).

Point AUTOCODE_RUN_PROCESS at this module (tests: `AUTOCODE_RUN_PROCESS=run_process_handler`)
and every `util.run_process` call executes its command in this interpreter via
runpy instead of spawning a grand-child: one interpreter startup serves a whole
battery of provider calls. The command's own directory goes on `sys.path` first,
so a copied fixture script can import siblings (autocode_util, for example).
Stdin bytes and the child env are honoured; captured stdout/stderr come back on
the result, and SystemExit from a scripted main becomes the return code.
"""

from __future__ import annotations

import io
import os
import runpy
import sys
from types import SimpleNamespace


def run_process(cmd, *, input=None, env=None):  # noqa: A002 - mirrors subprocess.run
    script = str(cmd[0])
    saved = (sys.argv, sys.stdin, sys.stdout, sys.stderr, os.getcwd(), dict(os.environ))
    out, err = io.StringIO(), io.StringIO()
    code = 0
    try:
        sys.argv = [script, *cmd[1:]]
        sys.stdin = io.TextIOWrapper(io.BytesIO(input or b""))
        sys.stdout, sys.stderr = out, err
        if env is not None:
            os.environ.clear()
            os.environ.update(env)
        script_dir = os.path.dirname(os.path.abspath(script))
        if script_dir and script_dir not in sys.path:
            sys.path.insert(0, script_dir)
        try:
            runpy.run_path(script, run_name="__main__")
        except SystemExit as exit_code:
            payload = exit_code.code
            code = payload if isinstance(payload, int) else (0 if payload is None else 1)
    finally:
        sys.argv, sys.stdin, sys.stdout, sys.stderr = saved[0], saved[1], saved[2], saved[3]
        os.chdir(saved[4])
        os.environ.clear()
        os.environ.update(saved[5])
    return SimpleNamespace(returncode=code, stdout=out.getvalue(), stderr=err.getvalue())
