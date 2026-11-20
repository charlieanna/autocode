"""Portable identities for observed unittest failures, shared by CI and base attribution."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

HEADER = re.compile(r"^={70}\n(FAIL|ERROR|UNEXPECTED SUCCESS): (\S+) \(([^\n()]+)\)([^\n]*)\n", re.M)
FOOTER = re.compile(r"^-{70}\nRan \d+ tests? in [^\n]*\n\n(?:FAILED|OK)\b", re.M)
# A traceback frame header; its path and line number are where the code is, not what failed.
FRAME = re.compile(r'^  File "[^"\n]*", line \d+(?:, in (.*))?$')
# unittest joins a FAIL's standard message and the test's own message with "%s : %s".
APPENDED = " : "
QUOTE_OPENERS = " ([{,:="


def appended_at(line: str) -> int | None:
    """Where unittest's ' : ' joins an appended message in this line, outside quoted and bracketed values."""
    depth = 0
    quote = ""
    index = 0
    while index < len(line):
        char = line[index]
        if quote:
            if char == "\\":
                index += 1
            elif char == quote:
                quote = ""
        elif char in "'\"" and (index == 0 or line[index - 1] in QUOTE_OPENERS):
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        elif depth == 0 and line.startswith(APPENDED, index):
            return index
        index += 1
    return None


def compared(exception: str) -> str:
    """A FAIL's exception text without the message the test appended to what the assertion compared.

    unittest writes ``standard : message``: on the first line when the standard message is one line, on
    a line of its own when the standard message ends with a newline (a string diff), otherwise at the end
    of the last line (a list diff, a set difference, assertCountEqual).
    """
    lines = exception.split("\n")
    cut = appended_at(lines[0])
    if cut is not None:
        lines[0] = lines[0][:cut]
        return "\n".join(lines)
    for index, line in enumerate(lines[1:], 1):
        if line.startswith(APPENDED):
            return "\n".join(lines[:index])
    cut = appended_at(lines[-1])
    if cut is not None:
        lines[-1] = lines[-1][:cut]
    return "\n".join(lines)


def identity(test: str, kind: str, detail: str) -> str:
    """Hash the test, the kind, each frame's function and code, and the exception text after the last frame."""
    lines = detail.splitlines()
    frames: list[list[str]] = []
    start = 0
    index = 0
    while index < len(lines):
        frame = FRAME.match(lines[index])
        index += 1
        if frame:
            code = []
            while index < len(lines) and lines[index].startswith("    "):
                code.append(lines[index].strip())
                index += 1
            frames.append([frame.group(1) or "", *code])
            start = index
    exception = "\n".join(lines[start:]).strip()
    if kind == "FAIL":
        exception = compared(exception)
    return hashlib.sha256(json.dumps([test, kind, frames, exception]).encode()).hexdigest()


def signatures(output: str, root: Path) -> list[dict[str, str]]:
    """Identify each failure by what failed, not by checkout paths, line numbers or text appended to it."""
    headers = list(HEADER.finditer(output))
    result = []
    for index, match in enumerate(headers):
        kind, method, owner, suffix = match.groups()
        test_id = owner if owner.endswith("." + method) else owner + "." + method
        end = headers[index + 1].start() if index + 1 < len(headers) else len(output)
        footer = FOOTER.search(output, match.end(), end)
        detail = output[match.end() : footer.start() if footer else end]
        detail = detail.removeprefix("-" * 70 + "\n").strip()
        for directory in sorted({str(root.absolute()), str(root.resolve())}, key=len, reverse=True):
            detail = detail.replace(directory, "<checkout>")
        result.append({"test": test_id + suffix, "kind": kind, "signature": identity(test_id + suffix, kind, detail)})
    return result


def result_rows(result) -> list[dict]:
    """Give serial unittest results the same per-module output representation as child runs."""
    rows: dict[str, dict] = {}
    failures = [("FAIL", test, detail) for test, detail in result.failures]
    failures += [("ERROR", test, detail) for test, detail in result.errors]
    failures += [("UNEXPECTED SUCCESS", test, "") for test in result.unexpectedSuccesses]
    for kind, test, detail in failures:
        case = getattr(test, "test_case", test)
        if hasattr(case, "_testMethodName"):
            module = case.__class__.__module__
            owner = module + "." + case.__class__.__qualname__
            method = case._testMethodName
            suffix = test.id().removeprefix(case.id()) if test is not case else ""
        else:
            # unittest represents class/module setup failures as _ErrorHolder,
            # whose ID already names the fixture and its owning class/module.
            method, _, owner = test.id().partition(" (")
            owner = owner.removesuffix(")")
            module = next(
                (
                    name
                    for name in sorted(sys.modules, key=len, reverse=True)
                    if owner == name or owner.startswith(name + ".")
                ),
                owner,
            )
            suffix = ""
        row = rows.setdefault(module, {"module": module, "ok": False, "output": ""})
        row["output"] += "=" * 70 + f"\n{kind}: {method} ({owner}){suffix}\n" + "-" * 70 + "\n" + detail + "\n"
    return list(rows.values())


def write(path: Path, rows: list[dict], root: Path) -> None:
    """Write only failed modules, retaining test identities instead of exonerating whole modules."""
    failures = [
        {
            "module": row["module"],
            "failures": signatures(row["output"], root),
            "timed_out": bool(row.get("timed_out")),
        }
        for row in rows
        if not row["ok"]
    ]
    path.write_text(json.dumps({"version": 1, "failures": failures}, indent=2) + "\n")
