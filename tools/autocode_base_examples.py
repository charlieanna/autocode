"""Advisory base-revision check for planned exact-output examples (#676).

A criterion of the form "when `CMD` runs, ... writes exactly `OUT` to stdout" states what the
command prints. For a feature on an existing command, the parts the brief says must not change
should print the same on the base revision. This module runs such a command against a copy of
the workspace's HEAD (extracted with ``git archive``, never in the workspace) and reports where
the planned output differs from what the base prints. The notes are advisory: they never block
approval and never change the contract. A new behavior legitimately differs from the base.

State: ``state["goal_base_check"]`` = {"examples": digest of the examples checked, "notes": [...]}.
Written only by ``check`` (called from the plan display); read only by ``notes`` (the plan
rendering, which runs no command). Imports nothing from AutoCode except the draft-example
spelling helper, which imports nothing from AutoCode.
"""

from __future__ import annotations

import difflib
import hashlib
import io
import json
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

try:
    from . import autocode_draft_examples as examples
except ImportError:
    import autocode_draft_examples as examples

WHEN_COMMAND = re.compile(r"\bwhen\s+`([^`\n]+)`\s+runs\b")
EXACT_STDOUT = re.compile(r"\bwrites exactly\s+`([^`\n]+)`\s+to stdout")
TIMEOUT_SECONDS = 60


def planned(body) -> list[dict]:
    """The criteria that name a command and the exact stdout it must write."""
    rows = []
    for row in (body or {}).get("acceptance_criteria", []):
        text = str(row.get("criterion") or "")
        when, exact = WHEN_COMMAND.search(text), EXACT_STDOUT.search(text)
        if when and exact:
            rows.append({"id": row.get("id"), "command": when.group(1), "expected": examples.spelling(exact.group(1))})
    return rows


def _digest(rows) -> str:
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def base_copy(workspace: Path):
    """A temporary copy of the workspace's HEAD, and that revision; (None, None) when not a git checkout."""
    try:
        head = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "--verify", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        ).stdout.strip()
        archive = subprocess.run(
            ["git", "-C", str(workspace), "archive", head], capture_output=True, timeout=120, check=True
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None, None
    root = Path(tempfile.mkdtemp(prefix="autocode-base-"))
    try:
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(root)
    except (OSError, tarfile.TarError):
        shutil.rmtree(root, ignore_errors=True)
        return None, None
    return root, head


def base_output(root: Path, command: str):
    """(exit code, stdout) of the command on the base copy; (None, '') when it cannot run."""
    try:
        argv = shlex.split(command)
        done = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, ""
    return done.returncode, done.stdout


def differences(expected: str, base: str) -> list[str]:
    """The changed lines between the base output and the planned output, as unified-diff lines."""
    return [
        line
        for line in difflib.unified_diff(base.splitlines(), expected.splitlines(), "base", "plan", lineterm="", n=0)
        if line[:1] in "+-" and line[:3] not in ("+++", "---")
    ]


def check(state) -> dict:
    """Run the planned commands on the base revision and record the advisory notes (plan display only)."""
    rows = planned((state.get("goal_contract") or {}).get("body"))
    digest = _digest(rows)
    recorded = state.get("goal_base_check") or {}
    if recorded.get("examples") == digest:
        return recorded  # the same planned examples were already compared; the base does not change within a run
    if not rows:
        result = {"examples": digest, "notes": []}
    else:
        root, head = base_copy(Path(state.get("workspace") or "."))
        notes = []
        if root is None:
            notes.append("Base-revision check: the workspace is not a git checkout, so no base output was compared.")
        else:
            try:
                for row in rows:
                    code, base = base_output(root, row["command"])
                    if code != 0:
                        continue  # the command does not exist on the base revision (a new behavior)
                    changed = differences(row["expected"], base)
                    if changed:
                        notes.append(
                            f"  - {row['id']}: the base revision ({head[:8]}) prints differently from this "
                            f"example: " + " | ".join(changed)
                        )
            finally:
                shutil.rmtree(root, ignore_errors=True)
        result = {"examples": digest, "notes": notes}
    state["goal_base_check"] = result
    return result


def notes(state) -> list[str]:
    """The notes recorded for the contract currently in state; empty when none were recorded for it."""
    recorded = state.get("goal_base_check") or {}
    if recorded.get("examples") != _digest(planned((state.get("goal_contract") or {}).get("body"))):
        return []
    lines = recorded.get("notes") or []
    if not lines:
        return []
    return ["", "Base-revision check (advisory; a new behavior legitimately differs from the base):", *lines]
