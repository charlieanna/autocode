"""Forget learned project defaults and conventions without changing saved runs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from . import autocode_project_memory as memory
    from . import autocode_project_memory_interface as memory_interface
except ImportError:
    import autocode_project_memory as memory
    import autocode_project_memory_interface as memory_interface


def parser() -> argparse.ArgumentParser:
    """The arguments `autocode forget` reads; `autocode completion` reads them too."""
    result = argparse.ArgumentParser(prog="autocode forget", description=__doc__)
    result.add_argument("--workspace", type=Path, default=Path.cwd(), help="project or task worktree (default: cwd)")
    result.add_argument("--json", action="store_true", help="print the result as JSON")
    return result


def cli(argv=None):
    args = parser().parse_args(argv)
    selected = args.workspace.resolve()
    try:
        project = memory_interface.selected(selected)
        forgotten = memory.forget(project)
    except (OSError, ValueError) as error:
        message = "Cannot forget project memory: " + str(error)
        if args.json:
            print(json.dumps({"error": message}))
        else:
            print(message, file=sys.stderr)
        return 1
    result = {"forgotten": forgotten, "workspace": str(project), "path": ".autocode/project.json"}
    if args.json:
        print(json.dumps(result))
    else:
        print(("Forgot learned project memory" if forgotten else "No learned project memory") + f" for {project}")
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
