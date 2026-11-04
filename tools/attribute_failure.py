"""Say whether a suite failure belongs to this branch or was already on its base (#2603).

A red ``--changed`` run does not say whose failure it is, so an agent debugs its
own breakage and someone else's alike. This runs only the failing modules against
the event's pinned base commit. A failure is pre-existing only when every recorded
head test and failure signature reproduces; incomplete evidence stays unresolved.

Informational: it always exits 0. The suite job is already red, and ``ci-required``
is what blocks the merge; this only decides which module is whose.

    python3 tools/attribute_failure.py --base SHA --failed .suite-failed.json

Run those modules in a worktree of the base, so each sees that revision's own
tests and tools rather than this branch's.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import ci_failure_signatures as failure_signatures

PRE_EXISTING = "pre-existing"
INTRODUCED = "introduced"
NEW_MODULE = "introduced (new)"
DIFFERENT = "introduced (different failures)"
UNRESOLVED = "unresolved (base also fails)"
INCOMPLETE = "unresolved (base incomplete)"
HEAD_INCOMPLETE = "unresolved (head incomplete)"


def classify(module: str, base: Path, timeout: float, *, head_failures: list[dict] | None = None) -> str:
    """One module's verdict against the base checkout: introduced here, or already red there."""
    if not (base / "tests" / f"{module.rsplit('.', 1)[-1]}.py").is_file():
        return NEW_MODULE  # the module itself is new, so the failure cannot be the base's
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    # The attribution step's comment credentials are not part of the suite environment.
    environment.pop("GH_TOKEN", None)
    environment.pop("GITHUB_TOKEN", None)
    try:
        probe = subprocess.run(
            [sys.executable, "-m", "unittest", module],
            cwd=base,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return INCOMPLETE
    if probe.returncode == 0:
        return INTRODUCED
    output = probe.stdout + probe.stderr
    if probe.returncode < 0 or not failure_signatures.FOOTER.search(output):
        return INCOMPLETE
    observed = failure_signatures.signatures(output, base)
    if not head_failures or not observed:
        return UNRESOLVED
    return PRE_EXISTING if all(failure in observed for failure in head_failures) else DIFFERENT


def report(rows: list[tuple[str, str]]) -> str:
    """The attribution as a markdown table, for a step summary or a pull request comment."""
    lines = ["| Module | on base | verdict |", "|---|---|---|"]
    base_status = {NEW_MODULE: "absent", INCOMPLETE: "unknown", HEAD_INCOMPLETE: "not checked", INTRODUCED: "passes"}
    lines += [f"| `{module}` | {base_status.get(verdict, 'fails')} | {verdict} |" for module, verdict in rows]
    introduced = [module for module, verdict in rows if verdict.startswith(INTRODUCED)]
    unresolved = [module for module, verdict in rows if verdict.startswith("unresolved")]
    notes = []
    if introduced:
        notes.append(
            f"**{len(introduced)} introduced** ({', '.join(f'`{m}`' for m in introduced)}): these head failures were not reproduced on base."
        )
    if unresolved:
        notes.append(
            f"**{len(unresolved)} unresolved**: a module-level failure or incomplete probe cannot attribute the head's failures."
        )
    if not introduced and not unresolved:
        notes.append("All observed head failures reproduced on base. This does not exonerate unexamined changes.")
    return "\n".join(lines) + "\n\n" + "\n".join(notes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", required=True, help="the event's pinned base commit, not whatever master is now")
    parser.add_argument("--failed", required=True, type=Path, help="the JSON report run_suite --failed-out wrote")
    parser.add_argument("--timeout", type=float, default=900, help="per-module seconds on the base")
    parser.add_argument("--json", type=Path, default=None, help="also write the verdicts as JSON here")
    args = parser.parse_args(argv)

    document = json.loads(args.failed.read_text())
    modules = document["failures"] if isinstance(document, dict) else document
    if not modules:
        print("No failing module to attribute.")
        return 0

    with tempfile.TemporaryDirectory(prefix="attribute-base-") as scratch:
        worktree = Path(scratch) / "base"
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree), args.base], check=True, capture_output=True
        )
        try:
            rows = [
                (
                    item["module"] if isinstance(item, dict) else item,
                    HEAD_INCOMPLETE
                    if isinstance(item, dict) and item.get("timed_out")
                    else classify(
                        item["module"] if isinstance(item, dict) else item,
                        worktree,
                        args.timeout,
                        head_failures=item.get("failures") if isinstance(item, dict) else None,
                    ),
                )
                for item in modules
            ]
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], check=False, capture_output=True)

    print(report(rows))
    if args.json is not None:
        args.json.write_text(json.dumps(dict(rows), indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
