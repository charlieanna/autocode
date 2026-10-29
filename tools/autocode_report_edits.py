"""Bounded edits to a pinned rejected planning report, before full validation.

This is an optional wire format, not a report validator or a source of authority.
The caller retains the raw response and validates the reconstructed complete report.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

try:
    from . import autocode_report_text_edits as text_edits
    from .autocode_util import atomic_json, file_hash, validate_schema
except ImportError:
    import autocode_report_text_edits as text_edits
    from autocode_util import atomic_json, file_hash, validate_schema

KEY = "repair_report"
META = "x-autocode-report-edits"
STAGES = frozenset({"requirements_gather", "astra_discovery", "glm_revise"})
RULE = (
    "BOUNDED REPORT EDITS. When the output schema offers repair_report, prefer that form for a small correction: "
    "return only repair_report with the exact rejected_report.sha256 and up to 32 edits. Each edit has op, path "
    "and value (null for remove). Paths are nonempty arrays of object keys or existing array indices. "
    "Use add only for an absent object field; remove only an existing object field; replace only an existing "
    "field/item. Do not edit overlapping paths or shift arrays. The runner applies edits to the complete "
    "pinned rejected_report.content and then runs every ordinary schema, provenance, protected-contract and "
    "evidence check. This does not authorize changed requirements, approvals or invented execution. "
    "A complete corrected report is still accepted. Earlier instructions to return a complete report also "
    "permit this exact-bound edit form; it retains every unedited value.\n"
)


def eligible(stage, source):
    return bool(
        stage in STAGES
        and not source.get("truncated")
        and (
            source.get("format") == "json"
            and isinstance(source.get("content"), dict)
            and KEY not in source["content"]
            or text_edits.eligible(source)
        )
        and isinstance(source.get("sha256"), str)
        and len(source["sha256"]) == 64
    )


def instruction(stage, source):
    if not eligible(stage, source):
        return ""
    return text_edits.RULE if source.get("format") == "text" else RULE


def validation_schema(schema):
    if META not in schema:
        return schema
    original = copy.deepcopy(schema)
    metadata = original.pop(META)
    original["properties"].pop(KEY)
    if metadata["required"] is None:
        original.pop("required", None)
    else:
        original["required"] = metadata["required"]
    return original


def prepare(schema_path, source, stage):
    """Save an offer beside its pinned input; old/full-report schemas stay usable."""
    if not eligible(stage, source):
        return schema_path
    original = validation_schema(json.loads(Path(schema_path).read_text()))
    if (
        original.get("type") != "object"
        or not isinstance(original.get("properties"), dict)
        or KEY in original["properties"]
    ):
        return schema_path
    path = {
        "type": "array",
        "minItems": 1,
        "maxItems": 32,
        "items": {"anyOf": [{"type": "string"}, {"type": "integer", "minimum": 0}]},
    }
    edit = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"op": {"type": "string", "enum": ["add", "remove", "replace"]}, "path": path, "value": {}},
        "required": ["op", "path", "value"],
    }
    text_mode = source.get("format") == "text"
    field = "text_edits" if text_mode else "edits"
    operations = text_edits.schema() if text_mode else {"type": "array", "minItems": 1, "maxItems": 32, "items": edit}
    patch = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "sha256": {"type": "string", "const": source["sha256"]},
            field: operations,
        },
        "required": ["sha256", field],
    }
    offer = copy.deepcopy(original)
    offer["properties"][KEY] = patch
    offer["required"] = []  # either full report or edits; full validation follows reconstruction
    offer[META] = {
        "required": original.get("required"),
        "source_path": source["path"],
        "sha256": source["sha256"],
        "stage": stage,
        "format": source["format"],
    }
    target = Path(source["path"]).with_name(Path(source["path"]).stem + ".repair-edits.schema.json")
    atomic_json(target, offer)
    return target


def _parent(root, path):
    current = root
    for part in path[:-1]:
        if (
            isinstance(current, dict)
            and isinstance(part, str)
            and part in current
            or isinstance(current, list)
            and type(part) is int
            and 0 <= part < len(current)
        ):
            current = current[part]
        else:
            raise ValueError("Report edit parent path does not exist")
    return current, path[-1]


def apply(base, edits):
    """Apply disjoint edits without index shifting or coercing keys and indices."""
    result = copy.deepcopy(base)
    seen = []
    for edit in edits:
        path, op, value = edit["path"], edit["op"], edit["value"]
        if not path or any(type(part) not in (str, int) or type(part) is int and part < 0 for part in path):
            raise ValueError("Report edits require a nonempty typed path")
        if any(path[: len(old)] == old or old[: len(path)] == path for old in seen):
            raise ValueError("Report edit paths overlap")
        seen.append(path)
        parent, key = _parent(result, path)
        if isinstance(parent, dict) and isinstance(key, str):
            exists = key in parent
            if (op == "add" and exists) or (op in ("remove", "replace") and not exists):
                raise ValueError("Report edit operation does not match field existence")
            if op == "remove":
                if value is not None:
                    raise ValueError("Report remove edit must have null value")
                del parent[key]
            elif op in ("add", "replace"):
                parent[key] = copy.deepcopy(value)
            else:
                raise ValueError("Unknown report edit operation")
        elif isinstance(parent, list) and type(key) is int and 0 <= key < len(parent) and op == "replace":
            parent[key] = copy.deepcopy(value)
        else:
            raise ValueError("Report edit target is invalid; array insertion/removal is forbidden")
    return result


def expand(value, record):
    """Expand only an offered, source-bound response; caller validates all semantics."""
    if not isinstance(value, dict) or KEY not in value:
        return value
    schema = json.loads(Path(record["schema"]).read_text())
    meta = schema.get(META)
    if not record.get("report_only") or not isinstance(meta, dict) or meta.get("stage") not in STAGES:
        raise ValueError("Report edits were not offered for this attempt")
    if str(record.get("stage", "")).removesuffix("_report_repair") != meta["stage"]:
        raise ValueError("Report edits belong to a different stage")
    if set(value) != {KEY}:
        raise ValueError("Report edits cannot be mixed with a full report")
    validate_schema(value[KEY], schema["properties"][KEY])
    source = Path(meta["source_path"])
    if source.is_symlink() or not source.is_file() or file_hash(source) != meta["sha256"]:
        raise ValueError("Pinned rejected report changed before applying edits")
    if value[KEY]["sha256"] != meta["sha256"]:
        raise ValueError("Report edits do not name the exact rejected source")
    text = source.read_text()
    if meta.get("format") == "text":
        if not text_edits.eligible({"format": "text", "content": text}):
            raise ValueError("Text edits require a complete malformed JSON source")
        result = text_edits.apply(text, value[KEY]["text_edits"])
        if KEY in result:
            raise ValueError("Text edits cannot reconstruct another repair envelope")
        return result
    base = json.loads(text)
    if not isinstance(base, dict) or KEY in base:
        raise ValueError("Report edits do not name the exact rejected object")
    return apply(base, value[KEY]["edits"])
