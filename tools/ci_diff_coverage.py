"""Diff coverage of this repository's own pull requests (#2754). Advisory: it never fails a check.

AutoCode holds the projects it builds to "no untested code" (AGENTS.md); this holds AutoCode's own
changes to the same report. Two steps, both run by the ``diff-coverage`` job in
``.github/workflows/tests.yml``:

``run --data-dir DIR -- COMMAND...``
    Runs ``COMMAND`` (the ``run_suite.py --changed`` selection) with ``COVERAGE_PROCESS_START`` set, so
    every Python process started from this interpreter's environment is measured, including the CLI
    subprocesses most tests launch. coverage.py's own ``.pth`` file starts measurement there. Then it
    combines the per-process data into ``DIR/coverage.json``. It returns the command's exit status.

``report --base SHA --coverage-json FILE``
    Intersects that report with the changed ``tools/`` lines since ``SHA``, using the same hunk logic
    AutoCode applies to the projects it builds (``autocode_diff_coverage``), and writes Markdown for the
    job summary and up to ``--annotations`` notices on the changed lines no selected test executed.

A line excluded with ``# pragma: no cover`` is not a statement to coverage.py, so it is not reported:
that comment is where a recorded reason goes until the check becomes required.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

try:
    from . import autocode_diff_coverage as diff_coverage
except ImportError:
    import autocode_diff_coverage as diff_coverage

REPO_ROOT = Path(__file__).resolve().parent.parent
PRODUCT = "tools/"
# Warnings a measured test subprocess would otherwise print to its own stderr, which tests read.
QUIET = ("module-not-measured", "module-not-imported", "no-data-collected", "couldnt-parse", "no-ctracer")


def config_text(data_dir, source) -> str:
    return "\n".join(
        [
            "[run]",
            "parallel = True",
            f"data_file = {Path(data_dir).resolve() / '.coverage'}",
            f"source = {Path(source).resolve()}",
            "sigterm = True",
            "disable_warnings = " + ", ".join(QUIET),
            "",
        ]
    )


def run(data_dir, command, *, root=None, python=sys.executable) -> int:
    """Run ``command`` under subprocess coverage and leave ``data_dir/coverage.json``. Returns its status."""
    root = REPO_ROOT if root is None else root
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    rcfile = data_dir / "coveragerc"
    rcfile.write_text(config_text(data_dir, Path(root) / PRODUCT))
    measured = dict(os.environ, COVERAGE_PROCESS_START=str(rcfile))
    status = subprocess.run(command, cwd=root, env=measured).returncode
    plain = {name: value for name, value in os.environ.items() if not name.startswith("COVERAGE_PROCESS_")}
    for step in (["combine", "--quiet"], ["json", "--quiet", "--ignore-errors", "-o", str(data_dir / "coverage.json")]):
        subprocess.run([python, "-m", "coverage", *step, f"--rcfile={rcfile}"], cwd=root, env=plain)
    return status


def is_product(path) -> bool:
    """A Python file under tools/ that is not itself a test."""
    parts = Path(path).parts
    return path.startswith(PRODUCT) and path.endswith(".py") and "tests" not in parts[1:-1]


def changed_files(root, base) -> list[tuple[str, str]]:
    """``(path, status)`` for each product file the change adds or modifies. A rename is a delete and an add."""
    listing = subprocess.run(
        ["git", "-C", str(root), "diff", "--name-status", "--no-renames", base, "--", PRODUCT],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    files = []
    for line in listing.splitlines():
        status, _, path = line.partition("\t")
        if path and is_product(path) and not status.startswith("D"):
            files.append((path, "added" if status.startswith("A") else "modified"))
    return files


def measure(diff_text, payload, root) -> dict:
    """The changed statement lines that ran and did not, per file, from a coverage.py JSON ``payload``."""
    executed, missing = diff_coverage.parse_coverage_json(payload, root)
    result = diff_coverage.intersect(diff_coverage.hunks(diff_text), executed, missing)
    statements = covered = 0
    for item in result["files"]:
        ran = executed.get(item["path"], set())
        gap = missing.get(item["path"], set()) - ran
        lines = {n for hunk in item["hunks"] for n in range(hunk["start"], hunk["end"] + 1)}
        statements += len(lines & (ran | gap))
        covered += len(lines & ran)
    result.update(statements=statements, covered=covered)
    return result


def spans(numbers) -> list[tuple[int, int]]:
    """Consecutive runs of line numbers: [3, 4, 5, 9] is [(3, 5), (9, 9)]."""
    runs: list[tuple[int, int]] = []
    for number in sorted(set(numbers)):
        if runs and number == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], number)
        else:
            runs.append((number, number))
    return runs


def _ranges(numbers) -> str:
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in spans(numbers))


def uncovered_by_file(result) -> dict[str, list[int]]:
    lines: dict[str, list[int]] = {}
    for hunk in result["uncovered"]:
        lines.setdefault(hunk["path"], []).extend(hunk["uncovered_lines"])
    return lines


def markdown(result) -> str:
    heading = "## Diff coverage of changed `tools/` lines (advisory)\n"
    if not result["files"]:
        return heading + "\nThis pull request changes no Python product lines under `tools/`.\n"
    statements, covered = result["statements"], result["covered"]
    share = f" ({100 * covered // statements}%)" if statements else ""
    body = [
        heading,
        f"{covered} of {statements} changed statement lines ran under the selected tests{share}.",
        "",
    ]
    missed = uncovered_by_file(result)
    if missed:
        body += [
            "Each line below is changed by this pull request and no selected test executed it. Add a test that "
            "exercises it, or mark a line no public input reaches with `# pragma: no cover` and the reason.",
            "",
            "| File | Changed lines not executed |",
            "| --- | --- |",
        ]
        body += [f"| `{path}` | {_ranges(lines)} |" for path, lines in sorted(missed.items())]
    else:
        body.append("Every changed statement line ran.")
    if result["unmeasured"]:
        body += ["", "Not measured (coverage.py has no data for these files): "]
        body.append(", ".join(f"`{path}`" for path in result["unmeasured"]))
    return "\n".join(body) + "\n"


def notices(result, limit) -> list[str]:
    """GitHub ``::notice`` commands on the first ``limit`` uncovered spans, which show on the diff."""
    commands = []
    for path, lines in sorted(uncovered_by_file(result).items()):
        for start, end in spans(lines):
            commands.append(
                f"::notice file={path},line={start},endLine={end},title=Not executed by tests::"
                "No test in this pull request's selection executed this changed line."
            )
    return commands[:limit]


def report(base, coverage_json, *, root=None) -> dict:
    root = REPO_ROOT if root is None else root
    files = changed_files(root, base)
    diff_text = diff_coverage.product_diff(root, base, files) if files else ""
    try:
        payload = json.loads(Path(coverage_json).read_text())
    except (OSError, ValueError):
        payload = {}
    return measure(diff_text, payload, root)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="action", required=True)
    runner = commands.add_parser("run", help="run a command under subprocess coverage")
    runner.add_argument("--data-dir", required=True)
    runner.add_argument("command", nargs=argparse.REMAINDER)
    reporter = commands.add_parser("report", help="report the changed tools/ lines no test executed")
    reporter.add_argument("--base", required=True)
    reporter.add_argument("--coverage-json", required=True)
    reporter.add_argument("--summary", help="append the Markdown here (the job's GITHUB_STEP_SUMMARY)")
    reporter.add_argument("--json-out", help="write the full result here")
    reporter.add_argument("--annotations", type=int, default=10, help="most notices to print (GitHub shows 10)")
    args = parser.parse_args(argv)
    if args.action == "run":
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        if not command:
            parser.error("run needs a command after --")
        return run(args.data_dir, command)
    result = report(args.base, args.coverage_json)
    text = markdown(result)
    print(text)
    for line in notices(result, args.annotations):
        print(line)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as summary:
            summary.write(text)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(result, indent=2, default=sorted))
    return 0  # advisory: the report never fails the job


if __name__ == "__main__":  # pragma: no cover - the script entry; tests call main()
    sys.exit(main())
