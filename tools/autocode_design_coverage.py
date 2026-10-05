"""Report coverage and completion obligations for a retained design inventory.

Checks coverage and capture provenance. Pixel comparison remains the independent
reviewer's obligation; a valid capture receipt never supplies a PASS verdict.
"""
from __future__ import annotations
import copy
from pathlib import Path
try:
    from . import autocode_design_manifest as manifest, autocode_util as util, autocode_visual_evidence as visual, autocode_design_plan as design_plan
except ImportError:
    import autocode_design_manifest as manifest, autocode_util as util, autocode_visual_evidence as visual, autocode_design_plan as design_plan


RESULT = manifest.obj({
    "id": manifest.TEXT,
    "status": {"type": "string", "enum": ["PASS", "FAIL", "NOT_VERIFIED"]},
    "criterion_ids": {"type": "array", "items": manifest.TEXT},
    "candidate_ref": {"type": "string"}, "comparison_ref": {"type": "string"},
    "capture_ref": {"type": "string"}, "capture_sha256": {"type": "string"},
})


def extend_schema(schema, state, stage):
    record = state.get("settings", {}).get("design_manifest")
    if not visual.reference_hash(state.get('settings', {})) or stage not in ("sol", "astra_checkpoint"):
        return schema
    schema = copy.deepcopy(schema)
    slot = schema if stage == "sol" else schema.get("properties", {}).get("validation")
    if not slot:
        return schema
    if not record:
        slot['properties']['implementation_captures'] = {'type': 'array', 'items': manifest.obj({
            'capture_ref': manifest.TEXT, 'capture_sha256': manifest.TEXT})}
        slot['required'] = list(dict.fromkeys([*slot['required'], 'implementation_captures']))
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
        return visual.native_refs(state, report) if stage in (None, 'sol', 'astra_checkpoint') else []
    if (stage not in ("sol", "astra_checkpoint")
            and not report.get("design_manifest_hash") and not report.get("design_results")):
        return []
    manifest.verify(record)
    plan_body = (state.get("goal_contract") or {}).get("body") or {}
    design_plan.validate(record, plan_body, ready=True)
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
    refs, candidates = [], set()
    current = util.snapshot(root) if any(row['status'] == 'PASS' for row in rows) else None
    for row in rows:
        mapped = row["criterion_ids"]
        if len(mapped) != len(set(mapped)) or not set(mapped) <= criteria:
            raise ValueError(f"Design case has unknown/duplicate criterion IDs: {row['id']}")
        if record["body"]["version"] == 2 and set(mapped) != set(design_plan.case_criteria(plan_body)[row["id"]]):
            raise ValueError(f"Design report must retain the approved case-to-criterion mapping: {row['id']}")
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
        if not row['capture_ref'] or not row['capture_sha256']:
            raise ValueError(f"Design PASS needs current capture provenance: {row['id']}")
        captured, capture_refs = visual.verify(state, row['capture_ref'], row['capture_sha256'],
                                               case=cases[row['id']], current=current)
        if paths[0] != root / captured['artifacts']['candidate']['path']:
            raise ValueError('Visual review cited a different image from the bound capture')
        candidates.add(str(paths[0]))
        refs.extend(capture_refs)
        refs.extend(str(path) for path in paths)
    visual.require_current_image_citations(state, report, candidates, references)
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
    passed.update(row["id"] for row in reusable_results(state) if row["status"] == "PASS")
    return [case["id"] for case in record["body"]["cases"] if case["id"] not in passed]


def reusable_results(state):
    if not state.get('design_input_changes'):
        return []
    record = (state.get('settings') or {}).get('design_manifest') or {}
    cases = {row['id']:row for row in record.get('body',{}).get('cases',[])}
    rows = {}
    current = None
    for change in state['design_input_changes']:
        previous = change['previous_validation']
        pins = previous.get('evidence_hashes') or {}
        for row in previous.get('design_results',[]):
            if row['id'] not in cases or row['status'] != 'PASS':
                continue
            try:
                current = current or util.snapshot(state['workspace'])
                _, refs = visual.verify(state,row['capture_ref'],row['capture_sha256'],case=cases[row['id']],current=current)
                refs += [str(Path(state['workspace']) / row['comparison_ref']) if not Path(row['comparison_ref']).is_absolute() else row['comparison_ref']]
                if not all(pins.get(path) == util.file_hash(path) for path in refs):
                    continue
                rows[row['id']] = copy.deepcopy(row)
            except (ValueError,OSError,KeyError,TypeError):
                continue
    return list(rows.values())


def ready(state):
    if not visual.reference_hash(state.get('settings', {})):
        return True
    try:
        record = (state.get("settings") or {}).get("design_manifest")
        if record and manifest.blockers(record):
            return False
        refs = report_refs(state, state.get("validation") or {})
        pins = (state.get("validation") or {}).get("evidence_hashes") or {}
        return not gaps(state) and all(pins.get(path) == util.file_hash(path) for path in refs)
    except (ValueError, OSError, KeyError, TypeError):
        return False


def projection(state):
    record = state.get("settings", {}).get("design_manifest")
    if not record:
        intake = state.get("design_intake")
        return {"intake": copy.deepcopy(intake), "current_visual_acceptance": None} if intake else None
    result = {"manifest_hash": record["manifest_hash"], "files": copy.deepcopy(record["body"]["files"]),
              "case_ids": [case["id"] for case in record["body"]["cases"]],
              "not_passing": gaps(state), "reported_source_revision": (state.get("validation") or {}).get("source_revision"),
              "current_visual_acceptance": None,
              "reference_changes": [{key:copy.deepcopy(row.get(key)) for key in ("at","reason","previous_hash","current_hash","coverage_change","previous_references","current_references")}
                                    for row in state.get("design_input_changes",[])],
              "reusable_case_results": reusable_results(state)}
    if record["body"].get("version") == 2:
        try:
            manifest.verify(record)
            result["inventory"] = manifest.inventory.catalog(record["body"], record["root"])
            result["inventory_blockers"] = manifest.blockers(record)
        except (ValueError, OSError, KeyError, TypeError) as error:
            result["inventory_error"] = str(error)
            result["inventory_blockers"] = [str(error)]
            result["not_passing"] = list(result["case_ids"])
            result["reusable_case_results"] = []
        result["plan_coverage"] = copy.deepcopy(((state.get("goal_contract") or {}).get("body") or {}).get("design_coverage"))
    return result
