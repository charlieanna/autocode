"""Prefer scoped descendant discovery over macOS psutil's whole-table parent scan.

Candidate PIDs never establish ownership: each child and parent is checked
again through psutil, and the supervisor subsequently checks birth identities.
macOS API: Apple xnu/libsyscall/wrappers/libproc/libproc.h and libproc.c.
"""
from functools import lru_cache
import sys

import psutil


@lru_cache(maxsize=1)
def _library():
    import ctypes

    library = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
    function = library.proc_listchildpids
    function.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    function.restype = ctypes.c_int
    return library


def _child_pids(pid):
    import ctypes

    # proc_listchildpids returns a PID count, unlike proc_listpids' byte count.
    # A full buffer may be truncated; grow it instead of silently losing workers.
    size = 64
    while size <= 1 << 20:
        buffer = (ctypes.c_int * size)()
        ctypes.set_errno(0)
        count = _library().proc_listchildpids(pid, buffer, ctypes.sizeof(buffer))
        error = ctypes.get_errno()
        if count < 0 or (count == 0 and error):
            raise OSError(error or 5, 'Cannot enumerate owned process children')
        if count > size:
            raise OSError('Invalid child-process count')
        if count < size:
            return [pid for pid in buffer[:count] if pid > 0]
        size *= 2
    raise OSError('Child-process enumeration remained truncated')


def descendants(parent):
    if sys.platform != 'darwin':
        return parent.children(recursive=True)
    try:
        import ctypes
    except (ImportError, PermissionError):
        # Some sandboxes deny ctypes' import-time OS probe, not process lookup.
        ctypes = None
    found, seen, pending = [], {parent.pid}, [parent]
    while pending:
        node = pending.pop()
        try:
            if not node.is_running():
                continue
            children = []
            candidates = (_child_pids(node.pid) if ctypes is not None else
                          [child.pid for child in node.children(recursive=False)])
            for pid in candidates:
                if pid in seen:
                    continue
                try:
                    child = psutil.Process(pid)
                    # Raw macOS birth times do not depend on clock adjustment.
                    if child.ppid() == node.pid and child._ident[1] >= node._ident[1]:
                        children.append(child)
                except psutil.NoSuchProcess:
                    continue
            # Do not attach candidates to a different process reusing this PID.
            if not node.is_running():
                continue
            for child in children:
                seen.add(child.pid)
                found.append(child)
                pending.append(child)
        except psutil.NoSuchProcess:
            continue
    return found
