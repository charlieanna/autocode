"""Line coverage of the proving tests, intersected with the change's product hunks (#701).

Collected by running the proving-test command once more, with the project's own coverage
tool, in the proof's scratch tree. Report only: a missing tool, an unmeasured file or an
uncovered hunk never changes the verdict. Nothing is installed into the project. Imports
nothing from the runner; the caller runs the commands and passes the diff.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
from pathlib import Path

try:
    from . import autocode_python_tests as python_tests
except ImportError:
    import autocode_python_tests as python_tests

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
SKIP_NOTE = "no coverage tool is installed in the project; diff coverage was not collected"


def skipped(note=SKIP_NOTE) -> dict:
    return {"collected": False, "tool": None, "note": note, "files": [], "uncovered": [], "unmeasured": []}


# Exactly the changed lines, whatever the person's git config says: no hunks merged across unchanged
# lines (diff.interHunkContext), no color codes before a hunk header, no external or textconv driver,
# and the a/ b/ prefixes hunks() reads.
DIFF_OPTIONS = (
    "--unified=0",
    "--inter-hunk-context=0",
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "--src-prefix=a/",
    "--dst-prefix=b/",
)


def product_diff(workspace, base, files, *, env=None) -> str:
    """Zero-context unified diff of product files. ``files`` is ``(relative path, status)``. ``env`` is
    Git's environment, for an index other than the checkout's own (autocode_verify.product_diff)."""
    chunks = []
    root = str(workspace)
    # GIT_DIFF_OPTS (--unified=N) would override the zero context above.
    environment = {
        name: value for name, value in (os.environ if env is None else env).items() if name != "GIT_DIFF_OPTS"
    }
    for path, status in files:
        if status == "added":
            command = ["git", "-C", root, "diff", "--no-index", *DIFF_OPTIONS, "--", os.devnull, path]
        else:
            command = ["git", "-C", root, "diff", *DIFF_OPTIONS, base, "--", path]
        chunks.append(subprocess.run(command, capture_output=True, text=True, env=environment).stdout)
    prefix = str(Path(workspace).resolve()) + "/"
    return "\n".join(chunk.replace(prefix, "") for chunk in chunks if chunk)


def hunks(diff_text) -> list[dict]:
    """Added or replaced line ranges in the new file. A deletion has nothing to execute."""
    files: list[dict] = []
    current: dict | None = None
    for line in (diff_text or "").splitlines():
        if line.startswith("diff --git "):
            current = {"path": line.split(" b/", 1)[-1], "hunks": []}
            files.append(current)
        elif line.startswith("+++ ") and current is not None:
            name = line[4:].strip()
            if name != "/dev/null":
                current["path"] = name[2:] if name.startswith("b/") else name
        elif current is not None:
            match = HUNK.match(line)
            if match:
                start = int(match.group(1))
                count = int(match.group(2) if match.group(2) is not None else "1")
                if count:
                    current["hunks"].append({"start": start, "end": start + count - 1})
    return [item for item in files if item["hunks"]]


def intersect(file_hunks, executed, missing) -> dict:
    """A hunk is uncovered when one of its statements did not run.

    Comments and blank lines are not statements, so a hunk with none is covered. A changed
    file the report does not mention was never measured: its hunks are ``unmeasured``, never
    covered. ``executed`` and ``missing`` map a relative path to line numbers.
    """
    files, uncovered, unmeasured = [], [], []
    for item in file_hunks:
        path = item["path"]
        if path not in executed and path not in missing:
            unmeasured.append(path)
            files.append({"path": path, "hunks": [{**hunk, "covered": None} for hunk in item["hunks"]]})
            continue
        ran = set(executed.get(path) or ())
        gap = set(missing.get(path) or ()) - ran
        reported = []
        for hunk in item["hunks"]:
            missed = [number for number in range(hunk["start"], hunk["end"] + 1) if number in gap]
            reported.append({**hunk, "covered": not missed, "uncovered_lines": missed})
            if missed:
                uncovered.append({"path": path, **hunk, "uncovered_lines": missed})
        files.append({"path": path, "hunks": reported})
    return {"files": files, "uncovered": uncovered, "unmeasured": unmeasured}


def _probe(command, tree) -> bool:
    try:
        return subprocess.run(command, cwd=tree, capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def prepare(framework, command, tree, evidence_dir) -> dict | None:
    """How to collect coverage for this proving-test command, or None when the project has no tool.

    The returned command is that proving-test command with the tool's flag or wrapper. The caller
    runs it in addition to the judged runs; its exit code is not a verdict.
    """
    if not framework or not command:
        return None
    evidence_dir = Path(evidence_dir)
    name = framework.name
    if name in ("unittest", "pytest"):
        invocation = python_tests.parse(command)
        if invocation is None or invocation.kind not in ("unittest", "pytest"):
            return None
        if not _probe([invocation.python, "-c", "import coverage"], tree):
            return None
        evidence_dir.mkdir(parents=True, exist_ok=True)
        data, report = evidence_dir / ".coverage", evidence_dir / "coverage.json"
        wrapped = shlex.join(
            [
                *invocation.prefix,
                invocation.python,
                "-m",
                "coverage",
                "run",
                f"--data-file={data}",
                "--source=.",
                "-m",
                invocation.kind,
                *invocation.arguments,
            ]
        )
        finalize = shlex.join([invocation.python, "-m", "coverage", "json", f"--data-file={data}", "-o", str(report)])
        return {"tool": "coverage.py", "command": wrapped, "finalize": finalize, "report": report}
    if name == "go" and command.startswith("go test ") and not re.search(r"[;&|<>`$()\n]", command):
        evidence_dir.mkdir(parents=True, exist_ok=True)
        report = evidence_dir / "cover.out"
        wrapped = "go test -coverprofile=" + shlex.quote(str(report)) + " " + command[len("go test ") :]
        return {"tool": "go coverprofile", "command": wrapped, "finalize": None, "report": report}
    if name == "node" and command.startswith("node --test"):
        if not _probe(["npx", "--no-install", "c8", "--version"], tree):
            return None
        evidence_dir.mkdir(parents=True, exist_ok=True)
        report_dir = evidence_dir / "c8"
        wrapped = f"npx --no-install c8 --reporter=json --reports-dir={shlex.quote(str(report_dir))} {command}"
        return {"tool": "c8", "command": wrapped, "finalize": None, "report": report_dir / "coverage-final.json"}
    if name == "vitest" and "vitest" in command:
        packages = ("@vitest/coverage-v8", "@vitest/coverage-istanbul")
        if not any((Path(tree) / "node_modules" / package).exists() for package in packages):
            return None
        evidence_dir.mkdir(parents=True, exist_ok=True)
        report_dir = evidence_dir / "vitest"
        wrapped = (
            f"{command} --coverage --coverage.reporter=json --coverage.reportsDirectory={shlex.quote(str(report_dir))}"
        )
        return {"tool": "vitest", "command": wrapped, "finalize": None, "report": report_dir / "coverage-final.json"}
    return None


def _relative(path, root) -> str:
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except (OSError, ValueError):
        return str(path).replace("\\", "/")


def parse_coverage_json(payload, root) -> tuple[dict, dict]:
    executed, missing = {}, {}
    for path, info in (payload.get("files") or {}).items():
        if isinstance(info, dict):
            relative = _relative(Path(root) / path, root)
            executed[relative] = set(info.get("executed_lines") or [])
            missing[relative] = set(info.get("missing_lines") or [])
    return executed, missing


def go_module(root) -> str | None:
    """The module path go.mod declares; a cover profile names files by it, not by the checkout path."""
    try:
        text = (Path(root) / "go.mod").read_text(errors="replace")
    except OSError:
        return None
    match = re.search(r"^module\s+(\S+)", text, re.MULTILINE)
    return match.group(1).strip('"') if match else None


def parse_go_cover(text, root=None) -> tuple[dict, dict]:
    """``file:startLine.col,endLine.col statements count`` blocks, with files made checkout-relative."""
    module = go_module(root) if root else None
    executed: dict[str, set] = {}
    missing: dict[str, set] = {}
    for line in text.splitlines():
        match = re.match(r"(.+):(\d+)\.\d+,(\d+)\.\d+ \d+ (\d+)$", line)
        if not match:
            continue
        path = match.group(1)
        if module and path.startswith(module + "/"):
            path = path[len(module) + 1 :]
        bucket = executed if int(match.group(4)) else missing
        bucket.setdefault(path, set()).update(range(int(match.group(2)), int(match.group(3)) + 1))
    for path in set(executed) | set(missing):
        missing[path] = missing.get(path, set()) - executed.get(path, set())
    return executed, missing


def parse_istanbul(payload, root) -> tuple[dict, dict]:
    """Istanbul ``coverage-final.json`` (c8, Vitest): statement spans and their hit counts."""
    executed, missing = {}, {}
    for path, info in (payload or {}).items():
        if not isinstance(info, dict):
            continue
        statements, counts = info.get("statementMap") or {}, info.get("s") or {}
        ran: set[int] = set()
        gap: set[int] = set()
        for key, count in counts.items():
            span = statements.get(key) or {}
            start = (span.get("start") or {}).get("line")
            end = (span.get("end") or {}).get("line")
            if start:
                (ran if count else gap).update(range(int(start), int(end or start) + 1))
        relative = _relative(path, root)
        executed[relative], missing[relative] = ran, gap - ran
    return executed, missing


def collect(framework, command, tree, evidence_dir, diff_text, run) -> dict:
    """Run the proving-test ``command`` once more under the project's coverage tool and report the
    changed product hunks it did not execute. ``diff_text()`` gives the product diff; ``run(command,
    log)`` runs a command in ``tree`` and returns its receipt. Without a tool this costs no test run."""
    text = diff_text()
    if not text:
        return skipped("the change has no product code hunks; diff coverage was not collected")
    spec = prepare(framework, command, tree, evidence_dir)
    if spec is None:
        return skipped()
    report_path = Path(spec["report"])
    # Each proof owns its directory. Never accept a pre-existing or redirected
    # report as the output of this invocation.
    if report_path.is_symlink():
        return skipped("the coverage report is a symlink; no measurement was accepted")
    report_path.unlink(missing_ok=True)
    receipts = [run(spec["command"], Path(evidence_dir) / "run.log")]
    if spec["finalize"]:
        receipts.append(run(spec["finalize"], Path(evidence_dir) / "report.log"))
    result = finish(spec["tool"], report_path, tree, text)
    result.update(
        command_receipts=receipts,
        command_outputs=[row.get("output") for row in receipts],
        report_path=str(report_path),
        report_fresh=True,
        source_root=str(Path(tree).resolve()),
    )
    if report_path.is_file() and not report_path.is_symlink():
        result["report_sha256"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
    return result


def finish(tool, report_path, tree, diff_text) -> dict:
    """Read a coverage report and intersect it with ``diff_text``. A missing report is a note, not a failure."""
    path = Path(report_path)
    if not path.is_file():
        return skipped(f"{tool} ran but wrote no report; diff coverage was not collected")
    try:
        if tool == "go coverprofile":
            executed, missing = parse_go_cover(path.read_text(errors="replace"), tree)
        elif tool in ("c8", "vitest"):
            executed, missing = parse_istanbul(json.loads(path.read_text()), tree)
        else:
            executed, missing = parse_coverage_json(json.loads(path.read_text()), tree)
    except (OSError, ValueError, TypeError, AttributeError):
        return skipped(f"the {tool} report could not be read; diff coverage was not collected")
    return {
        "collected": True,
        "tool": tool,
        "note": None,
        "measured_lines": {
            name: {"executed": sorted(executed.get(name, ())), "missing": sorted(missing.get(name, ()))}
            for name in sorted(executed.keys() | missing.keys())
        },
        **intersect(hunks(diff_text), executed, missing),
    }
