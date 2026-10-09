"""Display-only screenshot evidence from an already projected task view.

Only image refs recorded against a criterion are addressable. Opening a card
never constitutes approval or current visual acceptance.
"""

import hashlib
import json
import os
import stat
from pathlib import Path

MAX_IMAGE_BYTES = 12 * 1024 * 1024
EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def recorded_hash(view, ref, workspace=None):
    validation = view.get("validation") or {}
    pins = validation.get("evidence_hashes") or {}
    if not isinstance(pins, dict):
        return None
    base = workspace or view.get("workspace")
    absolute = str(Path(base) / ref) if base and not Path(ref).is_absolute() else ref
    return pins.get(ref) or pins.get(absolute)


def records(view):
    validation = view.get("validation")
    if not isinstance(validation, dict):
        return []
    criteria = {str(c.get("id")): c for c in view.get("criteria", []) if isinstance(c, dict)}
    results = validation.get("criterion_results")
    identity = hashlib.sha256(json.dumps(validation, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    rows = []
    seen = set()
    for row in results if isinstance(results, list) else []:
        if not isinstance(row, dict) or str(row.get("id")) not in criteria:
            continue
        cid = str(row["id"])
        refs = row.get("evidence_refs")
        for ref in refs if isinstance(refs, list) else []:
            if not isinstance(ref, str) or Path(ref).suffix.lower() not in EXTENSIONS or (cid, ref) in seen:
                continue
            seen.add((cid, ref))
            token = hashlib.sha256(json.dumps([identity, cid, ref], separators=(",", ":")).encode()).hexdigest()
            rows.append(
                {
                    "id": token,
                    "criterion_id": cid,
                    "label": criteria[cid].get("criterion") or criteria[cid].get("description") or cid,
                    "status": str(row.get("status") or "UNCHECKED"),
                    "source_revision": validation.get("source_revision"),
                    "hash_recorded": bool(recorded_hash(view, ref)),
                    "ref": ref,
                }
            )
    return rows


def project(view):
    return [{key: value for key, value in row.items() if key != "ref"} for row in records(view)]


def read_image(view, image_id, workspace, run):
    row = next((row for row in records(view) if row["id"] == image_id), None)
    if not row:
        raise ValueError("Screenshot no longer belongs to this saved verification")
    workspace, run = Path(workspace).resolve(strict=True), Path(run).resolve(strict=True)
    ref = Path(row["ref"])
    candidate = ref if ref.is_absolute() else workspace / ref
    # Prefer the run root when explicitly recorded there. Never accept a path
    # outside this registered task/project or any parent traversal.
    root = run if candidate.is_relative_to(run) else workspace
    if root != run and candidate.is_relative_to(workspace / ".autocode/runs"):
        raise ValueError("Screenshot belongs to a different task")
    try:
        parts = candidate.relative_to(root).parts
    except ValueError as error:
        raise ValueError("Screenshot is outside this task and project") from error
    if not parts or any(part in (".", "..", ".git") for part in parts):
        raise ValueError("Invalid screenshot path")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(file_fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_IMAGE_BYTES:
                raise ValueError("Screenshot is not a bounded regular file")
            raw = stream.read(MAX_IMAGE_BYTES + 1)
    except OSError as error:
        raise ValueError("Screenshot is unavailable or contains a symlink") from error
    finally:
        os.close(fd)
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("Screenshot is too large")
    expected = recorded_hash(view, row["ref"], workspace)
    if expected and hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("Screenshot differs from the saved verification fingerprint")
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif raw.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        raise ValueError("Screenshot has an unsupported image format")
    return raw, mime
