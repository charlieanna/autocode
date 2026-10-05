"""AutoResolver calls in a saved run, counted by the rules of issue #59 (scenarios/README.md, "Diagnosis").

Reads only what the runner saved: state.json's stage rows, the report files they point to, and the code
checkpoints in the project's Git repository. Imports nothing from AutoCode.

A call counts when its row is ``astra_resolve``, not runner-owned, and was launched with a model
(``launch_route.model``, ``runner_calls`` >= 1). Report repairs, ``astra_diagnose`` and Investigator calls
are other stages and never count. A rejected report the runner then accepted after a report-only repair
(an ``astra_resolve_report_repair`` row at the same source) is one accepted call, scored on the repaired
report. A call is scorable when its report was saved and the runner applied it: a call still in
``active_stage`` when the run stopped, or one whose repair was still running, was never accepted or rejected.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

STAGE = "astra_resolve"
REPAIR = STAGE + "_report_repair"


def load_state(project: Path) -> tuple[dict, Path] | None:
    """The run's saved state and its run directory, or None when the run saved no state."""
    states = sorted((Path(project) / ".autocode" / "runs").glob("*/state.json"))
    if not states:
        return None
    return json.loads(states[-1].read_text()), states[-1].parent


def calls(state: dict, run_dir: Path) -> list[dict]:
    """Every counted Resolver call, in launch order. Each: ``row`` (the launched row), ``index`` and ``end``
    (the positions in ``stages`` of that row and of its last report repair; None for an unapplied call),
    ``revision`` (the source it saw), ``report`` (the report to score, or None), ``output`` (its path),
    ``applied`` (the runner saved its row), ``accepted``, ``report_repaired``, ``pending`` (why it cannot be
    judged, or ""), ``model`` and ``cost_usd`` (the call and its repairs)."""
    rows = [row for row in state.get("stages") or [] if isinstance(row, dict)]
    active = state.get("active_stage") if isinstance(state.get("active_stage"), dict) else None
    found = []
    for index, row in enumerate(rows):
        if not launched(row):
            continue
        revision_ = revision(row, run_dir)
        later = next((n for n in range(index + 1, len(rows)) if launched(rows[n])), len(rows))
        repairs = [n for n in range(index + 1, later) if _repairs(rows[n]) and rows[n].get("source_revision") == revision_]
        end, repairs = (repairs[-1] if repairs else index), [rows[n] for n in repairs]
        repaired = next((other for other in repairs if not other.get("rejected")), None) if row.get("rejected") else None
        in_flight = bool(row.get("rejected") and not repaired and active and _repairs(active)
                         and revision(active, run_dir) == revision_ and later == len(rows))
        scored = repaired or row
        found.append({
            "row": row, "index": index, "end": end, "revision": revision_, "output": scored.get("output"),
            "report": _dict(read(local(scored.get("output"), run_dir))), "applied": True,
            "accepted": not row.get("rejected") or repaired is not None, "report_repaired": repaired is not None,
            "pending": "its report repair was still running when the run stopped" if in_flight else "",
            "model": (row.get("launch_route") or {}).get("model"), "cost_usd": _cost([row, *repairs])})
    if active and launched(active) and active not in rows:
        found.append({"row": active, "index": None, "end": None, "revision": revision(active, run_dir),
                      "output": active.get("output"),
                      "report": _dict(read(local(active.get("output"), run_dir))), "applied": False,
                      "accepted": False, "report_repaired": False,
                      "pending": "the run stopped before the runner applied it",
                      "model": (active.get("launch_route") or {}).get("model"), "cost_usd": _cost([active])})
    return found


def launched(row: dict) -> bool:
    return (row.get("stage") == STAGE and not row.get("runner_owned")
            and bool((row.get("launch_route") or {}).get("model"))
            and isinstance(row.get("runner_calls"), int) and row["runner_calls"] >= 1)


