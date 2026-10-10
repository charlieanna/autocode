"""Immutable provider-neutral inventories of exported Figma references.

Configuration, native intake and audited reference revisions retain settings.design_manifest. Stage contexts and
coverage gates read it; this module never reads a run's private state file.
"""

from __future__ import annotations

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


import copy
import math
import re
import shutil
import struct
import tempfile
from pathlib import Path, PurePosixPath

try:
    from . import autocode_design_inventory as inventory
    from . import autocode_util as util
except ImportError:
    import autocode_design_inventory as inventory
    import autocode_util as util


def obj(properties):
    return {"type": "object", "additionalProperties": False, "properties": properties, "required": list(properties)}


TEXT = {"type": "string", "minLength": 1}
TEXTS = {"type": "array", "minItems": 1, "items": TEXT}
ARTIFACT = obj({"path": TEXT, "sha256": TEXT})
NODE_ID = re.compile(r"[0-9]+:[0-9]+\Z")
SCHEMA = obj(
    {
        "version": {"type": "integer", "enum": [1]},
        "files": {"type": "array", "minItems": 1, "items": obj({"key": TEXT, "nodes": TEXTS})},
        "cases": {
            "type": "array",
            "minItems": 1,
            "items": obj(
                {
                    "id": TEXT,
                    "file_key": TEXT,
                    "node_id": TEXT,
                    "state": TEXT,
                    "route": TEXT,
                    "implementation_paths": TEXTS,
                    "viewport": obj(
                        {"width": {"type": "integer"}, "height": {"type": "integer"}, "device_scale_factor": {}}
                    ),
                    "export_scale": {},
                    "artifacts": obj({"screenshot": ARTIFACT, "design_context": ARTIFACT}),
                }
            ),
        },
    }
)


def _object(properties, required=None):
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties) if required is None else required,
    }


VARIANT = _object({"page_id": TEXT, "node_id": TEXT, "properties": {"type": "object"}})
COMPONENT = _object(
    {
        "key": TEXT,
        "name": TEXT,
        "source_page_id": TEXT,
        "source_node_id": TEXT,
        "variants": {"type": "array", "items": VARIANT},
    }
)
VARIABLE = _object(
    {
        "key": TEXT,
        "name": TEXT,
        "kind": TEXT,
        "value": {},
        "collection": TEXT,
        "mode": TEXT,
        "source_id": TEXT,
        "mode_id": TEXT,
    }
)
FONT = _object(
    {
        "id": TEXT,
        "family": TEXT,
        "style": TEXT,
        "status": {"type": "string", "enum": ["available", "missing"]},
        "reason": TEXT,
        "artifact": ARTIFACT,
    },
    required=["id", "family", "style", "status"],
)
ASSET = _object(
    {
        "id": TEXT,
        "page_id": TEXT,
        "node_id": TEXT,
        "name": TEXT,
        "mime_type": TEXT,
        "status": {"type": "string", "enum": ["available", "missing"]},
        "reason": TEXT,
        "artifact": ARTIFACT,
    },
    required=["id", "node_id", "name", "mime_type", "status"],
)
TRANSITION = _object(
    {
        "id": TEXT,
        "source_node_id": TEXT,
        "target_node_id": {"type": "string"},
        "trigger": TEXT,
        "action": {"type": "object"},
    }
)
PAGE = _object({"id": TEXT, "name": TEXT, "metadata_xml": ARTIFACT, "source_json": ARTIFACT})
SCREEN_STATE = _object(
    {
        "page_id": TEXT,
        "node_id": TEXT,
        "state": TEXT,
        "viewport": _object({"width": {"type": "integer"}, "height": {"type": "integer"}, "device_scale_factor": {}}),
    }
)
FILE_V2 = _object(
    {
        "key": TEXT,
        "revision": TEXT,
        "metadata_xml": ARTIFACT,
        "pages": {"type": "array", "minItems": 1, "items": PAGE},
        "screen_states": {"type": "array", "items": SCREEN_STATE},
        "components": {"type": "array", "items": COMPONENT},
        "variables": {"type": "array", "items": VARIABLE},
        "fonts": {"type": "array", "items": FONT},
        "assets": {"type": "array", "items": ASSET},
        "transitions": {"type": "array", "items": TRANSITION},
    }
)
CASE_V2 = _object(
    {
        "native_size": _object({"width": {}, "height": {}}),
        "id": TEXT,
        "file_key": TEXT,
        "page_id": TEXT,
        "node_id": TEXT,
        "state": TEXT,
        "route": TEXT,
        "implementation_paths": TEXTS,
        "inventory_refs": _object(
            {
                key: {"type": "array", "items": TEXT}
                for key in ("components", "variables", "fonts", "assets", "transitions")
            }
        ),
        "viewport": _object({"width": {"type": "integer"}, "height": {"type": "integer"}, "device_scale_factor": {}}),
        "export_scale": {},
        "artifacts": _object({"screenshot": ARTIFACT, "design_context": ARTIFACT}),
    }
)
RESPONSIVE_TARGET = _object(
    {
        "id": TEXT,
        "source_case_id": TEXT,
        "reference_case_id": {"type": "string"},
        "viewport": _object({"width": {"type": "integer"}, "height": {"type": "integer"}, "device_scale_factor": {}}),
        "constraints": {"type": "array", "items": TEXT},
    }
)
SCHEMA_V2 = obj(
    {
        "version": {"type": "integer", "enum": [2]},
        "files": {"type": "array", "minItems": 1, "items": FILE_V2},
        "responsive_targets": {"type": "array", "items": RESPONSIVE_TARGET},
        "cases": {"type": "array", "minItems": 1, "items": CASE_V2},
    }
)


