"""Check fixture-owned reader identities and stop its private warm source hosts."""

import json
import os
import stat
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


def stop_private_source_hosts(root):
    """Stop hosts on this fixture's socket directory, including editor-specific fingerprints."""
    tools = Path(__file__).resolve().parents[1] / "tools"
    sys.path.insert(0, str(tools))
    import autocode_source_host as source_host

    directory = Path(root).resolve(strict=True) / "tmp" / f"autocode-{os.getuid()}"
    if not directory.exists():
        return []
    mode = directory.lstat()
    if not stat.S_ISDIR(mode.st_mode) or mode.st_uid != os.getuid() or mode.st_mode & 0o077:
        raise ValueError(f"Fixture source socket directory is not private: {directory}")
    stopped = []
    for socket in directory.glob("source-????????????????????.sock"):
        mode = socket.lstat()
        if not stat.S_ISSOCK(mode.st_mode) or mode.st_uid != os.getuid():
            raise ValueError(f"Unexpected fixture source socket: {socket}")
        status = source_host.host_status(socket)
        if not status.get("running"):
            continue
        process = psutil.Process(status["pid"])
        if process.cmdline()[1:] != [str(tools / "autocode_source_host.py"), str(socket)]:
            raise ValueError(f"Unexpected fixture source host identity: {socket}")
        if not source_host.stop_host(socket).get("stopped"):
            raise RuntimeError(f"Fixture source host did not stop: {socket}")
        stopped.append(status["pid"])
    return stopped


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--stop-source-hosts":
        print(json.dumps({"stopped": stop_private_source_hosts(sys.argv[2])}))
    else:
        found = running_readers(sys.argv[1])
        if len(sys.argv) == 3:
            found.extend(unrecorded_readers(sys.argv[2]))
        print(json.dumps({"running": sorted(set(found))}))
