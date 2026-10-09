"""Design coverage inside the existing, hashed product contract.

No separate acceptance authority or run-state writes. The goal lifecycle checks
this mapping before installing/approving a plan; report coverage checks it again.
"""
from __future__ import annotations
import json

try:
    from . import autocode_design_manifest as manifest, autocode_util as util
except ImportError:
    import autocode_design_manifest as manifest, autocode_util as util


CASE = manifest.obj({"id": manifest.TEXT, "criterion_ids": manifest.TEXTS,
                     "milestone_ids": manifest.TEXTS})
DERIVED = manifest.obj({"target_id": manifest.TEXT, "behavior": manifest.TEXT,
                        "basis": {"type": "string", "enum": ["approved_constraints", "derived_behavior"]},
                        "exact_match": {"type": "boolean", "enum": [False]},
                        "criterion_ids": manifest.TEXTS, "milestone_ids": manifest.TEXTS})
SCHEMA = manifest.obj({"manifest_hash": manifest.TEXT,
                       "cases": {"type": "array", "items": CASE},
                       "responsive_derivations": {"type": "array", "items": DERIVED}})


def body_schema(schema, record):
    if not record or record["body"]["version"] != 2:
        return schema
    from copy import deepcopy
    result = deepcopy(schema)
    result["properties"]["design_coverage"] = {"anyOf": [SCHEMA, {"type": "null"}]}
    return result


def report_schema(schema, record):
    if not record or record["body"]["version"] != 2 or "contract" not in schema.get("properties", {}):
        return schema
    from copy import deepcopy
    result = deepcopy(schema)
    result["properties"]["contract"] = body_schema(result["properties"]["contract"], record)
    return result


def _ownership(row, body):
    criteria = {criterion["id"] for criterion in body["acceptance_criteria"]}
    milestones = {milestone["id"]: milestone for milestone in body.get("milestones", [])}
    for key in ("criterion_ids", "milestone_ids"):
        if len(row[key]) != len(set(row[key])):
            raise ValueError(f"Design coverage repeats {key}: {row.get('id', row.get('target_id'))}")
    if not set(row["criterion_ids"]) <= criteria or not set(row["milestone_ids"]) <= set(milestones):
        raise ValueError("Design coverage names unknown product criteria or milestones")
    covered = {criterion for identity in row["milestone_ids"]
               for criterion in milestones[identity]["acceptance_criteria"]}
    if not set(row["criterion_ids"]) <= covered:
        raise ValueError("Design ownership milestones do not own its mapped product criteria")
    return [path for identity in row["milestone_ids"] for path in milestones[identity].get("affected_paths", [])]


def _validate_visual_declaration(body, coverage):
    """Keep the visual-runtime declaration bound to the same approved case mapping."""
    marker = "VISUAL_CASE_CRITERIA="
    rows = [row[len(marker):] for row in body.get("constraints", [])
            if isinstance(row, str) and row.startswith(marker)]
    if not rows:
        return
    if len(rows) != 1:
        raise ValueError("Design coverage needs exactly one visual case declaration when supplied")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Visual case declaration repeats a case: " + key)
            result[key] = value
        return result
    declared = json.loads(rows[0], object_pairs_hook=unique)
    expected = {row["id"]: row["criterion_ids"] for row in coverage["cases"]}
    if (not isinstance(declared, dict) or set(declared) != set(expected)
            or any(not isinstance(mapped, list)
                   or not all(isinstance(cid, str) for cid in mapped)
                   or len(mapped) != len(set(mapped)) or set(mapped) != set(expected[identity])
                   for identity, mapped in declared.items())):
        raise ValueError("Visual case declaration conflicts with structured design coverage")


def validate(record, body, *, ready=False):
    if not record or record["body"]["version"] != 2:
        return
    coverage = body.get("design_coverage")
    if not coverage and body.get("open_blocking_questions") and not ready:
        return  # Clarification can precede a complete implementation plan.
    if not coverage:
        raise ValueError("Version 2 Figma inventory needs design_coverage in the product plan before approval")
    util.validate_schema(coverage, SCHEMA)
    manifest.verify(record)
    if coverage["manifest_hash"] != record["manifest_hash"]:
        raise ValueError("Plan design coverage belongs to a different reference version; review the new inputs")
    cases = {case["id"]: case for case in record["body"]["cases"]}
    identities = [row["id"] for row in coverage["cases"]]
    if len(identities) != len(set(identities)) or set(identities) != set(cases):
        raise ValueError("Product plan must map every approved design case exactly once")
    for row in coverage["cases"]:
        paths = _ownership(row, body)
        if not all(any(manifest.inventory.path_intersects(path, owned) for owned in paths)
                   for path in cases[row["id"]]["implementation_paths"]):
            raise ValueError(f"Design case has no milestone owning its implementation paths: {row['id']}")
    _validate_visual_declaration(body, coverage)
    targets = {target["id"]: target for target in record["body"].get("responsive_targets", [])
               if not target["reference_case_id"]}
    derived_ids = [row["target_id"] for row in coverage["responsive_derivations"]]
    if len(derived_ids) != len(set(derived_ids)) or set(derived_ids) != set(targets):
        raise ValueError("Plan must document every responsive target without a supplied reference exactly once")
    for row in coverage["responsive_derivations"]:
        _ownership(row, body)
        expected = "approved_constraints" if targets[row["target_id"]]["constraints"] else "derived_behavior"
        if row["basis"] != expected or not row["behavior"].strip():
            raise ValueError("Absent responsive references need the actual constraints or explicit derived behavior")


def case_criteria(body):
    return {row["id"]: list(row["criterion_ids"])
            for row in (body.get("design_coverage") or {}).get("cases", [])}


def render(record, body):
    if not record or record["body"]["version"] != 2:
        return []
    coverage = body.get("design_coverage") or {}
    mapped = {row["id"]: row for row in coverage.get("cases", [])}
    lines = ["", "Figma coverage (reference " + record["manifest_hash"][:12] + "):"]
    for case in record["body"]["cases"]:
        row = mapped.get(case["id"])
        owner = (", ".join(row["milestone_ids"]) + "; criteria " + ", ".join(row["criterion_ids"])) if row else "UNMAPPED"
        viewport = case["viewport"]
        lines.append(f"  {case['id']}: {case['file_key']}/{case['node_id']} · {case['route']} · "
                     f"{case['state']} · {viewport['width']}×{viewport['height']} · {owner}")
    for row in coverage.get("responsive_derivations", []):
        lines.append(f"  {row['target_id']}: derived responsive behavior ({row['basis']}): {row['behavior']}. "
                     "No exact reference match is claimed.")
    lines.extend("  Reference blocker: " + blocker for blocker in manifest.blockers(record))
    return lines