def relative_path(value):
    path = PurePosixPath(value)
    if (
        not value.strip()
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in value
        or str(path) != value
        or value == "."
    ):
        raise ValueError(f"Design input paths must be portable relative paths: {value}")
    return path


def validate(body, *, root=None):
    if isinstance(body, dict) and body.get("version") == 2:
        util.validate_schema(body, SCHEMA_V2)
        if root is None:
            raise ValueError("Version 2 inventory validation requires its bundle directory")
        files = body["files"]
        if len({file["key"] for file in files}) != len(files):
            raise ValueError("Duplicate Figma file")
        for file in files:
            if not re.fullmatch(r"[A-Za-z0-9]+", file["key"]):
                raise ValueError("Invalid Figma file key")
            page_ids = [page["id"] for page in file["pages"]]
            if len(page_ids) != len(set(page_ids)):
                raise ValueError(f"Duplicate Figma page in {file['key']}")
            for section in ("components", "variables", "fonts", "assets", "transitions"):
                identities = []
                for row in file[section]:
                    identity = (
                        row["key"]
                        if section == "components"
                        else row["id"]
                        if section in ("fonts", "assets", "transitions")
                        else row["key"]
                    )
                    identities.append(identity)
                if len(identities) != len(set(identities)):
                    raise ValueError(f"Duplicate {section} inventory entry in {file['key']}")
            for page in file["pages"]:
                if not NODE_ID.fullmatch(page["id"]):
                    raise ValueError(f"Invalid Figma page ID: {page['id']}")
            for row in file["components"]:
                if not NODE_ID.fullmatch(row["source_page_id"]) or not NODE_ID.fullmatch(row["source_node_id"]):
                    raise ValueError(f"Invalid component source identity: {row['key']}")
                for variant in row["variants"]:
                    if not NODE_ID.fullmatch(variant["page_id"]) or not NODE_ID.fullmatch(variant["node_id"]):
                        raise ValueError(f"Invalid component variant identity: {row['key']}")
            for row in file["fonts"]:
                _validate_available_reference(row, "font")
            for row in file["assets"]:
                _validate_available_reference(row, "asset")
    else:
        util.validate_schema(body, SCHEMA)
    if body.get("version") == 2:
        _validate_v2_cases(body, root)
        inventory.catalog(body, root)  # reject conflicting definitions of a shared component/token
        paths = {}
        for artifact in all_artifacts(body):
            relative_path(artifact["path"])
            if not re.fullmatch(r"[a-f0-9]{64}", artifact["sha256"]):
                raise ValueError("Every design artifact needs its SHA256")
            previous = paths.setdefault(artifact["path"], artifact["sha256"])
            if previous != artifact["sha256"]:
                raise ValueError("Conflicting design artifact hashes")
        return
    declared_nodes, keys = set(), []
    for file in body["files"]:
        keys.append(file["key"])
        if not re.fullmatch(r"[A-Za-z0-9]+", file["key"]):
            raise ValueError("Invalid Figma file key")
        if len(file["nodes"]) != len(set(file["nodes"])):
            raise ValueError("Duplicate declared Figma node")
        for node in file["nodes"]:
            if not NODE_ID.fullmatch(node):
                raise ValueError("Figma node IDs must use the canonical colon form")
            declared_nodes.add((file["key"], node))
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
        if frame not in declared_nodes:
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
    if covered != declared_nodes:
        raise ValueError(f"Declared Figma frames have no implementation case: {sorted(declared_nodes - covered)}")


