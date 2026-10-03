"""Immutable provider-neutral inventories of exported Figma references.

Only new-run configuration writes settings.design_manifest. Stage contexts and
coverage gates read it; this module never reads a run's private state file.
"""
from __future__ import annotations
import copy
import math
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import tempfile
try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util


def obj(properties):
    return {"type": "object", "additionalProperties": False,
            "properties": properties, "required": list(properties)}


TEXT = {"type": "string", "minLength": 1}
TEXTS = {"type": "array", "minItems": 1, "items": TEXT}
ARTIFACT = obj({"path": TEXT, "sha256": TEXT})
SCHEMA = obj({
    "version": {"type": "integer", "enum": [1]},
    "files": {"type": "array", "minItems": 1, "items": obj({"key": TEXT, "nodes": TEXTS})},
    "cases": {"type": "array", "minItems": 1, "items": obj({
        "id": TEXT, "file_key": TEXT, "node_id": TEXT, "state": TEXT, "route": TEXT,
        "implementation_paths": TEXTS,
        "viewport": obj({"width": {"type": "integer"}, "height": {"type": "integer"},
                         "device_scale_factor": {}}),
        "export_scale": {},
        "artifacts": obj({"screenshot": ARTIFACT, "design_context": ARTIFACT}),
    })},
})


def relative_path(value):
    path = PurePosixPath(value)
    if (not value.strip() or path.is_absolute() or ".." in path.parts
            or "\\" in value or str(path) != value or value == "."):
        raise ValueError(f"Design input paths must be portable relative paths: {value}")
    return path


def validate(body):
    util.validate_schema(body, SCHEMA)
    inventory, keys = set(), []
    for file in body["files"]:
        keys.append(file["key"])
        if not re.fullmatch(r"[A-Za-z0-9]+", file["key"]):
            raise ValueError("Invalid Figma file key")
        if len(file["nodes"]) != len(set(file["nodes"])):
            raise ValueError("Duplicate declared Figma node")
        for node in file["nodes"]:
            if not re.fullmatch(r"[0-9]+:[0-9]+", node):
                raise ValueError("Figma node IDs must use the canonical colon form")
            inventory.add((file["key"], node))
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate Figma file")
    identities, ids, covered, paths = set(), set(), set(), {}
    for case in body["cases"]:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", case["id"]) or case["id"] in ids:
            raise ValueError("Design case IDs must be unique stable identifiers")
        ids.add(case["id"])
        if not case["state"].strip() or not case["route"].strip():
            raise ValueError("Every design case needs a state and route")
        frame = (case["file_key"], case["node_id"])
        if frame not in inventory:
            raise ValueError(f"Undeclared Figma file/frame: {case['id']}")
        covered.add(frame)
        viewport = case["viewport"]
        for number in (*viewport.values(), case["export_scale"]):
            if type(number) not in (int, float) or not math.isfinite(number) or number <= 0:
                raise ValueError("Design viewport and export scale must be finite and positive")
        identity = (*frame, case["state"], viewport["width"], viewport["height"], viewport["device_scale_factor"])
        if identity in identities:
            raise ValueError("Duplicate Figma frame/state/viewport")
        identities.add(identity)
        for path in case["implementation_paths"]:
            relative_path(path)
        for artifact in case["artifacts"].values():
            relative_path(artifact["path"])
            if not re.fullmatch(r"[a-f0-9]{64}", artifact["sha256"]):
                raise ValueError("Every design artifact needs its SHA256")
            previous = paths.setdefault(artifact["path"], artifact["sha256"])
            if previous != artifact["sha256"]:
                raise ValueError("Conflicting design artifact hashes")
    if covered != inventory:
        raise ValueError(f"Declared Figma frames have no implementation case: {sorted(inventory - covered)}")


def png_dimensions(path):
    with Path(path).open("rb") as stream:
        header = stream.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ValueError(f"Expected a PNG capture: {path}")
    return struct.unpack(">II", header[16:24])


def verify(record):
    body, root = record["body"], Path(record["root"]).resolve()
    validate(body)
    if record["manifest_hash"] != util.digest(body):
        raise ValueError("Saved design manifest identity changed")
    for case in body["cases"]:
        for artifact in case["artifacts"].values():
            path = root / artifact["path"]
            if (path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file()
                    or util.file_hash(path) != artifact["sha256"] or path.stat().st_size == 0):
                raise ValueError(f"Missing or changed design reference: {case['id']} / {artifact['path']}")
        expected = tuple(round(case["viewport"][key] * case["export_scale"]) for key in ("width", "height"))
        if png_dimensions(root / case["artifacts"]["screenshot"]["path"]) != expected:
            raise ValueError(f"Reference export dimensions disagree with viewport/export_scale: {case['id']}")
    return record


def load(path):
    path = Path(path).resolve()
    body = util.read_object(path)
    return verify({"body": body, "manifest_hash": util.digest(body), "root": str(path.parent)})


def retain(record, workspace):
    """Retain ignored/external exports inside the new task's private input bundle."""
    verify(record)
    parent = Path(workspace).resolve() / ".autocode" / "design-inputs"
    parent.mkdir(parents=True, exist_ok=True)
    destination = parent / record["manifest_hash"]
    retained = {**copy.deepcopy(record), "root": str(destination)}
    if destination.exists():
        return verify(retained)  # never replace a previously retained reference
    temporary = Path(tempfile.mkdtemp(prefix=".design-", dir=parent))
    try:
        for case in record["body"]["cases"]:
            for artifact in case["artifacts"].values():
                target = temporary / artifact["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(Path(record["root"]) / artifact["path"], target)
        verify({**record, "root": str(temporary)})
        temporary.rename(destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return verify(retained)


def context(settings):
    record = settings.get("design_manifest")
    if not record:
        return None
    try:
        verify(record)
    except (ValueError, OSError) as error:
        raise util.Paused("PAUSED_DESIGN_REFERENCE", str(error)) from error
    return copy.deepcopy(record)


INSTRUCTION = """
DESIGN COVERAGE INVENTORY
The retained design_manifest is the complete declared file/frame/state inventory.
Use exact exported PNGs, design context, routes, implementation paths and native CSS
viewport; export_scale describes reference pixels, not the browser CSS width.
Keep every case in the approved plan and map it to acceptance criteria. Do not edit
references or replace them with screenshots of the implementation. Missing access
or proof remains NOT_VERIFIED, never an invented PASS or human acceptance.
Intermediate tasks may leave future cases NOT_VERIFIED; overall completion needs
fresh independent PASS for every case on the same source and approved contract.
The independent Validator reports design_manifest_hash and design_results with each
case ID exactly once, mapped criterion_ids, status, candidate_ref and comparison_ref.
A PASS needs a current rendered PNG at viewport * device_scale_factor and a separate
comparison artifact describing reference comparison and functional/state checks.
Do not cite a reference PNG as the candidate or use a passing test log as a capture.
"""
