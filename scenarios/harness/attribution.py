"""Operator annotations beside immutable scenario results, validated through a public CLI.

The harness never imports the runtime. An annotation is evidence about a saved
failure, not another oracle verdict or a replacement for the original result.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from . import attempts

VALIDATOR = Path(__file__).resolve().parents[2] / "tools" / "autocode_failure_attribution.py"
ANNOTATIONS = "failure-attributions"
NAME = re.compile(r"[0-9]{20}-[0-9a-f]{32}-([0-9a-f]{64})\.json\Z")


def unknown() -> dict:
    """Initial and legacy results contain no inferred diagnosis."""
    return {
        "evidence_refs": [],
        "suspected_cause": None,
        "confirmed_diagnosis": None,
        "regression": None,
        "confirmed_by": None,
    }


def _bytes(path: Path, *, bounded=False) -> bytes:
    with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or (bounded and info.st_size > attempts.MAX_RECORD_BYTES):
            raise ValueError("Attribution evidence must be a bounded regular file")
        return handle.read(attempts.MAX_RECORD_BYTES + 1) if bounded else handle.read()


def _result(root: Path) -> tuple[bytes, dict]:
    raw = _bytes(root / "result.json")
    value = json.loads(raw)
    if not isinstance(value, dict) or not value.get("scenario") or not value.get("verdict"):
        raise ValueError("Annotation requires a saved final scenario or baseline result")
    return raw, value


def validate(report: Path, root: Path, reviewer: str | None = None) -> dict:
    """Use the runtime's standalone public validator; no model or AutoCode run is launched."""
    argv = [sys.executable, str(VALIDATOR), str(report), "--evidence-root", str(root)]
    if reviewer is not None:
        argv += ["--reviewer", reviewer]
    completed = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    if completed.returncode:
        raise ValueError(completed.stderr.strip() or "Invalid failure attribution")
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise ValueError("Attribution validator did not return an object")
    return value


def append(root: Path, report: Path, *, reviewer: str | None = None) -> Path:
    """Append a validated annotation pinned to the original result's exact bytes."""
    root = Path(root).resolve(strict=True)
    original, _ = _result(root)
    attribution = validate(Path(report), root, reviewer)
    if _bytes(root / "result.json") != original:
        raise ValueError("Saved result changed while annotating")
    directory = root / ANNOTATIONS
    directory.mkdir(exist_ok=True)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Attribution directory must not be redirected")
    raw = (
        json.dumps(
            {
                "schema": 1,
                "result_sha256": hashlib.sha256(original).hexdigest(),
                "failure_attribution": attribution,
            },
            indent=2,
        )
        + "\n"
    ).encode()
    if len(raw) > attempts.MAX_RECORD_BYTES:
        raise ValueError("Failure attribution exceeded its bound")
    # Order explicit appends by the stored sequence even when the wall clock moves backward.
    prefixes = [int(path.name[:20]) for path in directory.glob("*.json") if NAME.fullmatch(path.name)]
    sequence = max(time.time_ns(), max(prefixes, default=-1) + 1)
    path = directory / f"{sequence:020d}-{uuid.uuid4().hex}-{hashlib.sha256(raw).hexdigest()}.json"
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return path


def project(root: Path, result: dict) -> dict:
    """Project an eligible latest annotation; invalid or stale evidence stays unknown.

    A damaged latest append does not revive an older confirmation. Projection
    never rewrites the saved result, its verdict, or its oracle diagnosis score.
    """
    projected = {**result, "failure_attribution": unknown()}
    directory = Path(root) / ANNOTATIONS
    if not directory.is_dir() or directory.is_symlink():
        return projected
    try:
        paths = sorted(directory.glob("*.json"))
        if not paths:
            return projected
        path = paths[-1]
        # Concurrent appends may allocate the same sequence; UUID ordering cannot pick an operator's intent.
        if len(paths) > 1 and paths[-2].name[:20] == path.name[:20]:
            return projected
        name = NAME.fullmatch(path.name)
        raw = _bytes(path, bounded=True)
        if name is None or name[1] != hashlib.sha256(raw).hexdigest():
            return projected
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != {"schema", "result_sha256", "failure_attribution"}:
            return projected
        original, saved = _result(Path(root))
        if value.get("schema") != 1 or value.get("result_sha256") != hashlib.sha256(original).hexdigest():
            return projected
        if saved != result:
            return projected
        recorded = value["failure_attribution"]
        if not isinstance(recorded, dict):
            return projected
        report = {key: row for key, row in recorded.items() if key != "confirmed_by"}
        with tempfile.TemporaryDirectory(prefix="scenario-attribution-") as tmp:
            source = Path(tmp) / "report.json"
            source.write_text(json.dumps(report))
            validated = validate(source, Path(root), recorded.get("confirmed_by"))
        if validated == recorded:
            projected["failure_attribution"] = validated
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        pass
    return projected
