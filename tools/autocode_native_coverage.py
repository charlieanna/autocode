"""Coverage from a project's own non-Python toolchain: gcov, JaCoCo, PHPUnit and coverlet.

Each is used only where the project shows that it already has the tool, never installed or configured by
AutoCode (AGENTS.md, "AutoCode delivers no untested code"):

* **gcov**: the proof's own run compiled the code with coverage, so the tree holds ``.gcno`` notes; gcovr
  (or lcov) turns the next run's counts into a report. Whatever the runner (CMake, Make, a shell script).
* **JaCoCo**: the proof's own Maven or Gradle run left JaCoCo's execution data at the project root,
  because the build applies its agent; the build's own report task writes the XML.
* **PHPUnit**: a PHPUnit command, where PHP has a coverage driver (pcov or Xdebug) loaded.
* **coverlet**: a ``dotnet test`` command, where a test project references ``coverlet.collector``.

``prepare`` returns the command to run, the command that writes the report into the proof's evidence
(or None) and the report's path, as ``autocode_diff_coverage.prepare`` does; the report is read by
``autocode_coverage_reports``, which recognizes the format from its bytes. It removes the data an earlier
run left (``.gcda`` counts, a JaCoCo ``.exec``), so the report can only describe the run that follows.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path, PurePosixPath

# Where the walk for gcov notes and .NET projects never goes: metadata, AutoCode's own files, installed code.
SKIP = frozenset({".git", ".autocode", "node_modules", "vendor", ".venv", "venv"})
WALK_LIMIT = 20_000
SHELL = re.compile(r"[;&|<>`$()\n\\]")
# A JaCoCo agent's data and the report its build tool writes, beside the build file at the command's root.
JACOCO = {
    "maven": ("target/jacoco.exec", "jacoco:report", "target/site/jacoco/jacoco.xml"),
    "gradle": ("build/jacoco/test.exec", "jacocoTestReport", "build/reports/jacoco/test/jacocoTestReport.xml"),
}
_BUILD_TOOLS = {"mvn": "maven", "mvnw": "maven", "gradle": "gradle", "gradlew": "gradle"}
PHP_DRIVER = "exit(extension_loaded('pcov') || extension_loaded('xdebug') ? 0 : 1);"


def _files(tree, keep):
    """Paths below ``tree`` whose name ``keep`` accepts, at most WALK_LIMIT files looked at."""
    seen = 0
    for directory, names, files in os.walk(tree):
        names[:] = sorted(name for name in names if name not in SKIP)
        for name in files:
            seen += 1
            if seen > WALK_LIMIT:
                return
            if keep(name):
                yield Path(directory) / name


def _words(command):
    if not command or SHELL.search(command):
        return None
    try:
        return shlex.split(command)
    except ValueError:
        return None


def _probe(command, tree) -> bool:
    try:
        return subprocess.run(command, cwd=tree, capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def prepare(command, tree, evidence_dir) -> dict | None:
    """The spec (tool, command, finalize, report) for the first of the tools above this project has, or None."""
    for found in (_jacoco, _phpunit, _coverlet, _gcov):
        spec = found(command, Path(tree), Path(evidence_dir))
        if spec is not None:
            return spec
    return None


def _jacoco(command, tree, evidence):
    words = _words(command)
    tool = _BUILD_TOOLS.get(PurePosixPath(words[0]).name) if words else None
    if tool is None:
        return None
    data, task, written = JACOCO[tool]
    if not (tree / data).is_file() or (tree / data).is_symlink():
        return None
    (tree / data).unlink()
    evidence.mkdir(parents=True, exist_ok=True)
    report = evidence / "jacoco.xml"
    options = ["-B", "-q"] if tool == "maven" else ["--no-daemon", "-q"]
    finalize = (
        shlex.join([words[0], *options, task]) + " && cp " + shlex.quote(written) + " " + shlex.quote(str(report))
    )
    return {"tool": "jacoco", "command": command, "finalize": finalize, "report": report}


def _phpunit(command, tree, evidence):
    words = _words(command)
    if not words:
        return None
    runner = words[1] if PurePosixPath(words[0]).name == "php" and len(words) > 1 else words[0]
    if PurePosixPath(runner).name != "phpunit" or not _probe(["php", "-r", PHP_DRIVER], tree):
        return None
    evidence.mkdir(parents=True, exist_ok=True)
    report = evidence / "cobertura.xml"
    # Xdebug 3 measures coverage only in its coverage mode; pcov ignores the variable.
    wrapped = "XDEBUG_MODE=coverage " + shlex.join([*words, "--coverage-cobertura", str(report)])
    return {"tool": "phpunit", "command": wrapped, "finalize": None, "report": report}


def _coverlet(command, tree, evidence):
    words = _words(command)
    if not words or words[:2] != ["dotnet", "test"]:
        return None
    projects = _files(tree, lambda name: name.endswith((".csproj", ".fsproj", ".vbproj")))
    if not any("coverlet.collector" in path.read_text(errors="replace") for path in projects):
        return None
    results = evidence / "coverlet"
    shutil.rmtree(results, ignore_errors=True)
    results.mkdir(parents=True)
    report = evidence / "coverage.cobertura.xml"
    wrapped = shlex.join([*words, "--collect", "XPlat Code Coverage", "--results-directory", str(results)])
    # The collector writes into a directory named by a new GUID; exactly one test project's report is read.
    finalize = f'cp "$(find {shlex.quote(str(results))} -name coverage.cobertura.xml)" {shlex.quote(str(report))}'
    return {"tool": "coverlet", "command": wrapped, "finalize": finalize, "report": report}


def _gcov(command, tree, evidence):
    if not command or not any(_files(tree, lambda name: name.endswith(".gcno"))):
        return None
    report = evidence / "gcov-report"
    if _probe(["gcovr", "--version"], tree):
        tool, finalize = "gcovr", shlex.join(["gcovr", "--root", ".", "--xml", "--output", str(report)])
    elif _probe(["lcov", "--version"], tree):
        tool, finalize = (
            "lcov",
            shlex.join(["lcov", "--quiet", "--capture", "--directory", ".", "--output-file", str(report)]),
        )
    else:
        return None
    for counts in list(_files(tree, lambda name: name.endswith(".gcda"))):
        counts.unlink()
    evidence.mkdir(parents=True, exist_ok=True)
    return {"tool": tool, "command": command, "finalize": finalize, "report": report}
