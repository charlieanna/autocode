"""Compare a project command's diagnostics on the run's base revision and on the candidate (#3947).

A single run of a build shows what the candidate prints, not what the change added: in #3947 the
project's normal ``sphinx-build -W`` passed while ``sphinx-build -n`` on base and candidate showed
two new missing-reference warnings. ``autocode capture --compare-base REV -- COMMAND`` runs COMMAND
in a copy of REV extracted with ``git archive`` under the workspace's ``.autocode/scratch`` (never in
the workspace itself, as autocode_base_examples does) and then in the workspace, and records the
diagnostic lines the candidate prints that the base does not.

A diagnostic line is an output line containing a whole-word ``warning``, ``error`` or ``fatal`` in any
case: a vocabulary of diagnostics, not of any one tool. Lines are compared as multisets after the base
copy's root path is replaced with the workspace path and location line/column numbers are normalized,
so a moved diagnostic is not new but numbers in its message remain significant. The receipt is read by
autocode_claim_evidence. The shared captured-process owner bounds each exact
argv and retains process-family cleanup across Linux metadata invocations.
"""

from __future__ import annotations

import collections
import io
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

try:
    from . import autocode_captured_process as captured_process
    from . import autocode_util as util
except ImportError:
    import autocode_captured_process as captured_process
    import autocode_util as util

KIND = "diagnostics_comparison"
COMPARISON_TIMEOUT = 900
_DIAGNOSTIC = re.compile(r"\b(?:warning|error|fatal)\b", re.IGNORECASE)
_LOCATION = re.compile(r"^(.+?):\d+(?::\d+)?:")


def _diagnostic_key(line: str) -> str:
    """Ignore a conventional file location, never digits in the diagnostic itself."""
    match = _LOCATION.match(line)
    if match and not _DIAGNOSTIC.search(match.group(1)):
        return f"{match.group(1)}:#{line[match.end() - 1 :]}"
    return line


def diagnostics(text: str) -> list[str]:
    """The output lines that report a diagnostic, in order."""
    return [line.rstrip() for line in text.splitlines() if _DIAGNOSTIC.search(line)]


def new_lines(base: list[str], candidate: list[str]) -> list[str]:
    """Candidate diagnostic lines the base did not print, with only source locations normalized."""
    remaining = collections.Counter(_diagnostic_key(line) for line in base)
    added = []
    for line in candidate:
        key = _diagnostic_key(line)
        if remaining[key] > 0:
            remaining[key] -= 1
        else:
            added.append(line)
    return added


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise ValueError(f"git {' '.join(args)} failed: {result.stderr.strip() or result.returncode}")
    return result.stdout.strip()


def _extract(top: Path, revision: str, destination: Path) -> None:
    archive = subprocess.run(["git", "-C", str(top), "archive", revision], capture_output=True, timeout=300)
    if archive.returncode != 0:
        raise ValueError(f"git archive {revision} failed: {archive.stderr.decode(errors='replace').strip()}")
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(destination, filter="data")


def _run(command: list[str], cwd: Path, log: Path, deadline: float) -> int:
    """Retain combined output while the shared capture owner bounds the command family."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired(command, COMPARISON_TIMEOUT)
    with log.open("x+b") as output:
        result = captured_process.run_metadata(
            command, cwd=cwd, timeout=remaining, text=False, output_stream=output, merge_output=True
        )
    return result.returncode


def compare(command: list[str], revision: str, cwd: Path, receipt_path: Path) -> dict:
    """Run ``command`` on ``revision`` and on the candidate in ``cwd``; return the comparison receipt.

    Both outputs are retained beside ``receipt_path`` (``.base.log`` and ``.log``); the base copy is
    removed before returning, whatever happened.
    """
    cwd = Path(cwd).resolve()
    top = Path(_git(cwd, "rev-parse", "--show-toplevel")).resolve()
    base_revision = _git(cwd, "rev-parse", "--verify", f"{revision}^{{commit}}")
    base_log = receipt_path.with_suffix(".base.log").absolute()
    candidate_log = receipt_path.with_suffix(".log").absolute()
    if base_log.exists() or base_log.is_symlink() or candidate_log.exists() or candidate_log.is_symlink():
        raise FileExistsError("diagnostic comparison logs already exist")
    deadline = time.monotonic() + COMPARISON_TIMEOUT
    scratch = top / ".autocode" / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    copy = Path(tempfile.mkdtemp(prefix="compare-base-", dir=scratch))
    try:
        _extract(top, base_revision, copy)
        base_cwd = copy / cwd.relative_to(top)
        base_cwd.mkdir(parents=True, exist_ok=True)
        base_exit = _run(command, base_cwd, base_log, deadline)
    finally:
        shutil.rmtree(copy, ignore_errors=True)
    candidate_exit = _run(command, cwd, candidate_log, deadline)
    # The base copy's own root reads as the workspace's, so an absolute path in a diagnostic is not new.
    base_text = base_log.read_bytes().decode("utf-8", errors="replace").replace(str(copy), str(top))
    candidate_text = candidate_log.read_bytes().decode("utf-8", errors="replace")
    base_lines = diagnostics(base_text)
    return {
        "kind": KIND,
        "command": command,
        "command_text": shlex.join(command),
        "base_revision": base_revision,
        "base_exit_code": base_exit,
        "candidate_exit_code": candidate_exit,
        "base_output": str(base_log),
        "base_output_sha256": util.file_hash(base_log),
        "full_output": str(candidate_log),
        "full_output_sha256": util.file_hash(candidate_log),
        "base_diagnostics": len(base_lines),
        "candidate_diagnostics": len(diagnostics(candidate_text)),
        "new_diagnostics": new_lines(base_lines, diagnostics(candidate_text)),
    }
