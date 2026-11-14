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


_UNSAFE_SHELL = re.compile(r"[;|<>`$()\n\r\\]")


def split_and(script) -> list[str] | None:
    """Split a composite ``a && b`` npm script into single commands, or None for unsupported shell syntax."""
    if not isinstance(script, str) or "&&" not in script or _UNSAFE_SHELL.search(script):
        return None
    parts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i = 0
    n = len(script)
    while i < n:
        ch = script[i]
        if quote is not None:
            if ch == quote:
                quote = None
            buf.append(ch)
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if script[i : i + 2] == "&&":
            parts.append("".join(buf).strip())
            buf.clear()
            i += 2
            continue
        if ch == "&":
            return None  # single background `&`
        buf.append(ch)
        i += 1
    if quote is not None:
        return None
    parts.append("".join(buf).strip())
    if len(parts) < 2 or any(not part or words(part) is None for part in parts):
        return None
    return parts


def safe_root_script(command, tree) -> list[str] | None:
    """Return split ``&&`` steps for a root ``npm test`` without extra args, lifecycle hooks, or ``.npmrc``."""
    if tree is None:
        return None
    found = test_script(command, tree)
    if not found:
        return None
    scripts, extra, prefix = found
    if extra or prefix != "." or "pretest" in scripts or "posttest" in scripts:
        return None
    if (Path(tree) / ".npmrc").exists():
        return None
    return split_and(scripts.get("test"))


def npm_exec_call(steps: list[str]) -> str:
    """Wrap composite ``&&`` script steps for deterministic exit-code-preserving ``npm exec --silent --call``."""
    return "npm exec --silent --call " + shlex.quote(" && ".join(steps))
