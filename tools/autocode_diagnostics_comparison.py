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
autocode_claim_evidence. Imports nothing from AutoCode except autocode_util.
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
from pathlib import Path

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util

KIND = "diagnostics_comparison"
_DIAGNOSTIC = re.compile(r"\b(?:warning|error|fatal)\b", re.IGNORECASE)
_LOCATION = re.compile(r"^(.+?):\d+(?::\d+)?:")


def _diagnostic_key(line: str) -> str:
    """Ignore a conventional file location, never digits in the diagnostic itself."""
    return _LOCATION.sub(r"\1:#:", line, count=1)


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


def _run(command: list[str], cwd: Path, log: Path) -> int:
    with log.open("xb") as handle:
        return subprocess.run(command, cwd=cwd, stdout=handle, stderr=subprocess.STDOUT).returncode


def compare(command: list[str], revision: str, cwd: Path, receipt_path: Path) -> dict:
    """Run ``command`` on ``revision`` and on the candidate in ``cwd``; return the comparison receipt.

    Both outputs are retained beside ``receipt_path`` (``.base.log`` and ``.log``); the base copy is
    removed before returning, whatever happened.
    """
    cwd = Path(cwd).resolve()
    top = Path(_git(cwd, "rev-parse", "--show-toplevel")).resolve()
    base_revision = _git(cwd, "rev-parse", "--verify", f"{revision}^{{commit}}")
    scratch = top / ".autocode" / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    copy = Path(tempfile.mkdtemp(prefix="compare-base-", dir=scratch))
    base_log, candidate_log = receipt_path.with_suffix(".base.log"), receipt_path.with_suffix(".log")
    try:
        _extract(top, base_revision, copy)
        base_cwd = copy / cwd.relative_to(top)
        base_cwd.mkdir(parents=True, exist_ok=True)
        base_exit = _run(command, base_cwd, base_log)
    finally:
        shutil.rmtree(copy, ignore_errors=True)
    candidate_exit = _run(command, cwd, candidate_log)
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
