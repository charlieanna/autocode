"""Bind independently reviewed changed-line reasons to the exact offered proof.

Call bind only after ordinary source, report identity and executed-check admission.
verified_refs maps model references to references already authenticated and pinned
by the caller. This module never treats model prose or a file name as evidence.
"""

from __future__ import annotations

import copy

try:
    from . import autocode_changed_code_evidence as line_evidence
except ImportError:
    import autocode_changed_code_evidence as line_evidence

FIELD = "changed_code_exceptions"
KINDS = ("defensive_unreachable", "instrumentation_unavailable", "equivalent_mutant")
SCHEMA = {
    "type": ["array", "null"],
    "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["gap_id", "decision", "reason_kind", "reason", "evidence_refs"],
        "properties": {
            "gap_id": {"type": "string"},
            "decision": {"type": "string", "enum": ["accept", "reject"]},
            "reason_kind": {"type": "string", "enum": list(KINDS)},
            "reason": {"type": "string"},
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
        },
    },
}


def _stage(record):
    return str(record.get("original_stage") or record.get("stage", "")).removesuffix("_report_repair")


def _fields(schema, stage):
    fields = schema.get("properties", {})
    return fields.get("validation", {}).get("properties", {}) if stage == "astra_checkpoint" else fields


def _gaps(assessment):
    if not isinstance(assessment, dict) or not isinstance(assessment.get("obligations"), list):
        return {}
    result = {}
    for row in assessment["obligations"]:
        if not isinstance(row, dict) or row.get("status") != "reason_required":
            continue
        identity = row.get("gap_id")
        if not isinstance(identity, str) or not identity or identity in result:
            return {}
        result[identity] = row
    return result


def generation_schema(schema, assessment, stage):
    """Offer only current gaps to an independent Tester; never mutate a shared schema."""
    result = copy.deepcopy(schema)
    if stage == "terra":
        # The Builder's final-only self-assessment borrows the Tester shape but
        # cannot independently approve its own reasons.
        own = result.get("properties", {}).get("continuation", {}).get("properties", {}).get("self_assessment", {})
        own.get("properties", {}).pop(FIELD, None)
        if "required" in own:
            own["required"] = [key for key in own["required"] if key != FIELD]
        return result
    if stage not in ("sol", "astra_checkpoint"):
        return result
    field = _fields(result, stage).get(FIELD)
    if not isinstance(field, dict):
        return result
    ids = list(_gaps(assessment))
    field["maxItems"] = len(ids)
    if ids:
        props = field["items"]["properties"]
        props["gap_id"] = {**props["gap_id"], "enum": ids}
    return result


def normalize_absent(value, schema, record):
    """Legacy omissions mean no decision; supplied decisions remain untouched."""
    stage = _stage(record)
    if stage not in ("sol", "astra_checkpoint"):
        return value
    field = _fields(schema, stage).get(FIELD, {})
    kind = field.get("type")
    if not isinstance(kind, list) or "array" not in kind or "null" not in kind:
        return value
    result = copy.deepcopy(value)
    target = result.get("validation") if stage == "astra_checkpoint" else result
    if isinstance(target, dict):
        target.setdefault(FIELD, None)
    return result


def bind(assessment, decisions, record, *, offered_schema, verified_refs):
    """Return explicit, source-bound review decisions for the domain assessor.

    A nullable/absent field grants nothing. Accepted rows alone can satisfy a gap;
    rejected rows remain available for disclosure. Evidence aliases resolve only
    through the caller's authenticated, pinned reference map. The saved generation
    schema must have offered the exact current gap, including its source/context
    identity. Builder self-checks and completion prose never create authority.
    """
    if decisions is None or decisions == []:
        return []
    if not isinstance(decisions, list):
        raise ValueError("Changed-code exception decisions must be an array or null")
    stage = _stage(record)
    if (stage, record.get("role")) not in (("sol", "sol"), ("astra_checkpoint", "astra")):
        raise ValueError("Changed-code reasons require an independent Tester report")
    if not isinstance(assessment, dict) or (
        not assessment.get("source_revision")
        or not assessment.get("proof_context")
        or record.get("source_revision") != assessment["source_revision"]
        or record.get("changed_files") != []
        or not isinstance(record.get("output"), str)
        or not record["output"]
    ):
        raise ValueError("Changed-code reason review does not match a read-only current source")
    field = _fields(offered_schema, stage).get(FIELD, {})
    offered = field.get("items", {}).get("properties", {}).get("gap_id", {}).get("enum")
    gaps = _gaps(assessment)
    if (
        not isinstance(offered, list)
        or not offered
        or len(offered) != len(set(offered))
        or field.get("maxItems") != len(offered)
        or set(offered) != set(gaps)
        or not isinstance(verified_refs, dict)
    ):
        raise ValueError("Changed-code reason review has no exact current offered gap binding")
    result, seen = [], set()
    for row in decisions:
        if not isinstance(row, dict) or set(row) != set(SCHEMA["items"]["properties"]):
            raise ValueError("Malformed changed-code exception decision")
        identity = row.get("gap_id")
        if not isinstance(identity, str) or identity not in gaps or identity in seen:
            raise ValueError("Changed-code reason names a duplicate, unknown or stale gap")
        seen.add(identity)
        reason, refs = row.get("reason"), row.get("evidence_refs")
        if (
            row.get("decision") not in ("accept", "reject")
            or row.get("reason_kind") not in KINDS
            or not isinstance(reason, str)
            or not reason.strip()
            or not isinstance(refs, list)
            or not refs
            or any(not isinstance(ref, str) or not ref.strip() for ref in refs)
        ):
            raise ValueError("Changed-code reason requires an explicit judgment, reason and evidence")
        evidence = []
        for ref in refs:
            actual = verified_refs.get(ref)
            if not isinstance(actual, str) or not actual.strip():
                raise ValueError(f"Changed-code reason cites unauthenticated evidence: {ref!r}")
            if actual not in evidence:
                evidence.append(actual)
        gap = gaps[identity]
        if (
            not isinstance(gap.get("path"), str)
            or not gap["path"]
            or not isinstance(gap.get("sha256"), str)
            or len(gap["sha256"]) != 64
            or type(gap.get("line")) is not int
            or gap["line"] < 1
            or line_evidence.gap_id(
                assessment["source_revision"], assessment["proof_context"], gap["path"], gap["sha256"], gap["line"]
            )
            != identity
        ):
            raise ValueError("Changed-code reason gap lacks its exact file and line binding")
        result.append(
            {
                "gap_id": identity,
                "source_revision": assessment["source_revision"],
                "proof_context": assessment["proof_context"],
                "path": gap["path"],
                "sha256": gap["sha256"],
                "line": gap["line"],
                "accepted": row["decision"] == "accept",
                "reason_kind": row["reason_kind"],
                "reason": reason.strip(),
                "evidence": evidence,
                "reviewer": {"stage": stage, "role": record["role"], "output": record["output"]},
            }
        )
    return result
