"""Say whether a suite failure belongs to this branch or was already on its base (#2603).

A red ``--changed`` run does not say whose failure it is, so an agent debugs its
own breakage and someone else's alike. This runs only the failing modules against
the event's pinned base commit and reports each as ``introduced`` (passes on the
base, so this branch caused it) or ``pre-existing`` (fails there too, so it is
master's and belongs in its own small pull request).

Informational: it always exits 0. The suite job is already red, and ``ci-required``
is what blocks the merge; this only decides which module is whose.

    python3 tools/attribute_failure.py --base SHA --failed .suite-failed.json

Run those modules in a worktree of the base, so each sees that revision's own
tests and tools rather than this branch's.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

PRE_EXISTING = "pre-existing"
INTRODUCED = "introduced"
NEW_MODULE = "introduced (new)"


def classify(module: str, base: Path, timeout: float) -> str:
    """One module's verdict against the base checkout: introduced here, or already red there."""
    if not (base / "tests" / f"{module.rsplit('.', 1)[-1]}.py").is_file():
        return NEW_MODULE  # the module itself is new, so the failure cannot be the base's
    probe = subprocess.run(
        [sys.executable, "-m", "unittest", module],
        cwd=base,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
    )
    return PRE_EXISTING if probe.returncode != 0 else INTRODUCED


def report(rows: list[tuple[str, str]]) -> str:
    """The attribution as a markdown table, for a step summary or a pull request comment."""
    lines = ["| Module | on base | verdict |", "|---|---|---|"]
    lines += [
        f"| `{module}` | {'fails' if verdict == PRE_EXISTING else 'passes'} | {verdict} |" for module, verdict in rows
    ]
    introduced = [module for module, verdict in rows if verdict != PRE_EXISTING]
    verdict = (
        f"**{len(introduced)} introduced** ({', '.join(f'`{m}`' for m in introduced)}): these are this branch's to fix."
        if introduced
        else "Nothing here is this branch's to fix; the red is already on the base."
    )
    return "\n".join(lines) + "\n\n" + verdict


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", required=True, help="the event's pinned base commit, not whatever master is now")
    parser.add_argument("--failed", required=True, type=Path, help="the JSON list run_suite --failed-out wrote")
    parser.add_argument("--timeout", type=float, default=900, help="per-module seconds on the base")
    parser.add_argument("--json", type=Path, default=None, help="also write the verdicts as JSON here")
    args = parser.parse_args(argv)

    modules = json.loads(args.failed.read_text())
    if not modules:
        print("No failing module to attribute.")
        return 0

    with tempfile.TemporaryDirectory(prefix="attribute-base-") as scratch:
        worktree = Path(scratch) / "base"
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree), args.base], check=True, capture_output=True
        )
        try:
            rows = [(module, classify(module, worktree, args.timeout)) for module in modules]
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], check=False, capture_output=True)

    print(report(rows))
    if args.json is not None:
        args.json.write_text(json.dumps(dict(rows), indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
