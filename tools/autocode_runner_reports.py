"""Per-test results of runners that name their tests only in JUnit XML: Maven, Gradle, PHPUnit and CTest.

These runners print no line AutoCode reads per test, so their proofs are judged by exit code. Each of them
writes JUnit XML, which names every test and its outcome: Maven Surefire and Gradle by default, one file per
test class (``target/surefire-reports/TEST-*.xml``, ``build/test-results/<task>/TEST-*.xml``, in each module),
PHPUnit with ``--log-junit FILE`` and CTest with ``--output-junit FILE``. The mutation check of changed
lines reads them (autocode_changed_code_proof), so a mutant of a Java, Kotlin, C, C++ or PHP line is killed
only when a named test failed in a report written by that run.

``prepare`` removes every report an earlier run left and returns the command that writes its report into
``report``; ``results`` merges what that run wrote, by autocode_junit's strict reader. A run that wrote no
report (a mutant that did not compile), or a report the reader refuses, has no results: never a kill.
Imports only autocode_junit.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
from pathlib import Path, PurePosixPath

try:
    from . import autocode_junit as junit
except ImportError:
    import autocode_junit as junit

RUNNERS = ("maven", "gradle", "phpunit", "ctest")
# Build tools that stop at the first module whose tests fail, so a later module's tests are not reported:
# their run stops after a selected failure, as pytest's ``-x`` does (autocode_mutation_results).
STOPS_AFTER_FAILURE = frozenset({"maven", "gradle"})
# Directories a build tool writes its per-class reports into, and never walks into to find them.
REPORT_DIRS = {"maven": ("target", "surefire-reports"), "gradle": ("build", "test-results")}
SKIP = frozenset({".git", ".autocode", "node_modules", "vendor", ".venv", "venv", "src"})
WALK_LIMIT = 20_000
# Shell syntax a command may not hold, once the ``&&`` between the steps of a CMake suite is set aside.
_SHELL = re.compile(r"[;&|<>`$()\n\\]")


def _steps(command):
    """The command's ``&&`` steps as word lists, or None for any other shell syntax."""
    if not isinstance(command, str) or not command.strip() or _SHELL.search(command.replace("&&", "")):
        return None
    try:
        return [shlex.split(step) for step in command.split("&&")]
    except ValueError:
        return None


def runner(command) -> str | None:
    """``maven``, ``gradle``, ``phpunit`` or ``ctest`` for a command whose last step runs that runner."""
    steps = _steps(command)
    if not steps or not steps[-1]:
        return None
    words = steps[-1]
    name = PurePosixPath(words[0]).name
    if name == "php" and len(words) > 1:
        name = PurePosixPath(words[1]).name
    return {"mvn": "maven", "mvnw": "maven", "gradle": "gradle", "gradlew": "gradle"}.get(name) or (
        name if name in ("phpunit", "ctest") else None
    )


def _report_dirs(tree, kind):
    """Each ``target/surefire-reports`` or ``build/test-results`` directory below ``tree``."""
    parent, name = REPORT_DIRS[kind]
    seen = 0
    for directory, names, _ in os.walk(tree):
        seen += len(names)
        if seen > WALK_LIMIT:
            return
        if Path(directory).name == parent and name in names:
            yield Path(directory) / name
        names[:] = sorted(item for item in names if item not in SKIP and not item.startswith("."))


def prepare(command, tree, report) -> str | None:
    """The command that runs ``command`` and writes its JUnit results where ``results`` reads them, after
    removing what an earlier run wrote; None for a command of no runner here."""
    kind = runner(command)
    if kind is None:
        return None
    report = Path(report)
    report.unlink(missing_ok=True)
    if kind in REPORT_DIRS:
        for directory in list(_report_dirs(tree, kind)):
            if not directory.is_symlink():
                shutil.rmtree(directory)
        return command
    option = "--log-junit" if kind == "phpunit" else "--output-junit"
    return command + " " + shlex.join([option, str(report.resolve())])


def results(command, tree, report) -> dict | None:
    """The merged per-test results the run of ``prepare``'s command wrote, or None when it wrote none or
    a report is refused."""
    kind = runner(command)
    if kind is None:
        return None
    if kind in REPORT_DIRS:
        files = sorted(
            path
            for directory in _report_dirs(tree, kind)
            for path in directory.rglob("*.xml")
            if path.name.startswith("TEST-") and not path.is_symlink()
        )
    else:
        files = [Path(report)] if Path(report).is_file() else []
    if not files:
        return None
    merged: dict = {key: [] for key in ("passed", "failed", "skipped", "collection_errors", "uncollected")}
    try:
        for path in files:
            parsed = junit.results(path)
            for key in merged:
                merged[key] += parsed.get(key) or []
    except ValueError:
        return None
    names = [test for key in ("passed", "failed", "skipped") for test in merged[key]]
    if len(names) != len(set(names)):
        return None  # one test reported twice (two modules' classes of one name) is no attribution
    return {**{key: sorted(value) for key, value in merged.items()}, "total": len(names), "complete": True}
