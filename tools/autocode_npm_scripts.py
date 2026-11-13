"""The package ``test`` script an ``npm test`` command runs, read from package.json without running npm.

A runner that owns a reporter (Vitest, node:test) needs to know what ``npm test`` would start before it
can ask that command for per-test results. Only npm's own spellings of the ``test`` script are read:
``npm test``, ``npm t``, ``npm run test`` and ``npm run-script test``, with ``--silent`` and ``--prefix``.
Lower layer: imports nothing from AutoCode.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

SPELLINGS = (["test"], ["t"], ["run", "test"], ["run-script", "test"])


def words(command) -> list[str] | None:
    """The words of one plain command, or None for a shell construct the runner will not rewrite."""
    if not isinstance(command, str) or re.search(r"[;&|<>`$()\n\r]", command):
        return None
    try:
        return shlex.split(command)
    except ValueError:
        return None


def test_script(command, tree) -> tuple[dict, list[str], str] | None:
    """What ``command`` runs when it is npm's ``test`` script of a package inside ``tree``.

    Returns the package's ``scripts`` object, the words npm appends to the script (those after ``--``)
    and the package directory as the command names it (``.`` without ``--prefix``). None for any other
    command, a package outside ``tree`` and a package.json that cannot be read.
    """
    found = words(command)
    if not found or Path(found[0]).name not in ("npm", "npm.cmd") or tree is None:
        return None
    at = found.index("--") if "--" in found else len(found)
    args, extra = found[1:at], found[at + 1 :]
    prefix, selected = ".", []
    while args:
        arg, *args = args
        if arg in ("--silent", "-s"):
            continue
        if arg == "--prefix":
            if not args:
                return None
            prefix, *args = args
        elif arg.startswith("--prefix="):
            prefix = arg.partition("=")[2]
        else:
            selected.append(arg)
    if selected not in SPELLINGS:
        return None
    try:
        root = Path(tree).resolve()
        package_root = (root / prefix).resolve()
        if not package_root.is_relative_to(root):
            return None
        scripts = json.loads((package_root / "package.json").read_text()).get("scripts", {})
    except (OSError, ValueError, TypeError, AttributeError, RuntimeError):
        return None
    return (scripts, extra, prefix) if isinstance(scripts, dict) else None
