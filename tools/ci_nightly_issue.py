#!/usr/bin/env python3
"""Report the nightly full CI run on one GitHub issue: open or comment when red, close when green.

CI tooling only, run by the tests workflow's nightly-report job with ``gh`` and the workflow token.
The required gate leaves the slow modules and most scenarios to the nightly run, so a red night
is the only signal those give; this keeps it in front of whoever merges next.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

TITLE = "Nightly full CI is red"
LABEL = "master-red"
# Each nightly job reports on its own issue: what it runs, and what its title says.
KINDS = {
    "full": (
        TITLE,
        "The nightly full run",
        "It runs every slow test module and the whole scenario catalog, which the required gate leaves out. ",
    ),
    "setup-sweep": (
        "Nightly setup sweep is red",
        "The nightly setup sweep",
        "It runs AutoCode's own setup steps, with no model, on the real repositories pinned in "
        "tools/setup_sweep_repos.json (docs/setup-sweep.md); its artifacts hold each repository's results. ",
    ),
}


def gh(*args: str, check: bool = True) -> str:
    return subprocess.run(["gh", *args], check=check, capture_output=True, text=True).stdout


def plan(result: str, existing: int | None, run_url: str, sha: str, kind: str = "full") -> list[list[str]]:
    """The gh calls for one nightly result, given the open issue's number (or None)."""
    title, run, scope = KINDS[kind]
    if result == "success":
        if existing is None:
            return []
        body = f"{run} passed on `{sha}`: {run_url}"
        return [["issue", "comment", str(existing), "--body", body], ["issue", "close", str(existing)]]
    body = (
        f"{run} ({result}) on `{sha}` did not pass: {run_url}\n\n"
        f"{scope}Fix the failure on master in its own small pull request."
    )
    if existing is not None:
        return [["issue", "comment", str(existing), "--body", body]]
    return [["issue", "create", "--title", title, "--body", body, "--label", LABEL]]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", required=True)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--kind", choices=sorted(KINDS), default="full")
    args = parser.parse_args(argv)
    title = KINDS[args.kind][0]
    found = json.loads(
        gh("issue", "list", "--state", "open", "--search", f'in:title "{title}"', "--json", "number,title")
    )
    existing = next((row["number"] for row in found if row.get("title") == title), None)
    for call in plan(args.result, existing, args.run_url, args.sha, args.kind):
        if call[:2] == ["issue", "create"]:
            gh("label", "create", LABEL, "--color", "B60205", "--force", check=False)
        print("gh " + " ".join(call[:3]))
        gh(*call)
    return 0


if __name__ == "__main__":
    sys.exit(main())
