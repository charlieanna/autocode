"""Stop only an identified, test-owned editor process tree after a host-close deadline."""

import contextlib
import json
import sys

import psutil


def still_running(process):
    with contextlib.suppress(psutil.NoSuchProcess):
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    return False


def main(argv):
    operation, pid_text, *identity = argv
    try:
        parent = psutil.Process(int(pid_text))
        started = parent.create_time()
    except psutil.NoSuchProcess:
        print(json.dumps({"running": False}))
        return
    if operation == "identity":
        print(json.dumps({"started": started}))
        return
    if operation != "stop" or len(identity) != 1 or started != float(identity[0]):
        raise ValueError("Editor process identity changed; refusing cleanup")
    processes = [*parent.children(recursive=True), parent]
    for process in processes:
        with contextlib.suppress(psutil.NoSuchProcess):
            process.terminate()
    _, alive = psutil.wait_procs(processes, timeout=3)
    alive = [process for process in alive if still_running(process)]
    for process in alive:
        with contextlib.suppress(psutil.NoSuchProcess):
            process.kill()
    _, alive = psutil.wait_procs(alive, timeout=3)
    alive = [process for process in alive if still_running(process)]
    if alive:
        raise RuntimeError(f"Owned editor processes did not exit: {[process.pid for process in alive]}")
    print(json.dumps({"running": False}))


if __name__ == "__main__":
    main(sys.argv[1:])
