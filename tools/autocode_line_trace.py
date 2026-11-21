"""Record which lines of a project's files a Python test command executes (#4039).

Run with the project's interpreter in place of ``-m coverage run``:

    python autocode_line_trace.py --data-file=PATH -m pytest ARGS...

The command runs exactly as ``python -m pytest ARGS...`` would, from the same directory,
with the same ``sys.argv`` and ``sys.path``. Lines are observed through ``sys.monitoring``
under a tool id of their own, so a tracer the project starts itself (pytest-cov, a
``coverage`` call in a conftest, a debugger or profiler) runs unchanged beside it and
cannot displace it. coverage.py's own ``coverage run`` shares one tracer with such a
session: the inner session pauses it, and the outer report collects no data.

At exit the executed lines of files under the working directory are written as a
coverage.py data file, which ``coverage json`` turns into the usual report (executable
lines and multi-line statements come from coverage.py's analysis, as before).

Standalone: imports only the standard library, and coverage.py when it writes. The
caller copies this file outside the tree under test, so nothing is installed into it.
Requires Python 3.12 or later; the caller keeps ``coverage run`` for older interpreters.
"""

import atexit
import os
import runpy
import sys

# The ids sys.monitoring leaves unnamed come first; the named ones belong to
# tools a project may start itself (sys.monitoring.COVERAGE_ID is coverage.py's).
TOOL_IDS = (3, 4, 2, 5, 0)
NAME = "autocode-line-trace"


def _usage():
    sys.stderr.write("usage: autocode_line_trace.py --data-file=PATH -m MODULE [ARGS...]\n")
    return 2


def main(argv):
    if len(argv) < 3 or not argv[0].startswith("--data-file=") or argv[1] != "-m":
        return _usage()
    data_file, module, arguments = os.path.abspath(argv[0].split("=", 1)[1]), argv[2], argv[3:]
    monitoring = getattr(sys, "monitoring", None)
    if monitoring is None:
        sys.stderr.write(f"{NAME}: sys.monitoring needs Python 3.12 or later\n")
        return 2
    tool = next((tool for tool in TOOL_IDS if monitoring.get_tool(tool) is None), None)
    if tool is None:
        sys.stderr.write(f"{NAME}: every sys.monitoring tool id is in use\n")
        return 2
    root = os.path.realpath(os.getcwd())
    own = os.path.realpath(__file__)
    executed: dict[str, set[int]] = {}
    owned: dict[str, str | None] = {}  # co_filename -> the measured real path, or None

    def measured(filename):
        if filename not in owned:
            path = os.path.realpath(filename) if filename and not filename.startswith("<") else None
            inside = path is not None and path != own and os.path.commonpath((root, path)) == root
            owned[filename] = path if inside and os.path.isfile(path) else None
        return owned[filename]

    def on_line(code, line):
        path = measured(code.co_filename)
        if path is not None:
            executed.setdefault(path, set()).add(line)
        return monitoring.DISABLE  # one event per line is enough

    def write():
        if tool is None or monitoring.get_tool(tool) != NAME:
            return
        monitoring.set_events(tool, 0)
        monitoring.register_callback(tool, monitoring.events.LINE, None)
        monitoring.free_tool_id(tool)
        from coverage import CoverageData

        if os.path.lexists(data_file):
            os.unlink(data_file)
        data = CoverageData(basename=data_file)
        data.add_lines({path: sorted(lines) for path, lines in executed.items()})
        data.write()

    # atexit runs handlers in reverse order, so one registered before the tests run
    # writes after any the tests register: a test cannot replace the data afterwards.
    atexit.register(write)
    monitoring.use_tool_id(tool, NAME)
    monitoring.register_callback(tool, monitoring.events.LINE, on_line)
    monitoring.set_events(tool, monitoring.events.LINE)
    # As `python -m`: the working directory first on sys.path, not this file's.
    sys.path[0] = root
    sys.argv = [module, *arguments]
    runpy.run_module(module, run_name="__main__", alter_sys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
