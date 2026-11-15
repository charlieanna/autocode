"""Say whether a suite failure belongs to this branch or was already on its base (#2603).

A red ``--changed`` run does not say whose failure it is, so an agent debugs its
own breakage and someone else's alike. This runs only the failing modules against
the recorded tested checkout's base commit. A failure is pre-existing only when every recorded
head test and failure signature reproduces; incomplete evidence stays unresolved.

Informational: it always exits 0. The suite job is already red, and ``ci-required``
is what blocks the merge; this only decides which module is whose.

    python3 tools/attribute_failure.py --base SHA --failed .suite-failed.json

Run those modules in a standalone clone of the base, so each sees that revision's own
tests and tools rather than this branch's.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import autocode_util as util
import ci_failure_signatures as failure_signatures
import ci_tested_source as tested_source

PRE_EXISTING = "pre-existing"
INTRODUCED = "introduced"
NEW_MODULE = "introduced (new)"
DIFFERENT = "introduced (different failures)"
UNRESOLVED = "unresolved (base also fails)"
INCOMPLETE = "unresolved (base incomplete)"
HEAD_INCOMPLETE = "unresolved (head incomplete)"
SOURCE_UNRESOLVED = "unresolved (tested source)"


def head_rows(path: Path, *, contextual: bool) -> list:
    """Validate all supplied evidence before starting any base probe."""
    document = json.loads(path.read_text())
    if isinstance(document, dict):
        version = document.get("version")
        if (contextual or "version" in document) and (type(version) is not int or version != 1):
            raise ValueError("unsupported head report")
        modules = document.get("failures")
    else:
        modules = document if not contextual else None
    if not isinstance(modules, list):
        raise ValueError("invalid head report")
    seen = set()
    for item in modules:
        module = item.get("module") if isinstance(item, dict) else item
        if not isinstance(module, str) or not all(part.isidentifier() for part in module.split(".")) or module in seen:
            raise ValueError("invalid or repeated module")
        seen.add(module)
        if isinstance(item, dict):
            if "timed_out" in item and type(item["timed_out"]) is not bool:
                raise ValueError("invalid head timeout")
            failures = item.get("failures", [])
            if not isinstance(failures, list) or any(not isinstance(failure, dict) for failure in failures):
                raise ValueError("invalid head failures")
            if item.get("timed_out") is not True and any(
                not all(isinstance(failure.get(key), str) and failure[key] for key in ("test", "kind", "signature"))
                for failure in failures
            ):
                raise ValueError("invalid head failure identity")
        elif contextual:
            raise ValueError("invalid contextual module")
    return modules


def write_json(path: Path | None, document) -> None:
    if path is not None:
        try:
            path.write_text(json.dumps(document, indent=2) + "\n")
        except OSError:
            print("Could not save the informational attribution JSON.", file=sys.stderr)


def incomplete(args, status: str, reason: str, *, source=None) -> int:
    """A global observation, never an invented failing-module selector."""
    if source is not None:
        print(source.markdown() + "\n")
    print(f"### Failure attribution\n\n{status}: {reason} No base probe was run.")
    document = {"status": status, "reason": reason, "source": source.as_dict() if source else None}
    if args.tested_source is not None:
        document.update(version=2, modules={})
    write_json(args.json, document)
    return 0


def classify(module: str, base: Path, timeout: float, *, head_failures: list[dict] | None = None) -> str:
    """One module's verdict against the base checkout: introduced here, or already red there."""
    if not (base / "tests" / f"{module.rsplit('.', 1)[-1]}.py").is_file():
        return NEW_MODULE  # the module itself is new, so the failure cannot be the base's
    environment = util.git_environment({"PYTHONDONTWRITEBYTECODE": "1"})
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
    parser.add_argument("--expected-head", help="explicit PR head, paired with --tested-source")
    parser.add_argument("--tested-source", type=Path, help="the ci_tested_source record captured before the suite")
    parser.add_argument("--failed", required=True, type=Path, help="the JSON report run_suite --failed-out wrote")
    parser.add_argument("--timeout", type=float, default=900, help="per-module seconds on the base")
    parser.add_argument("--json", type=Path, default=None, help="also write the verdicts as JSON here")
    args = parser.parse_args(argv)
    if (args.expected_head is None) != (args.tested_source is None):
        parser.error("--expected-head and --tested-source must be supplied together")

    try:
        modules = head_rows(args.failed, contextual=args.tested_source is not None)
    except (OSError, ValueError, TypeError):
        return incomplete(args, HEAD_INCOMPLETE, "The suite failure report is missing, unreadable or invalid.")
    if not modules:
        if args.tested_source is not None:
            return incomplete(args, HEAD_INCOMPLETE, "The failed suite supplied no failing-module evidence.")
        print("No failing module to attribute.")
        return 0

    source = None
    comparison = args.base
    if args.tested_source is not None:
        try:
            source = tested_source.SourceContext.load(
                args.tested_source, expected_head=args.expected_head, event_base=args.base
            ).check_current(Path.cwd())
        except (OSError, ValueError, TypeError):
            return incomplete(args, SOURCE_UNRESOLVED, "The pre-suite source record cannot be verified.")
        comparison, _, reason = source.resolution()
        if comparison is None:
            return incomplete(args, SOURCE_UNRESOLVED, reason, source=source)

    def timed_out(item):
        return isinstance(item, dict) and item.get("timed_out") is True

    rows = []
    if all(timed_out(item) for item in modules):
        rows = [(item["module"], HEAD_INCOMPLETE) for item in modules]
    else:
        try:
            with tempfile.TemporaryDirectory(prefix="attribute-base-") as scratch:
                worktree = Path(scratch) / "base"
                subprocess.run(
                    ["git", "clone", "--no-hardlinks", "--no-checkout", ".", str(worktree)],
                    check=True,
                    capture_output=True,
                    env=util.git_environment(),
                )
                subprocess.run(
                    ["git", "-C", str(worktree), "checkout", "--detach", comparison],
                    check=True,
                    capture_output=True,
                    env=util.git_environment(),
                )
                rows = [
                    (
                        item["module"] if isinstance(item, dict) else item,
                        HEAD_INCOMPLETE
                        if timed_out(item)
                        else classify(
                            item["module"] if isinstance(item, dict) else item,
                            worktree,
                            args.timeout,
                            head_failures=item.get("failures") if isinstance(item, dict) else None,
                        ),
                    )
                    for item in modules
                ]
        except (OSError, subprocess.SubprocessError):
            rows = [
                (item["module"] if isinstance(item, dict) else item, HEAD_INCOMPLETE if timed_out(item) else INCOMPLETE)
                for item in modules
            ]

    if source is not None:
        print(source.markdown() + "\n")
    print(report(rows))
    write_json(
        args.json,
        {"version": 2, "status": "resolved", "source": source.as_dict(), "modules": dict(rows)}
        if source is not None
        else dict(rows),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