def _validate_available_reference(row, kind):
    if row["status"] == "available":
        if "artifact" not in row or "reason" in row:
            raise ValueError(
                f"Available Figma {kind} requires a hash-bound local artifact and no missing reason: {row['id']}"
            )
        relative_path(row["artifact"]["path"])
        if not re.fullmatch(r"[a-f0-9]{64}", row["artifact"]["sha256"]):
            raise ValueError(f"Available Figma {kind} requires a SHA256: {row['id']}")
    elif "artifact" in row or not row.get("reason", "").strip():
        raise ValueError(f"Missing Figma {kind} must carry a reason and no guessed artifact: {row['id']}")


def _validate_v2_cases(body, root):
    ids = set()
    page_ids = {(file["key"], page["id"]) for file in body["files"] for page in file["pages"]}
    identities, covered, paths = set(), set(), {}
    for case in body["cases"]:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", case["id"]) or case["id"] in ids:
            raise ValueError("Design case IDs must be unique stable identifiers")
        ids.add(case["id"])
        if (case["file_key"], case["page_id"]) not in page_ids:
            raise ValueError(f"Design case references an unknown Figma page: {case['id']}")
        if not case["state"].strip() or not case["route"].strip():
            raise ValueError("Every design case needs a state and route")
        for number in (*case["viewport"].values(), *case["native_size"].values(), case["export_scale"]):
            if type(number) not in (int, float) or not math.isfinite(number) or number <= 0:
                raise ValueError("Design viewport and export scale must be finite and positive")
        identity = (
            case["file_key"],
            case["page_id"],
            case["node_id"],
            case["state"],
            case["viewport"]["width"],
            case["viewport"]["height"],
            case["viewport"]["device_scale_factor"],
        )
        if identity in identities:
            raise ValueError("Duplicate Figma frame/state/viewport")
        identities.add(identity)
        covered.add((case["file_key"], case["page_id"], case["node_id"]))
        if not NODE_ID.fullmatch(case["node_id"]):
            raise ValueError(f"Invalid Figma frame ID: {case['node_id']}")
        for path in case["implementation_paths"]:
            relative_path(path)
        for artifact in case["artifacts"].values():
            relative_path(artifact["path"])
            if not re.fullmatch(r"[a-f0-9]{64}", artifact["sha256"]):
                raise ValueError("Every design artifact needs its SHA256")
            previous = paths.setdefault(artifact["path"], artifact["sha256"])
            if previous != artifact["sha256"]:
                raise ValueError("Conflicting design artifact hashes")
    expected_states = set()
    frame_set = set(inventory.screen_frames(body, root))
    for file in body["files"]:
        state_identities = set()
        for row in file["screen_states"]:
            frame = (file["key"], row["page_id"], row["node_id"])
            if frame not in frame_set:
                raise ValueError(f"Screen state references an unknown Figma frame: {frame}")
            if not row["state"].strip():
                raise ValueError(f"Screen state needs a name: {frame}")
            viewport = row["viewport"]
            for number in viewport.values():
                if type(number) not in (int, float) or not math.isfinite(number) or number <= 0:
                    raise ValueError(f"Invalid screen-state viewport: {frame}")
            identity = (
                file["key"],
                row["page_id"],
                row["node_id"],
                row["state"],
                viewport["width"],
                viewport["height"],
                viewport["device_scale_factor"],
            )
            if identity in state_identities:
                raise ValueError(f"Duplicate source screen state: {identity}")
            state_identities.add(identity)
            expected_states.add(identity)
    discovered = frame_set
    if covered != discovered:
        raise ValueError(
            f"Figma source frames and cases differ; missing cases: {sorted(discovered - covered)}; "
            f"unknown cases: {sorted(covered - discovered)}"
        )
    if identities != expected_states:
        raise ValueError(
            f"Figma source states and cases differ; missing cases: {sorted(expected_states - identities)}; "
            f"unknown cases: {sorted(identities - expected_states)}"
        )
    cases = {case["id"]: case for case in body["cases"]}
    target_ids = set()
    for target in body["responsive_targets"]:
        if target["id"] in target_ids or target["source_case_id"] not in cases:
            raise ValueError("Responsive targets need unique IDs and an existing source case")
        target_ids.add(target["id"])
        for number in target["viewport"].values():
            if type(number) not in (int, float) or not math.isfinite(number) or number <= 0:
                raise ValueError("Responsive target viewport must be finite and positive")
        source = cases[target["source_case_id"]]
        matches = {
            case["id"]
            for case in cases.values()
            if case["route"] == source["route"]
            and case["state"] == source["state"]
            and case["viewport"] == target["viewport"]
        }
        if (
            target["reference_case_id"]
            and target["reference_case_id"] not in matches
            or not target["reference_case_id"]
            and matches
        ):
            raise ValueError(
                "Responsive target must name its supplied exact reference or explicitly record its absence"
            )


