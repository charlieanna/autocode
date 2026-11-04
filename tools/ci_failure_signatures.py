"""Portable identities for observed unittest failures, shared by CI and base attribution."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

HEADER = re.compile(r"^={70}\n(FAIL|ERROR|UNEXPECTED SUCCESS): (\S+) \(([^\n()]+)\)([^\n]*)\n", re.M)
FOOTER = re.compile(r"^-{70}\nRan \d+ tests? in [^\n]*\n\n(?:FAILED|OK)\b", re.M)


def signatures(output: str, root: Path) -> list[dict[str, str]]:
    """Keep test, assertion and exception identity while removing checkout/line-number noise."""
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
        detail = re.sub(r'(File "[^"\n]+", line )\d+', r"\1<line>", detail)
        result.append(
            {"test": test_id + suffix, "kind": kind, "signature": hashlib.sha256(detail.encode()).hexdigest()}
        )
    return result


def result_rows(result) -> list[dict]:
    """Give serial unittest results the same per-module output representation as child runs."""
    rows = {}
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
