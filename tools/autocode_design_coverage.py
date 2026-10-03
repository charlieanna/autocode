"""Report coverage and completion obligations for a retained design inventory.

Checks coverage, identities and evidence files, not pixel comparison or browser
capture provenance; those require the separate visual gate.
"""
from __future__ import annotations
import copy
from pathlib import Path
try:
    from . import autocode_design_manifest as manifest, autocode_util as util
except ImportError:
    import autocode_design_manifest as manifest, autocode_util as util


RESULT = manifest.obj({
    "id": manifest.TEXT,
    "status": {"type": "string", "enum": ["PASS", "FAIL", "NOT_VERIFIED"]},
    "criterion_ids": {"type": "array", "items": manifest.TEXT},
    "candidate_ref": {"type": "string"}, "comparison_ref": {"type": "string"},
})


def extend_schema(schema, state, stage):
    record = state.get("settings", {}).get("design_manifest")
    if not record or stage not in ("sol", "astra_checkpoint"):
        return schema
    schema = copy.deepcopy(schema)
    slot = schema if stage == "sol" else schema.get("properties", {}).get("validation")
    if not slot:
        return schema
    result = copy.deepcopy(RESULT)
    result["properties"]["id"] = {**result["properties"]["id"],
                                   "enum": [case["id"] for case in record["body"]["cases"]]}
    slot["properties"].update(
        design_manifest_hash={"type": "string", "enum": [record["manifest_hash"]]},
        design_results={"type": "array", "items": result})
    slot["required"] = list(dict.fromkeys([*slot["required"], "design_manifest_hash", "design_results"]))
    return schema


def report_refs(state, report, *, stage=None):
    """Pin design evidence from a report. Non-design stages may omit it.

    Independent sol/checkpoint reports must account for every case (enforced at
    decode and here). Builder self_check is criteria self-evidence and does not
    carry design_results; that is not a mismatched manifest.
    """
    record = state.get("settings", {}).get("design_manifest")
    if not record:
        return []
    if (stage not in ("sol", "astra_checkpoint")
            and not report.get("design_manifest_hash") and not report.get("design_results")):
        return []
    manifest.verify(record)
    if report.get("design_manifest_hash") != record["manifest_hash"]:
        raise ValueError("Validation refers to a different design manifest")
    rows = report.get("design_results", [])
    util.validate_schema(rows, {"type": "array", "items": RESULT})
    ids = [row["id"] for row in rows]
    cases = {case["id"]: case for case in record["body"]["cases"]}
    if len(ids) != len(set(ids)) or set(ids) != set(cases):
        raise ValueError("Independent validation must account for every design case exactly once")
    criteria = {row["id"] for row in state.get("acceptance_criteria", [])}
    outcomes = {row["id"]: row["status"] for row in report.get("criterion_results", [])}
    root = Path(state["workspace"]).resolve()
    references = {str((Path(record["root"]) / artifact["path"]).resolve())
                  for case in cases.values() for artifact in case["artifacts"].values()}
    refs = []
    for row in rows:
        mapped = row["criterion_ids"]
        if len(mapped) != len(set(mapped)) or not set(mapped) <= criteria:
            raise ValueError(f"Design case has unknown/duplicate criterion IDs: {row['id']}")
        if row["status"] != "PASS":
            continue
        if not mapped or any(outcomes.get(cid) != "PASS" for cid in mapped):
            raise ValueError(f"Design PASS needs passing mapped criteria: {row['id']}")
        paths = []
        for key in ("candidate_ref", "comparison_ref"):
            path = Path(row[key])
            path = (path if path.is_absolute() else root / path).resolve()
            if (not row[key].strip() or not path.is_relative_to(root) or not path.is_file()
                    or path.stat().st_size == 0 or str(path) in references):
                raise ValueError(f"Design PASS lacks independent project evidence: {row['id']} / {key}")
            paths.append(path)
        if paths[0] == paths[1]:
            raise ValueError("Candidate and comparison artifacts must be distinct")
        viewport = cases[row["id"]]["viewport"]
        expected = tuple(round(viewport[key] * viewport["device_scale_factor"]) for key in ("width", "height"))
        if manifest.png_dimensions(paths[0]) != expected:
            raise ValueError(f"Candidate PNG has the wrong CSS viewport/device scale: {row['id']}")
        refs.extend(str(path) for path in paths)
    return list(dict.fromkeys(refs))


def gaps(state):
    """Saved coverage only; status does not claim current visual acceptance."""
    record = state.get("settings", {}).get("design_manifest")
    if not record:
        return []
    report = state.get("validation") or {}
    rows = report.get("design_results", [])
    passed = {row.get("id") for row in rows if row.get("status") == "PASS"}
    if report.get("design_manifest_hash") != record["manifest_hash"]:
        passed = set()
    return [case["id"] for case in record["body"]["cases"] if case["id"] not in passed]


def ready(state):
    if not state.get("settings", {}).get("design_manifest"):
        return True
    try:
        refs = report_refs(state, state.get("validation") or {})
        pins = (state.get("validation") or {}).get("evidence_hashes") or {}
        return not gaps(state) and all(pins.get(path) == util.file_hash(path) for path in refs)
    except (ValueError, OSError, KeyError, TypeError):
        return False


def projection(state):
    record = state.get("settings", {}).get("design_manifest")
    if not record:
        return None
    return {"manifest_hash": record["manifest_hash"], "files": copy.deepcopy(record["body"]["files"]),
            "case_ids": [case["id"] for case in record["body"]["cases"]],
            "not_passing": gaps(state), "reported_source_revision": (state.get("validation") or {}).get("source_revision"),
            "current_visual_acceptance": None}