def png_dimensions(path):
    with Path(path).open("rb") as stream:
        header = stream.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ValueError(f"Expected a PNG capture: {path}")
    return struct.unpack(">II", header[16:24])


def verify(record):
    body, root = record["body"], Path(record["root"]).resolve()
    validate(body, root=root)
    if record["manifest_hash"] != util.digest(body):
        raise ValueError("Saved design manifest identity changed")
    if record.get("manifest_path"):
        index = Path(record["manifest_path"])
        if index.is_symlink() or not index.resolve().is_relative_to(root) or _read_json(index) != body:
            raise ValueError("Retained design inventory index is missing or changed")
    for artifact in all_artifacts(body):
        path = root / artifact["path"]
        if (
            path.is_symlink()
            or not path.resolve().is_relative_to(root)
            or not path.is_file()
            or util.file_hash(path) != artifact["sha256"]
            or path.stat().st_size == 0
        ):
            raise ValueError(f"Missing or changed design reference: {artifact['path']}")
    for case in body["cases"]:
        dimensions = case.get("native_size", case["viewport"])
        expected = tuple(round(dimensions[key] * case["export_scale"]) for key in ("width", "height"))
        if png_dimensions(root / case["artifacts"]["screenshot"]["path"]) != expected:
            raise ValueError(f"Reference export dimensions disagree with viewport/export_scale: {case['id']}")
    return record


