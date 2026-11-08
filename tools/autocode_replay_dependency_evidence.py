"""Inspect retained copy bytes, never capture custody or execution authority.

Selectors come only from core stage records. The recording exporter supplies
its bounded no-follow reader; no model path, command, or live-copy executor is
used. Observed file/directory identities are rechecked before returning facts.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import stat
from pathlib import Path, PurePosixPath

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util

MAX_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_FILES = 10000
MAX_PATH = 4096
MAX_DEPTH = 128
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_MANIFEST = re.compile(r"tool-containment-[0-9a-f]{32}/verification-copy\.json\Z")
_INTERNAL = {".git", ".autocode", ".autocode-ui"}


def selector(record):
    """Bound the optional public projection without reading or resolving paths."""
    if "verification_copy" not in record:
        return {}
    original = record["verification_copy"]
    original = original if isinstance(original, dict) else {}
    value = {"version": 1}
    for key, bound in (("manifest", 4096), ("sha256", 64), ("source_revision", 64)):
        field = original.get(key)
        value[key] = field if isinstance(field, str) and 0 < len(field) <= bound else None
    return {"verification_copy": value}


class _Problem(ValueError):
    def __init__(self, status, reason):
        self.status, self.reason = status, reason


def _require(condition, reason, status="invalid"):
    if not condition:
        raise _Problem(status, reason)


def _hash(value):
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def _relative(value, *, source=False):
    _require(isinstance(value, str) and bool(value) and "\\" not in value and "\x00" not in value, "unsafe_path")
    _require(len(value) <= MAX_PATH and value.count("/") < MAX_DEPTH, "inspection_path_limit", "limited")
    path = PurePosixPath(value)
    _require(
        bool(path.parts) and not path.is_absolute() and path.as_posix() == value and ".." not in path.parts,
        "unsafe_path",
    )
    _require(not source or not any(part.casefold() in _INTERNAL for part in path.parts), "unsafe_path")
    return path


def _json(data):
    def unique(pairs):
        value = {}
        for key, child in pairs:
            _require(key not in value, "duplicate_manifest_key")
            value[key] = child
        return value

    def constant(_):
        raise _Problem("invalid", "malformed_manifest")

    return json.loads(data, object_pairs_hook=unique, parse_constant=constant)


def _identity(info, *, directory):
    identity = (info.st_dev, info.st_ino, info.st_mode)
    return identity if directory else (*identity, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class _Inspection:
    def __init__(self, root, read, budget):
        self.root, self.read, self.budget = Path(root), read, budget
        self.observed = {}
        self.names = {}

    def observe(self, path, *, directory):
        info = path.lstat()
        _require(not stat.S_ISLNK(info.st_mode), "aliased_path")
        _require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode), "special_or_wrong_file_type")
        _require(directory or info.st_nlink == 1, "aliased_file")
        inode = (info.st_dev, info.st_ino)
        _require(inode not in self.names or self.names[inode] == path, "aliased_path")
        self.names[inode] = path
        identity = _identity(info, directory=directory)
        _require(path not in self.observed or self.observed[path] == (identity, directory), "changed_during_inspection")
        self.observed[path] = (identity, directory)
        return info

    def verify(self, paths=None):
        for path in self.observed if paths is None else paths:
            identity, directory = self.observed[path]
            try:
                current = _identity(path.lstat(), directory=directory)
            except OSError:
                raise _Problem("invalid", "changed_during_inspection") from None
            _require(current == identity, "changed_during_inspection")

    def leaf(self, name):
        relative = _relative(name)
        path = self.root / relative
        for parent in reversed(path.parents):
            self.observe(parent, directory=True)
        info = self.observe(path, directory=False)
        _require(self.budget["files"] < MAX_FILES, "inspection_file_limit", "limited")
        limit = min(MAX_FILE_BYTES, self.budget["bytes"])
        _require(info.st_size <= limit, "inspection_byte_limit", "limited")
        data, mode = self.read(self.root, relative.as_posix(), limit=limit)
        _require(len(data) <= limit, "inspection_byte_limit", "limited")
        self.budget["files"] += 1
        self.budget["bytes"] -= len(data)
        self.verify([path, *path.parents])
        return data, mode


def _manifest(value, *, workspace, run_dir, manifest, snapshot, expected_source):
    _require(isinstance(value, dict), "malformed_manifest")
    fields = {
        "version",
        "workspace",
        "run_dir",
        "tree",
        "source_revision",
        "source_paths",
        "files",
        "directories",
        "joint_inputs",
    }
    _require(set(value) == fields, "unsupported_manifest_shape", "unsupported")
    _require(type(value["version"]) is int and value["version"] == 1, "unsupported_manifest_version", "unsupported")
    tree = manifest.parent / "scratch/verification"
    _require(
        value["workspace"] == str(workspace) and value["run_dir"] == str(run_dir) and value["tree"] == str(tree),
        "foreign_copy_layout",
    )
    _require(value["source_revision"] == expected_source, "source_revision_mismatch")
    _require(value["source_paths"] == snapshot["source_paths"], "source_selection_mismatch")
    files, directories = value["files"], value["directories"]
    _require(isinstance(files, dict) and isinstance(directories, list), "malformed_manifest")
    _require(len(files) <= MAX_FILES and len(directories) <= MAX_FILES, "manifest_entry_limit", "limited")
    for name in [*files, *directories]:
        _relative(name)
    _require(len(set(directories)) == len(directories), "duplicate_directory")
    _require(not set(files).intersection(directories), "ambiguous_manifest_path")
    _require(value["joint_inputs"] == {}, "joint_inputs_unsupported", "unsupported")
    for identity in files.values():
        _require(
            isinstance(identity, str) and _hash(identity.removeprefix("executable:")),
            "nonregular_copy_input",
            "unsupported",
        )
    return tree, files


def inspect_copy(value, snapshot, *, workspace, run_dir, read, budget=None):
    """Report pin/regular-source integrity within the original run, not portability."""
    result = {
        "version": 1,
        "scope": "manifest_and_regular_source",
        "status": "unavailable",
        "reason": "selector_missing",
        "source_files_checked": 0,
        "source_bytes_checked": 0,
    }
    if value is None:
        return result
    inspection = _Inspection(run_dir, read, budget if budget is not None else {"files": 0, "bytes": MAX_BYTES})
    try:
        _require(
            isinstance(value, dict) and set(value) == {"version", "manifest", "sha256", "source_revision"},
            "malformed_selector",
        )
        _require(type(value["version"]) is int and value["version"] == 1, "unsupported_selector_version", "unsupported")
        _require(_hash(value["sha256"]) and _hash(value["source_revision"]), "malformed_selector")
        name = value["manifest"]
        _require(
            isinstance(name, str) and len(name) <= 4096 and "\\" not in name and "\x00" not in name,
            "malformed_selector",
        )
        manifest = PurePosixPath(name)
        _require(manifest.is_absolute() and manifest.as_posix() == name and ".." not in manifest.parts, "unsafe_path")
        _require(manifest.is_relative_to(run_dir), "foreign_run_selector")
        relative = manifest.relative_to(run_dir).as_posix()
        _require(_MANIFEST.fullmatch(relative) is not None, "unsupported_copy_layout", "unsupported")
        _require(
            Path(workspace).is_absolute()
            and Path(run_dir).is_absolute()
            and Path(run_dir).parent == Path(workspace) / ".autocode/runs",
            "foreign_run_layout",
        )
        _require(
            isinstance(snapshot, dict)
            and isinstance(snapshot.get("files"), dict)
            and isinstance(snapshot.get("source_paths"), list)
            and isinstance(snapshot.get("head"), str)
            and re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", snapshot["head"]) is not None,
            "malformed_snapshot",
        )
        _require(
            len(snapshot["files"]) <= MAX_FILES and len(snapshot["source_paths"]) <= MAX_FILES,
            "snapshot_entry_limit",
            "limited",
        )
        for selected in snapshot["source_paths"]:
            _relative(selected, source=True)
            _require(not any(c in selected for c in "*?[]\n\r"), "malformed_source_selection")
        _require(snapshot["source_paths"] == sorted(set(snapshot["source_paths"])), "malformed_source_selection")
        expected_source = util.digest({"head": snapshot.get("head"), "files": snapshot["files"]})
        _require(value["source_revision"] == expected_source, "source_revision_mismatch")
        data, _mode = inspection.leaf(relative)
        _require(hashlib.sha256(data).hexdigest() == value["sha256"], "manifest_pin_mismatch")
        tree, files = _manifest(
            _json(data),
            workspace=workspace,
            run_dir=run_dir,
            manifest=manifest,
            snapshot=snapshot,
            expected_source=expected_source,
        )
        for name, expected in snapshot["files"].items():
            _relative(name, source=True)
            _require(
                isinstance(expected, str) and _hash(expected.removeprefix("executable:")),
                "nonregular_source_input",
                "unsupported",
            )
            _require(files.get(name) == expected, "source_manifest_mismatch")
            payload, mode = inspection.leaf((tree / name).relative_to(run_dir).as_posix())
            observed = ("executable:" if mode & 0o111 else "") + hashlib.sha256(payload).hexdigest()
            _require(observed == expected, "source_bytes_or_mode_mismatch")
            result["source_files_checked"] += 1
            result["source_bytes_checked"] += len(payload)
        inspection.verify()
        status, reason = "matched", "recorded_manifest_and_regular_source_match"
    except _Problem as error:
        status, reason = error.status, error.reason
    except FileNotFoundError:
        status, reason = "unavailable", "evidence_missing"
    except (NotADirectoryError, IsADirectoryError):
        status, reason = "invalid", "unsafe_or_changed_path"
    except OSError:
        status, reason = "unavailable", "evidence_unreadable"
    except (ValueError, TypeError, RecursionError):
        status, reason = "invalid", "malformed_or_changed_evidence"
    return {**result, "status": status, "reason": reason}


def inspect_inventory(inventory, *, document, records, workspace, run_dir, read):
    """Inspect each referenced dispatch once, within one shared I/O budget."""
    result = copy.deepcopy(inventory)
    inspected = {}
    budget = {"files": 0, "bytes": MAX_BYTES}
    dispatches = {row["id"]: row for row in document["dispatches"]}
    for entry in result["dependencies"]:
        identity = entry["dispatch"]
        if identity not in inspected:
            row = dispatches[identity]
            inspected[identity] = inspect_copy(
                records[row["ordinal"]].get("verification_copy"),
                document["snapshots"][row["before"]],
                workspace=workspace,
                run_dir=run_dir,
                read=read,
                budget=budget,
            )
        entry["copy_evidence"] = inspected[identity]
        if inspected[identity]["reason"] != "selector_missing":
            entry["issues"] = [issue for issue in entry["issues"] if issue != "copy_manifest_not_supplied"]
            if inspected[identity]["status"] != "matched":
                entry["issues"].append("copy_evidence_" + inspected[identity]["status"])
            elif (
                entry.get("copy_manifest_sha256_claim")
                != records[dispatches[identity]["ordinal"]]["verification_copy"]["sha256"]
            ):
                entry["issues"].append("copy_manifest_claim_mismatch")
    return result
