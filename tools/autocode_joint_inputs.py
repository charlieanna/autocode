"""Fingerprint-bound local joint inputs for independent execution copies.

This copies data only, never run state, provider configuration or Git authority.
The coordinator's existing manifest permits only matching fixed local paths.
"""

import hashlib
import json
import re
import shutil
from pathlib import Path

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util

MANIFEST = ".autocode/candidate-inputs.json"


def _physical(root, relative):
    path = root / relative
    if path.is_symlink() or any(
        parent.is_symlink() for parent in path.parents if parent != root and parent.is_relative_to(root)
    ):
        raise ValueError("Joint candidate input must not be a symlink: " + relative)
    if not path.resolve().is_relative_to(root):
        raise ValueError("Joint candidate input escaped the workspace")
    return path


def _file(path):
    if not path.is_file():
        raise ValueError("Joint candidate input must be an ordinary file")
    return {"sha256": util.file_hash(path), "executable": bool(path.stat().st_mode & 0o111)}


def inventory(workspace):
    """Authenticate the complete copied payload, including missing/extra files."""
    root = Path(workspace).resolve()
    manifest = _physical(root, MANIFEST)
    if not manifest.exists():
        return {}
    if not manifest.is_file() or manifest.stat().st_size > 1024 * 1024:
        raise ValueError("Invalid joint candidate manifest")
    raw = manifest.read_bytes()
    mapping = json.loads(raw)
    repository_path = _physical(root, "repositories.json")
    if not repository_path.is_file() or repository_path.stat().st_size > 1024 * 1024:
        raise ValueError("Invalid joint repository inventory")
    repositories = util.read_object(repository_path)
    if not isinstance(mapping, dict) or not mapping or set(mapping) != set(repositories):
        raise ValueError("Joint candidate manifest does not match the repository inventory")
    result, seen = {MANIFEST: _file(manifest)}, set()
    for name, row in mapping.items():
        if not isinstance(row, dict) or set(row) != {"source", "destination", "fingerprint"}:
            raise ValueError("Invalid joint candidate entry")
        source = row["source"]
        if not isinstance(source, str) or not re.fullmatch(r"\.autocode/candidate-inputs/[a-zA-Z0-9_-]+", source):
            raise ValueError("Joint candidate source must name its fixed local copy")
        slug = source.rsplit("/", 1)[1]
        if (
            source in seen
            or row["destination"] != "repos/" + slug
            or not isinstance(repositories[name], dict)
            or repositories[name].get("path") != row["destination"]
        ):
            raise ValueError("Joint candidate destination does not match the repository inventory")
        seen.add(source)
        directory = _physical(root, source)
        if not directory.is_dir():
            raise ValueError("Joint candidate copy is missing")
        files = {}
        for path in sorted(directory.rglob("*")):
            relative = path.relative_to(root).as_posix()
            _physical(root, relative)
            if path.is_dir():
                continue
            name = path.relative_to(directory).as_posix()
            if any(part in (".git", ".autocode", ".autocode-ui") for part in Path(name).parts):
                raise ValueError("Joint candidate contains internal control data")
            files[name] = _file(path)
            result[relative] = files[name]
        fingerprint = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
        if fingerprint != row["fingerprint"]:
            raise ValueError("Joint candidate fingerprint changed: " + source)
    if manifest.read_bytes() != raw:
        raise ValueError("Joint candidate manifest changed while reading")
    return result


def copy(workspace, destination):
    """Retain only authenticated joint inputs; return their execution identity."""
    root, target = Path(workspace).resolve(), Path(destination).resolve()
    before = inventory(root)
    for name, expected in before.items():
        original, copied = _physical(root, name), _physical(target, name)
        if copied.exists():
            raise ValueError("Joint input destination already exists")
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, copied)
        if _file(original) != expected or _file(copied) != expected:
            raise ValueError("Joint candidate changed while copying")
    if inventory(root) != before:
        raise ValueError("Joint candidate inputs changed while copying")
    return before
