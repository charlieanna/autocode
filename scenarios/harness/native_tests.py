"""Execute native scenario suites and retain only observed, unskipped case names."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Suite:
    process: object
    names: set[str]
    failed: set[str]
    complete: bool = True

    @property
    def passed(self) -> bool:
        return self.complete and self.process.returncode == 0 and bool(self.names) and not self.failed


def _go_cases(text: str):
    running, outcomes, packages, finished = set(), {}, set(), {}
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            return set(), set(), False
        if not isinstance(row, dict):
            return set(), set(), False
        action, package, test = row.get("Action"), row.get("Package"), row.get("Test")
        if not isinstance(action, str) or not isinstance(package, str) or not package:
            return set(), set(), False
        packages.add(package)
        if test is not None and (not isinstance(test, str) or not test):
            return set(), set(), False
        if test:
            identity = package + "/" + test
            if action == "run":
                if identity in running:
                    return set(), set(), False
                running.add(identity)
            elif action in {"pass", "fail", "skip"}:
                if identity not in running or identity in outcomes:
                    return set(), set(), False
                outcomes[identity] = action
        elif action in {"pass", "fail", "skip"}:
            if package in finished:
                return set(), set(), False
            finished[package] = action
    if running != outcomes.keys() or packages != finished.keys():
        return set(), set(), False
    names = {name for name, status in outcomes.items() if status != "skip"}
    failed = {name for name, status in outcomes.items() if status == "fail"}
    # A package can fail in a hook/build after its individually passing tests.
    if any(status == "fail" for status in finished.values()) and not failed:
        return set(), set(), False
    return names, failed, True


def _node_cases(text: str, files: list[str]):
    lines = text.splitlines()
    names, failed, counts = set(), set(), {}
    observed = {"pass": 0, "fail": 0, "skipped": 0, "todo": 0}
    for index, line in enumerate(lines):
        summary = re.fullmatch(r"# (tests|pass|fail|cancelled|skipped|todo) (\d+)", line)
        if summary:
            if summary[1] in counts:
                return set(), set(), False
            counts[summary[1]] = int(summary[2])
        match = re.match(r"(\s*)(not ok|ok) \d+ - (.*)", line)
        if not match:
            continue
        kind = "test"
        for metadata in lines[index + 1 :]:
            if not metadata.strip():
                continue
            if len(metadata) - len(metadata.lstrip()) <= len(match[1]):
                break
            declared = re.match(r"\s+type: ['\"]?(test|suite)['\"]?\s*$", metadata)
            if declared:
                kind = declared[1]
        if kind == "suite":
            continue
        directive = re.search(r"\s+# (SKIP|TODO)\b", match[3], re.I)
        if directive:
            observed["skipped" if directive[1].upper() == "SKIP" else "todo"] += 1
            continue
        name = match[3]
        # Node reports an empty test file as a passing file, without a test case.
        if name in files or any(name.endswith("/" + file) for file in files) or name in names:
            return set(), set(), False
        names.add(name)
        outcome = "fail" if match[2] == "not ok" else "pass"
        observed[outcome] += 1
        if outcome == "fail":
            failed.add(name)
    keys = {"tests", "pass", "fail", "cancelled", "skipped", "todo"}
    if (
        set(counts) != keys
        or counts["cancelled"] != 0
        or any(counts[key] != value for key, value in observed.items())
        or counts["tests"] != sum(observed.values())
    ):
        return set(), set(), False
    return names, failed, True


def _vitest_cases(data):
    if not isinstance(data, dict) or type(data.get("success")) is not bool:
        return set(), set(), False
    keys = (
        "numTotalTests",
        "numPassedTests",
        "numFailedTests",
        "numPendingTests",
        "numTodoTests",
        "numTotalTestSuites",
        "numPassedTestSuites",
        "numFailedTestSuites",
        "numPendingTestSuites",
    )
    if any(type(data.get(key)) is not int or data[key] < 0 for key in keys):
        return set(), set(), False
    files = data.get("testResults")
    if (
        not isinstance(files, list)
        or data["numTotalTestSuites"] < len(files)
        or data["numTotalTestSuites"]
        != sum(data[key] for key in ("numPassedTestSuites", "numFailedTestSuites", "numPendingTestSuites"))
    ):
        return set(), set(), False
    names, failed, seen = set(), set(), set()
    counts = {"passed": 0, "failed": 0, "pending": 0, "todo": 0}
    for file in files:
        if not isinstance(file, dict) or not isinstance(file.get("assertionResults"), list):
            return set(), set(), False
        for case in file["assertionResults"]:
            if not isinstance(case, dict):
                return set(), set(), False
            status, name = case.get("status"), case.get("fullName")
            if (
                not isinstance(status, str)
                or status not in counts
                or not isinstance(name, str)
                or not name
                or name in seen
            ):
                return set(), set(), False
            seen.add(name)
            counts[status] += 1
            if status in {"passed", "failed"}:
                names.add(name)
                if status == "failed":
                    failed.add(name)
    if (
        len(seen) != data["numTotalTests"]
        or any(
            counts[state] != data[key]
            for state, key in (
                ("passed", "numPassedTests"),
                ("failed", "numFailedTests"),
                ("pending", "numPendingTests"),
                ("todo", "numTodoTests"),
            )
        )
        or data["success"] != (not failed and data["numFailedTestSuites"] == 0)
    ):
        return set(), set(), False
    return names, failed, True


def execute(project: Path, runner: str, run, *, hidden: bool = False) -> Suite:
    names, failed, complete = set(), set(), False
    if runner == "go":
        argv = ["go", "test", "-json", "-count=1", "./..."]
        if hidden:
            argv += ["-run", "^TestOracle"]
        proc = run(argv, project, timeout=120)
        names, failed, complete = _go_cases(proc.stdout)
    elif runner == "node":
        folder = "_oracle_hidden" if hidden else "tests"
        files = sorted(str(path.relative_to(project)) for path in (project / folder).rglob("*.test.js"))
        # No files must never fall through to Node's automatic discovery.
        if not files:
            return Suite(
                run(["node", "--test", "--test-reporter=tap", folder + "/missing.test.js"], project, timeout=120),
                names,
                failed,
                complete=False,
            )
        proc = run(["node", "--test", "--test-reporter=tap", *files], project, timeout=120)
        names, failed, complete = _node_cases(proc.stdout, files)
    elif runner == "vitest":
        report = project / ".oracle-vitest.json"
        report.unlink(missing_ok=True)
        proc = run(
            [
                "node",
                "node_modules/vitest/vitest.mjs",
                "run",
                "_oracle_hidden" if hidden else "tests",
                "--reporter=json",
                "--outputFile=" + str(report),
            ],
            project,
            timeout=120,
        )
        try:
            data = json.loads(report.read_text())
            names, failed, complete = _vitest_cases(data)
        except (OSError, ValueError):
            pass
        finally:
            report.unlink(missing_ok=True)
    else:
        raise ValueError(f"Unknown native scenario runner: {runner}")
    return Suite(proc, names, failed, complete)
