"""Declared public input preflight: identity checks for supplied task inputs.

A run that depends on operator-supplied public proof inputs declares them in a
manifest of project-relative POSIX paths with type, mode, size and sha256. This
module validates the manifest and checks every declared input both in the
original workspace and in the actual copied source tree, so an input stored
under an ignored path (and therefore absent from the Git-enumerated copy)
fails here as a setup error instead of surfacing mid-run as a stop asking
where the supplied inputs are. Bottom-layer helper: standard library only, no
AutoCode imports.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath

_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_SUPPORTED_TYPES = ("file",)
_NON_IGNORED_REMEDY = (
    "store supplied public inputs in a non-ignored project-relative location "
    "such as 'pilot-public/' and declare that path in the manifest and brief"
)


def _normalize_entry(raw, index):
    """(entry, None) for one valid manifest item, or (None, error message)."""
    if not isinstance(raw, dict):
        return None, f"manifest inputs[{index}]: expected an object, found {type(raw).__name__}"
    path = raw.get("path")
    if not isinstance(path, str) or not path.strip():
        return None, f"manifest inputs[{index}]: path must be a non-empty string"
    pure = PurePosixPath(path)
    if (
        pure.is_absolute()
        or path.startswith("/")
        or "\\" in path
        or path.endswith("/")
        or any(part in ("..", ".") for part in pure.parts)
        or not pure.parts
    ):
        return None, (f"{path}: manifest path must be project-relative (a relative POSIX path with no '..' component)")
    kind = raw.get("type")
    if kind not in _SUPPORTED_TYPES:
        return None, f"{path}: manifest type must be one of {', '.join(_SUPPORTED_TYPES)}, found {kind!r}"
    mode = raw.get("mode")
    if isinstance(mode, bool):
        return None, f"{path}: manifest mode must be an integer or an octal string such as '0644'"
    try:
        if isinstance(mode, str):
            mode = int(mode, 8)
        elif not isinstance(mode, int):
            raise ValueError
    except ValueError:
        return None, f"{path}: manifest mode must be an integer or an octal string such as '0644'"
    if not 0 <= mode <= 0o777:
        return None, f"{path}: manifest mode must be between 0 and 0777, found 0{mode:o}"
    size = raw.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        return None, f"{path}: manifest size must be a nonnegative integer, found {size!r}"
    sha256 = raw.get("sha256")
    if not isinstance(sha256, str) or not _SHA256.match(sha256):
        return None, f"{path}: manifest sha256 must be 64 hexadecimal characters, found {sha256!r}"
    return {"path": path, "type": kind, "mode": mode, "size": size, "sha256": sha256.lower()}, None


def load_manifest(manifest_path):
    """Load and validate a declared-input manifest.

    Returns (entries, errors): one normalized entry per valid item plus one
    error message per invalid or duplicate entry. Invalid entries are dropped,
    so no identity check ever runs for them.
    """
    try:
        document = json.loads(Path(manifest_path).read_text())
    except (OSError, ValueError) as error:
        return [], [f"manifest {manifest_path}: unreadable or invalid JSON: {error}"]
    if not isinstance(document, dict) or not isinstance(document.get("inputs"), list):
        return [], [f"manifest {manifest_path}: expected an object with an 'inputs' list"]
    return validate_inputs(document["inputs"])


def validate_inputs(raw_inputs):
    """Validate an already-read input inventory without another file read."""
    if not isinstance(raw_inputs, list):
        return [], ["manifest inputs must be a list"]
    entries, errors, seen = [], [], set()
    for index, raw in enumerate(raw_inputs):
        entry, error = _normalize_entry(raw, index)
        if error:
            errors.append(error)
            continue
        if entry["path"] in seen:
            errors.append(f"{entry['path']}: duplicate manifest entry")
            continue
        seen.add(entry["path"])
        entries.append(entry)
    return entries, errors


def _kind_of(mode_bits):
    if stat.S_ISLNK(mode_bits):
        return "symlink"
    if stat.S_ISDIR(mode_bits):
        return "directory"
    if stat.S_ISREG(mode_bits):
        return "file"
    return "special"


def _sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_root(root, entry, label):
    """(status, message) for one declared input in one root.

    status is "missing", "type" or "mismatch", or None when the input matches.
    Type is checked before content, including directory symlink ancestors: a
    declared input must be a direct file within its selected root, even when a
    link target carries the declared bytes.
    """
    root = Path(root).resolve()
    relative = PurePosixPath(entry["path"])
    for parent in relative.parents:
        if (root / parent).is_symlink():
            return "type", (
                f"{entry['path']}: {label} type mismatch: "
                f"symlink ancestor {parent} is not a direct path within the root"
            )
    path = root / Path(*relative.parts)
    try:
        info = os.lstat(path)
    except OSError:
        return "missing", None
    found_kind = _kind_of(info.st_mode)
    if found_kind != entry["type"]:
        return "type", f"{entry['path']}: {label} type mismatch: expected {entry['type']}, found {found_kind}"
    fields = []
    found_mode = stat.S_IMODE(info.st_mode)
    if found_mode != entry["mode"]:
        fields.append(f"mode (expected 0{entry['mode']:o}, found 0{found_mode:o})")
    if info.st_size != entry["size"]:
        fields.append(f"size (expected {entry['size']}, found {info.st_size})")
    found_sha = _sha256_of(path)
    if found_sha != entry["sha256"]:
        fields.append(f"sha256 (expected {entry['sha256']}, found {found_sha})")
    if fields:
        return "mismatch", f"{entry['path']}: {label} mismatch: " + "; ".join(fields)
    return None, None


def check_inputs(original_root, copy_root, entries):
    """Check every declared input in the original workspace and the actual copy.

    Returns {"ok": [...], "errors": [...]}. An input missing from the original
    workspace is a setup error in its own right; an input that matches the
    original but is missing from the copy was excluded from the Git-enumerated
    source inventory (an ignored path), which is the gap this preflight exists
    to catch before any model work starts.
    """
    ok, errors = [], []
    for entry in entries:
        original_status, original_message = _check_root(original_root, entry, "original")
        copy_status, copy_message = _check_root(copy_root, entry, "copy")
        if original_status == "missing":
            errors.append(
                f"{entry['path']}: declared input is missing from the original workspace "
                f"{os.fspath(original_root)}; {_NON_IGNORED_REMEDY}"
            )
        elif original_status is not None:
            errors.append(original_message)
        if copy_status == "missing":
            if original_status is None:
                errors.append(
                    f"{entry['path']}: declared input is ignored (or otherwise excluded from the "
                    f"Git-enumerated source copy): it matches the original workspace but is missing "
                    f"from the copy root; {_NON_IGNORED_REMEDY}"
                )
        elif copy_status is not None:
            errors.append(copy_message)
        if original_status is None and copy_status is None:
            ok.append(
                {
                    "path": entry["path"],
                    "type": entry["type"],
                    "mode": entry["mode"],
                    "size": entry["size"],
                    "sha256": entry["sha256"],
                }
            )
    return {"ok": ok, "errors": errors}


def preflight(manifest_path, original_root, copy_root):
    """Load the manifest, then check it; manifest errors skip identity checks."""
    entries, errors = load_manifest(manifest_path)
    if errors:
        return {"ok": [], "errors": errors}
    return check_inputs(original_root, copy_root, entries)
