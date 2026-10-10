"""Serialize dashboard commands and deletion for one canonical workspace.

Only in-process dashboard requests cooperate here. External workers are still
checked by the deletion preflight and the runner's own writer locks.
"""

import threading
from contextlib import contextmanager
from pathlib import Path


def command_workspace(args):
    for index, arg in enumerate(args):
        if arg == "--workspace" and index + 1 < len(args):
            return args[index + 1]
        if isinstance(arg, str) and arg.startswith("--workspace="):
            return arg.split("=", 1)[1]
    return None


class WorkspaceCommandGate:
    def __init__(self):
        self._guard = threading.Lock()
        self._entries = {}

    @contextmanager
    def hold(self, workspace):
        if workspace is None:
            yield
            return
        key = str(Path(workspace).resolve())
        with self._guard:
            lock, users = self._entries.get(key, (threading.RLock(), 0))
            self._entries[key] = lock, users + 1
        try:
            with lock:
                yield
        finally:
            with self._guard:
                lock, users = self._entries[key]
                if users == 1:
                    del self._entries[key]
                else:
                    self._entries[key] = lock, users - 1
