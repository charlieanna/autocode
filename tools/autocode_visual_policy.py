"""Pinned comparison policies for exported version-1 Figma reference bundles.

This is an executable-check input, not another task contract or acceptance owner.
Paths are portable and source-owned so the same check can run in a clean replay.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path, PurePosixPath


def object_fields(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields.split()):
        raise ValueError(f"{label}: expected exactly {fields}")


def relative(value):
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("Expected a portable relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value or value == ".":
        raise ValueError(f"Expected a portable relative path: {value}")
    return path


def contained(root, value, *, is_file=True):
    path = Path(root) / relative(value)
    for item in (path, *path.parents):
        if item == Path(root):
            break
        if item.is_symlink():
            raise ValueError(f"Symlink is not a visual input/output: {path}")
    if not path.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError(f"Visual path escapes its root: {path}")
    if is_file and not path.is_file():
        raise ValueError(f"Missing visual input: {path}")
    return path


def sha256(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("Expected a lowercase SHA-256 digest")
    return value


def read_json(path, expected=None):
    data = Path(path).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if expected is not None and digest != sha256(expected):
        raise ValueError(f"Pinned input changed: {path}")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(value):
        raise ValueError(f"Nonfinite JSON number: {value}")

    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant), digest


def number(value, label, *, minimum=0, maximum=None, integer=False):
    try:
        valid = (type(value) in ((int,) if integer else (int, float)) and math.isfinite(value)
                 and value >= minimum and (maximum is None or value <= maximum))
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"Invalid {label}: {value!r}")
    return value


def viewport(value):
    object_fields(value, "width height device_scale_factor", "viewport")
    for key in ("width", "height"):
        number(value[key], key, minimum=1, maximum=16384, integer=True)
    number(value["device_scale_factor"], "device_scale_factor", minimum=0.01, maximum=8)
    return value


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Expected nonempty {label}")
    return value


def _list(value, label):
    if not isinstance(value, list) or not value or len(value) > 256:
        raise ValueError(f"Expected 1..256 {label}")
    return value


def _identity(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", value):
        raise ValueError("Case/region IDs must be stable alphanumeric identifiers")
    return value


def load(workspace, policy_path, expected):
    """Return validated policy, manifest and hashes of every immutable input."""
    root = Path(workspace).resolve()
    path = contained(root, policy_path)
    policy, digest = read_json(path, sha256(expected))
    object_fields(policy, "version manifest capture_command timeout_seconds cases", "policy")
    if type(policy["version"]) is not int or policy["version"] != 1:
        raise ValueError("Unsupported visual policy version")
    object_fields(policy["manifest"], "path sha256", "manifest reference")
    manifest_path = contained(path.parent, policy["manifest"]["path"])
    manifest, manifest_hash = read_json(manifest_path, sha256(policy["manifest"]["sha256"]))
    object_fields(manifest, "version files cases", "manifest")
    if type(manifest["version"]) is not int or manifest["version"] != 1:
        raise ValueError("Unsupported reference manifest version")
    files, nodes = set(), set()
    for file in _list(manifest["files"], "Figma files"):
        object_fields(file, "key nodes", "Figma file")
        key = _text(file["key"], "Figma file key")
        if not re.fullmatch(r"[A-Za-z0-9]+", key) or key in files:
            raise ValueError("Invalid or duplicate Figma file key")
        files.add(key)
        for node in _list(file["nodes"], "Figma nodes"):
            if not isinstance(node, str) or not re.fullmatch(r"[0-9]+:[0-9]+", node) or (key, node) in nodes:
                raise ValueError("Invalid or duplicate Figma node")
            nodes.add((key, node))
    pins = {str(path.relative_to(root)): digest, str(manifest_path.relative_to(root)): manifest_hash}
    cases, identities, covered = {}, set(), set()
    for case in _list(manifest["cases"], "reference cases"):
        object_fields(case, "id file_key node_id state route implementation_paths viewport export_scale artifacts", "case")
        key = _identity(case["id"])
        frame = (_text(case["file_key"], "file key"), _text(case["node_id"], "node ID"))
        if key in cases or frame not in nodes:
            raise ValueError(f"Duplicate case or undeclared Figma frame: {key}")
        view = viewport(case["viewport"])
        number(case["export_scale"], "export_scale", minimum=0.01, maximum=8)
        _text(case["route"], "route")
        _text(case["state"], "state")
        identity = (*frame, case["state"], view["width"], view["height"], view["device_scale_factor"])
        if identity in identities:
            raise ValueError(f"Duplicate frame/state/viewport: {key}")
        identities.add(identity)
        covered.add(frame)
        for implementation in _list(case["implementation_paths"], "implementation paths"):
            relative(implementation)
        object_fields(case["artifacts"], "screenshot design_context", "reference artifacts")
        for artifact in case["artifacts"].values():
            object_fields(artifact, "path sha256", "reference artifact")
            target = contained(manifest_path.parent, artifact["path"])
            data = target.read_bytes()
            actual = hashlib.sha256(data).hexdigest()
            if not data or actual != sha256(artifact["sha256"]):
                raise ValueError(f"Missing or changed reference: {key} / {artifact['path']}")
            pins[str(target.relative_to(root))] = actual
        cases[key] = case
    if covered != nodes:
        raise ValueError("Declared Figma frames have no reference case")
    command = _list(policy["capture_command"], "capture command arguments")
    for word in command:
        _text(word, "capture command argument")
        if "\x00" in word:
            raise ValueError("NUL in capture command")
    number(policy["timeout_seconds"], "timeout_seconds", minimum=0.01, maximum=900)
    checks = {}
    for check in _list(policy["cases"], "comparison cases"):
        object_fields(check, "id channel_tolerance max_changed_ratio regions", "comparison case")
        key = _identity(check["id"])
        if key in checks or key not in cases:
            raise ValueError(f"Duplicate or unknown comparison case: {key}")
        number(check["channel_tolerance"], "channel_tolerance", maximum=254, integer=True)
        ratio = number(check["max_changed_ratio"], "max_changed_ratio", maximum=1)
        if ratio == 1:
            raise ValueError("max_changed_ratio must be less than 1")
        if not isinstance(check["regions"], list) or len(check["regions"]) > 256:
            raise ValueError("regions must be an array of at most 256 rectangles")
        region_ids = set()
        for region in check["regions"]:
            object_fields(region, "id x y width height max_changed_ratio", "region")
            region_id = _identity(region["id"])
            if region_id in region_ids:
                raise ValueError("Duplicate comparison region")
            region_ids.add(region_id)
            for axis in ("x", "y", "width", "height"):
                number(region[axis], axis, integer=True, minimum=1 if axis in ("width", "height") else 0)
            if number(region["max_changed_ratio"], "region max_changed_ratio", maximum=1) == 1:
                raise ValueError("Region max_changed_ratio must be less than 1")
            for axis, dimension in (("x", "width"), ("y", "height")):
                if region[axis] + region[dimension] > round(cases[key]["viewport"][dimension] * cases[key]["export_scale"]):
                    raise ValueError(f"Region outside reference pixels: {region_id}")
        checks[key] = check
    if checks.keys() != cases.keys():
        raise ValueError("Comparison policy must cover every reference case exactly once")
    return {"policy": policy, "manifest": manifest, "manifest_path": manifest_path, "pins": pins,
            "policy_sha256": digest, "manifest_sha256": manifest_hash, "cases": cases, "checks": checks}


def implementation_inputs(workspace, cases, snapshot):
    """Declared files/directories must actually belong to the source being bound."""
    root, inputs = Path(workspace), {}
    for case in cases.values():
        for name in case["implementation_paths"]:
            target = contained(root, name, is_file=False)
            if not target.exists():
                raise ValueError(f"Missing declared implementation input: {name}")
            paths = sorted(target.rglob("*")) if target.is_dir() else [target]
            files = []
            for path in paths:
                relative_name = str(path.relative_to(root))
                contained(root, relative_name, is_file=False)
                if path.is_file():
                    if "__pycache__" in path.parts or path.suffix == ".pyc":
                        continue
                    if relative_name not in snapshot["files"]:
                        raise ValueError(f"Declared implementation input is not source-owned: {relative_name}")
                    files.append(relative_name)
                    inputs[relative_name] = snapshot["files"][relative_name]
            if not files:
                raise ValueError(f"Declared implementation input has no source files: {name}")
    return inputs
