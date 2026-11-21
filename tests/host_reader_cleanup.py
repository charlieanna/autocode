"""Observe the fixture's recorded reader identities without signalling any process."""

import json
import os
import sys
from pathlib import Path

import psutil


def running_readers(log):
    running = []
    for line in Path(log).read_text().splitlines():
        fields = line.split()
        if not fields or fields[0] not in ("start", "child"):
            continue
        if len(fields) != 3:
            raise ValueError("Reader start lacks its process birth identity")
        pid, started = int(fields[1]), float(fields[2])
        try:
            process = psutil.Process(pid)
            if process.create_time() == started and process.status() != psutil.STATUS_ZOMBIE:
                running.append(pid)
        except psutil.NoSuchProcess:
            pass
    return running


def unrecorded_readers(root):
    """Catch a CLI fork whose wrapper exited before recording the child."""
    owned = Path(root).resolve()
    running = []
    for process in psutil.process_iter():
        if process.pid == os.getpid():
            continue
        try:
            if hasattr(os, "getuid") and process.uids().real != os.getuid():
                continue
            if not process.name().lower().startswith("python"):
                continue
            if process.status() == psutil.STATUS_ZOMBIE:
                continue
            paths = [process.cwd(), *process.cmdline()]
            if any(Path(value).is_absolute() and Path(value).resolve().is_relative_to(owned) for value in paths):
                running.append(process.pid)
        except psutil.NoSuchProcess:
            pass
        # An unreadable same-user Python process cannot establish clean exit.
    return running


if __name__ == "__main__":
    found = running_readers(sys.argv[1])
    if len(sys.argv) == 3:
        found.extend(unrecorded_readers(sys.argv[2]))
    print(json.dumps({"running": sorted(set(found))}))
