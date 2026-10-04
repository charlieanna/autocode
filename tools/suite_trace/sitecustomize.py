"""Record which repository files a test process runs, for ``tools/run_suite.py --record-map``.

Python imports ``sitecustomize`` at startup from the first sys.path entry that has one, so
putting this directory on PYTHONPATH loads it in the test process and in every Python process the
test starts, the CLI subprocesses included, since they inherit the environment. It does nothing
unless AUTOCODE_SUITE_TRACE names a directory.

A file counts once one of its functions runs other than at import: a call made directly from an
imported module's top level (a decorator, a table built at import) does not count; one from the
top level of the script being run does. Files under a hidden directory (.venv, scratch copies)
are left out. Each process appends a file's repository path to its own record the first time it
sees it, so a process that is killed still leaves what it ran. Standard library only; nothing
here may import AutoCode.
"""
import os
import sys
import threading

_HERE = os.path.dirname(os.path.realpath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE)) + os.sep


def _install(out: str) -> None:
    paths: dict = {}
    seen: set = set()
    record = []

    def repository_path(filename: str) -> str:
        if filename.startswith("<"):
            return ""
        path = os.path.realpath(filename)
        if not path.startswith(_ROOT) or path.startswith(_HERE):
            return ""
        relative = path[len(_ROOT):].replace(os.sep, "/")
        return "" if relative.startswith(".") or "/." in relative else relative

    def trace(frame, event, arg):
        # A global trace function is called only when a frame starts; returning None skips its lines.
        # It runs on every call, so a file already recorded returns after two lookups.
        code = frame.f_code
        path = paths.get(code.co_filename)
        if path is None:
            path = paths[code.co_filename] = repository_path(code.co_filename)
        if not path or path in seen or code.co_name == "<module>":
            return
        caller = frame.f_back
        if caller is not None and caller.f_code.co_name == "<module>" and caller.f_globals.get("__name__") != "__main__":
            return  # called while its caller's module was being imported
        seen.add(path)
        if not record:
            record.append(open(os.path.join(out, f"{os.getpid()}.txt"), "a"))
        record[0].write(path + "\n")
        record[0].flush()

    def forked() -> None:  # a forked child keeps running Python: give it its own record
        seen.clear()
        record.clear()

    os.register_at_fork(after_in_child=forked)
    sys.settrace(trace)
    threading.settrace(trace)


def _chain() -> None:
    """Run the sitecustomize this one shadows, if the interpreter has one."""
    for entry in sys.path:
        directory = os.path.realpath(entry or os.getcwd())
        candidate = os.path.join(directory, "sitecustomize.py")
        if directory != _HERE and os.path.isfile(candidate):
            with open(candidate) as source:
                exec(compile(source.read(), candidate, "exec"), {"__name__": "sitecustomize", "__file__": candidate})
            return


if os.path.isdir(os.environ.get("AUTOCODE_SUITE_TRACE", "")):
    _install(os.environ["AUTOCODE_SUITE_TRACE"])
_chain()