def _repairs(row: dict) -> bool:
    return row.get("stage") == REPAIR or (bool(row.get("report_only")) and row.get("original_stage") == STAGE)


def revision(row: dict, run_dir: Path) -> str | None:
    """The source a call saw; an unfinished call has only its launch snapshot."""
    before = read(local(row.get("before_ref"), run_dir))
    return (row.get("source_revision") or (row.get("capture_context") or {}).get("source_revision")
            or (before.get("revision") if isinstance(before, dict) else None))


def _cost(rows: list[dict]):
    costs = [(row.get("metrics") or {}).get("provider_cost_usd") for row in rows]
    costs = [cost for cost in costs if isinstance(cost, (int, float))]
    return round(sum(costs), 4) if costs else None


def _dict(value):
    return value if isinstance(value, dict) else None


def local(path, run_dir: Path) -> Path | None:
    """A saved path, or the same file under ``run_dir`` when the evidence was copied elsewhere."""
    if not isinstance(path, str) or not path:
        return None
    candidate = Path(path)
    if candidate.exists():
        return candidate
    parts = candidate.parts
    for index in range(len(parts) - 2):
        if parts[index:index + 2] == (".autocode", "runs"):
            return Path(run_dir).joinpath(*parts[index + 3:])
    return candidate


def read(path):
    try:
        return json.loads(Path(path).read_text()) if path else None
    except (OSError, ValueError, TypeError):
        return None


def checkout(project: Path, state: dict, run_dir: Path, revision_: str, target: Path) -> str:
    """Write the project's tracked and untracked source as it was at ``revision_`` into ``target``.

    From the runner's code checkpoint for that source (a Git commit it verified), else from a stage row
    at that source: its snapshot's HEAD plus its saved ``git diff HEAD``, kept only when every file's
    sha256 matches the snapshot. Returns how ("code checkpoint" or "stage diff"), or "" when neither works.
    """
    for row in state.get("code_checkpoints") or []:
        if not (isinstance(row, dict) and row.get("source_revision") == revision_ and row.get("available")
                and row.get("commit")):
            continue
        _clear(target)
        if _extract(project, row["commit"], target):
            return "code checkpoint"
    for row in state.get("stages") or []:
        if not isinstance(row, dict) or row.get("source_revision") != revision_:
            continue
        after, diff = read(local(row.get("after_ref"), run_dir)), local(row.get("diff_ref"), run_dir)
        if not isinstance(after, dict) or not after.get("head") or not diff or not diff.is_file():
            continue
        _clear(target)
        if not _extract(project, after["head"], target):
            continue
        if diff.stat().st_size and _git(target, "apply", "--whitespace=nowarn", str(diff.resolve())) is None:
            continue
        if _matches(target, after.get("files") or {}):
            return "stage diff"
    _clear(target)
    return ""


def _extract(project: Path, commit: str, target: Path) -> bool:
    data = _git(project, "archive", "--format=tar", commit)
    if data is None:
        return False
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        if hasattr(tarfile, "data_filter"):
            archive.extractall(target, filter="data")
        else:  # Python before 3.11.4; the archive is the project's own commit
            archive.extractall(target)
    return True


def _matches(target: Path, files: dict) -> bool:
    for name, value in files.items():
        path = target / name
        if value == "deleted":
            if path.exists():
                return False
            continue
        if not isinstance(value, str) or value.startswith(("symlink:", "submodule:", "uninitialized")):
            continue
        executable = value.startswith("executable:")
        if not path.is_file() or (bool(path.stat().st_mode & 0o111) != executable
                                  or hashlib.sha256(path.read_bytes()).hexdigest() != value.removeprefix("executable:")):
            return False
    return True


def _clear(target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)


def _git(cwd: Path, *args: str) -> bytes | None:
    # GIT_CEILING_DIRECTORIES keeps `git apply` in a scratch directory from finding a repository above it.
    env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(Path(cwd).resolve().parent)}
    try:
        proc = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=cwd, capture_output=True,
                              env=env, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None
