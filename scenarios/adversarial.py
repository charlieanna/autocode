#!/usr/bin/env python3
"""Run reproducible attacks on AutoCode's CLI, preserving failures and evidence.

No live providers are invoked. A failed invariant returns nonzero; defects are
not expectedFailure markers or accepted baseline results.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
GROUPS = ("evidence", "recovery", "lifecycle", "persistence", "planning")

from scenarios.harness.attempts import atomic_json


def positive_integer(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def source_hashes():
    files = {Path(__file__).resolve(), REPO / "scenarios/harness/fake_codex.py",
             REPO / "scenarios/harness/processes.py", REPO / "scenarios/harness/attempts.py"}
    for pattern in ("test_adversarial_*.py", "harness/adversarial*.py", "harness/attack_*.py"):
        files.update((REPO / "scenarios").glob(pattern))
    return {str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}


def group_passed(report):
    return (report.get("exit_code", 0) == 0 and report["tests_run"] > 0
            and len(report["rows"]) == report["tests_run"]
            and all(row["status"] == "PASS" for row in report["rows"]))


def read_checkpoint(directory, group):
    report = json.loads((directory / "result.json").read_text())
    if (not isinstance(report, dict) or report.get("group") != group
            or type(report.get("tests_run")) is not int or report["tests_run"] < 0
            or not isinstance(report.get("rows"), list)
            or any(not isinstance(row, dict) or not isinstance(row.get("test"), str)
                   or not row["test"] or row.get("status") not in ("PASS", "FAIL", "ERROR", "NOT_EXERCISED")
                   or (row.get("evidence") is not None and not isinstance(row["evidence"], str))
                   for row in report["rows"])):
        raise ValueError("Invalid outcome checkpoint: expected group, nonnegative tests_run and result rows")
    return report


class Result(unittest.TextTestResult):
    def __init__(self, *args, checkpoint=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.rows = []
        self.checkpoint = checkpoint

    def add_row(self, test, status, detail=""):
        root = getattr(test, "root", None)
        row = {"test": test.id(), "status": status, "detail": detail,
               "evidence": str(root) if root else None}
        if root:
            trace = root / "provider-trace.jsonl"
            row["provider_trace_sha256"] = hashlib.sha256(trace.read_bytes()).hexdigest() if trace.exists() else None
        previous = next((r for r in self.rows if r["test"] == row["test"]), None)
        if previous:
            rank = {"PASS": 0, "NOT_EXERCISED": 1, "FAIL": 2, "ERROR": 3}
            row["status"] = max((previous["status"], status), key=rank.__getitem__)
            row["detail"] = "\n".join(filter(None, (previous["detail"], detail)))
            previous.update(row)
        else:
            self.rows.append(row)
        if self.checkpoint:
            self.checkpoint(self)

    def addSuccess(self, test):
        super().addSuccess(test)
        self.add_row(test, "PASS")

    def addFailure(self, test, error):
        super().addFailure(test, error)
        self.add_row(test, "FAIL", self._exc_info_to_string(error, test))

    def addError(self, test, error):
        super().addError(test, error)
        self.add_row(test, "ERROR", self._exc_info_to_string(error, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.add_row(test, "NOT_EXERCISED", reason)

    def addExpectedFailure(self, test, error):
        super().addExpectedFailure(test, error)
        self.add_row(test, "FAIL", "Expected-failure marker is not an accepted adversarial outcome")

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self.add_row(test, "ERROR", "Unexpected-success annotation must be removed")

    def addSubTest(self, test, subtest, error):
        super().addSubTest(test, subtest, error)
        if error is not None:
            status = "FAIL" if issubclass(error[0], test.failureException) else "ERROR"
            self.add_row(test, status, self._exc_info_to_string(error, subtest))


def worker(group, output):
    output.mkdir(parents=True, exist_ok=True)
    os.environ["AUTOCODE_ADVERSARIAL_OUT"] = str(output)
    suite = unittest.defaultTestLoader.loadTestsFromName("scenarios.test_adversarial_" + group)
    def checkpoint(result):
        atomic_json(output / "result.json",
                    {"group": group, "tests_run": result.testsRun, "rows": result.rows}, max_bytes=None)
    result = unittest.TextTestRunner(verbosity=2,
        resultclass=lambda *args, **kwargs: Result(*args, checkpoint=checkpoint, **kwargs)).run(suite)
    report = {"group": group, "tests_run": result.testsRun, "rows": result.rows}
    atomic_json(output / "result.json", report, max_bytes=None)
    return 0 if group_passed(report) else 1


def execute(group, output, group_timeout_seconds=600):
    from scenarios.harness.processes import run_cli
    directory = output / group
    command = [sys.executable, str(Path(__file__).resolve()), "--worker", group, "--out", str(directory)]
    result = None
    try:
        result = run_cli(command, cwd=REPO, env=os.environ.copy(), timeout=group_timeout_seconds)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "console.log").write_text(result.stdout + result.stderr)
        report = read_checkpoint(directory, group)
        report["exit_code"] = result.returncode
        return report
    except Exception as error:
        directory.mkdir(parents=True, exist_ok=True)
        detail = [str(error), *getattr(error, "__notes__", [])]
        cleanup_errors = getattr(error, "cleanup_errors", [])
        if cleanup_errors:
            detail.append("CLI cleanup incomplete: " + "; ".join(cleanup_errors))
        captured = []
        for name in ("stdout", "stderr"):
            stream = getattr(error, name, None)
            if stream is None and result is not None:
                stream = getattr(result, name)
            captured.append(stream.decode("utf-8", errors="replace") if isinstance(stream, bytes) else stream or "")
        try:
            report = read_checkpoint(directory, group)
        except (OSError, ValueError) as read_error:
            report = {"group": group, "tests_run": 0, "rows": []}
            detail.append("Cannot read outcome checkpoint: " + str(read_error))
        (directory / "console.log").write_text("".join(captured) + "\n" + "\n".join(detail) + "\n")
        report.update(exit_code=2, cleanup_errors=cleanup_errors)
        report["rows"].append({"test": group, "status": "ERROR", "detail": "\n".join(detail),
                               "evidence": str(directory)})
        atomic_json(directory / "result.json", report, max_bytes=None)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", nargs="+", choices=GROUPS, default=list(GROUPS))
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--group-timeout-seconds", type=positive_integer, default=600)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--worker", choices=GROUPS, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    output = (args.out or REPO / ".scenario-runs" / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-adversarial")).resolve()
    if args.worker:
        return worker(args.worker, output)
    if args.list:
        for group in dict.fromkeys(args.group):
            module = __import__("scenarios.test_adversarial_" + group, fromlist=["unused"])
            for name in dir(module):
                cls = getattr(module, name)
                if isinstance(cls, type) and issubclass(cls, unittest.TestCase):
                    for test in unittest.defaultTestLoader.getTestCaseNames(cls):
                        print(f"{group}: {cls.__name__}.{test}")
        return 0
    output.mkdir(parents=True, exist_ok=False)
    before_sources = source_hashes()
    groups = list(dict.fromkeys(args.group))
    with ThreadPoolExecutor(max_workers=min(args.jobs, len(groups))) as pool:
        reports = list(pool.map(lambda group: execute(group, output, args.group_timeout_seconds), groups))
    rows = [row for report in reports for row in report["rows"]]
    counts = {status: sum(row["status"] == status for row in rows) for status in ("PASS", "FAIL", "ERROR", "NOT_EXERCISED")}
    summary = {"mode": "real CLI with scripted provider and isolated process/I/O faults; no live models",
               "generated_at_utc": datetime.now(UTC).isoformat(), "counts": counts,
               "tests_run": sum(report["tests_run"] for report in reports), "groups": reports,
               "core_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()}
    summary["group_execution_failures"] = [report["group"] for report in reports
        if report["exit_code"] not in (0, 1) or len(report["rows"]) != report["tests_run"]
        or (report["exit_code"] != 0 and all(row["status"] == "PASS" for row in report["rows"]))]
    summary["test_source_sha256"] = before_sources
    summary["test_sources_unchanged"] = before_sources == source_hashes()
    summary["core_tools_modified"] = bool(subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "tools"], cwd=REPO).returncode)
    atomic_json(output / "summary.json", summary, max_bytes=None)
    lines = ["# Adversarial CLI results", "", summary["mode"], "", f"Tests: {summary['tests_run']}; results: {json.dumps(counts)}", "",
             "A failed assertion is retained for triage. It is a product defect only after the injection and control are verified.", "",
             "| Test | Result | Evidence |", "| --- | --- | --- |"]
    if summary["group_execution_failures"]:
        lines[6:6] = ["Group execution/reporting failures: " + ", ".join(summary["group_execution_failures"]), ""]
    for row in rows:
        evidence = f"[artifacts]({row['evidence']})" if row.get("evidence") else "none"
        lines.append(f"| {row['test']} | {row['status']} | {evidence} |")
    (output / "summary.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"tests_run": summary["tests_run"], "counts": counts, "report": str(output / "summary.md")}, indent=2))
    return 0 if summary["test_sources_unchanged"] and all(group_passed(r) for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