def all_artifacts(body):
    """Every immutable file referenced by either manifest version."""
    if body["version"] == 1:
        for case in body["cases"]:
            yield from case["artifacts"].values()
        return
    for file in body["files"]:
        for page in file["pages"]:
            yield page["metadata_xml"]
            yield page["source_json"]
        yield file["metadata_xml"]
        for row in (*file["fonts"], *file["assets"]):
            if row["status"] == "available":
                yield row["artifact"]
    for case in body["cases"]:
        yield from case["artifacts"].values()


def _read_json(path):
    try:
        return util.read_object(path)
    except RuntimeError as error:
        raise ValueError(f"Unreadable Figma inventory JSON: {path}") from error


def load(path):
    path = Path(path).resolve()
    body = _read_json(path)
    return verify({"body": body, "manifest_hash": util.digest(body), "root": str(path.parent)})


def retain(record, workspace):
    """Retain ignored/external exports inside the new task's private input bundle."""
    verify(record)
    if any(
        artifact["path"] == "inventory-manifest.json" or artifact["path"].startswith("inventory-manifest.json/")
        for artifact in all_artifacts(record["body"])
    ):
        raise ValueError("inventory-manifest.json is reserved for the retained inventory index")
    parent = Path(workspace).resolve() / ".autocode" / "design-inputs"
    parent.mkdir(parents=True, exist_ok=True)
    destination = parent / record["manifest_hash"]
    retained = {
        **copy.deepcopy(record),
        "root": str(destination),
        "manifest_path": str(destination / "inventory-manifest.json"),
    }
    if destination.exists():
        verify({**record, "root": str(destination), "manifest_path": None})
        index = Path(retained["manifest_path"])
        if not index.exists():
            util.atomic_json(index, record["body"])  # add an index for a legacy retained bundle
        return verify(retained)  # never replace a previously retained reference
    temporary = Path(tempfile.mkdtemp(prefix=".design-", dir=parent))
    try:
        for artifact in all_artifacts(record["body"]):
            target = temporary / artifact["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copyfile(Path(record["root"]) / artifact["path"], target)
        util.atomic_json(temporary / "inventory-manifest.json", record["body"])
        verify({**record, "root": str(temporary), "manifest_path": str(temporary / "inventory-manifest.json")})
        temporary.rename(destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return verify(retained)


def context(settings, *, stage=None, current_task=None):
    record = settings.get("design_manifest")
    if not record:
        return None
    try:
        verify(record)
    except (ValueError, OSError) as error:
        raise util.Paused("PAUSED_DESIGN_REFERENCE", str(error)) from error
    result = copy.deepcopy(record)
    if record["body"]["version"] == 2:
        result["catalog"] = inventory.catalog(record["body"], record["root"])
        result["inventory_instruction"] = INSTRUCTION_V2
        if stage in ("terra", "sol"):
            all_cases = copy.deepcopy(record["body"]["cases"])
            affected_paths = (current_task or {}).get("affected_paths", [])
            projection = inventory.builder_slice(record["body"], record["root"], affected_paths)
            # Builder receives only cases and inventory linked to its assigned source paths.
            # The full hash-bound body remains retained for planning and independent validation.
            result = {
                "version": 2,
                "manifest_hash": record["manifest_hash"],
                "bundle_root": record["root"],
                "full_manifest": record.get("manifest_path"),
                "case_roster": [{key: case[key] for key in ("id", "route", "state")} for case in all_cases],
                "catalog": projection["catalog"],
                "builder_scope": {
                    key: projection[key] for key in ("affected_paths", "case_ids", "unmapped_affected_paths")
                },
                "inventory_instruction": INSTRUCTION_V2,
            }
    return result


def blockers(record):
    if record["body"]["version"] != 2:
        return []
    return inventory.blockers(record["body"])


INSTRUCTION = prompts.get("fragments/design-manifest/instruction.md")

INSTRUCTION_V2 = prompts.get("fragments/design-manifest/instruction-v2.md")
