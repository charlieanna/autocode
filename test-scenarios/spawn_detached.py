#!/usr/bin/env python3
"""Spawn a command as its own session (setsid equivalent on macOS).

Usage: spawn_detached.py <pidfile> <logfile> <stdin-file> -- <command...>

The child becomes a session leader so a scenario can SIGKILL the whole
process group (runner + provider subprocesses) with `kill -9 -- -<pid>`.
Prints the child's PID to <pidfile> immediately; exits with its status.
"""
import subprocess
import sys

pidfile, logfile, stdinfile, *rest = sys.argv[1:]
if not rest or rest[0] != "--":
    raise SystemExit("expected '--' before the command")
command = rest[1:]
with open(stdinfile, "rb") as fin, open(logfile, "wb") as fout:
    child = subprocess.Popen(command, stdin=fin, stdout=fout,
                             stderr=subprocess.STDOUT, start_new_session=True)
with open(pidfile, "w") as out:
    out.write(str(child.pid))
sys.exit(child.wait())
