"""Bind independently reviewed changed-line reasons to the exact offered proof.

Call bind only after ordinary source, report identity and executed-check admission.
verified_refs maps model references to references already authenticated and pinned
by the caller. This module never treats model prose or a file name as evidence.
"""

from __future__ import annotations

import copy
import json

try:
    from . import autocode_changed_code_evidence as line_evidence
except ImportError:
    import autocode_changed_code_evidence as line_evidence

FIELD = "changed_code_exceptions"
KINDS = ("defensive_unreachable", "instrumentation_unavailable", "equivalent_mutant")


def prompt_order(schema: dict) -> dict:
    """The prompt copy with the attempt-specific property rendered last (A3, #2113).

    A schema read back from a state file is alphabetized by the JSON writer, so the
    attempt-specific property with its per-attempt enum sorts to the front of the
    prefix and pushes every static property deeper. Providers call this on their own
    copy; the saved schema and the bound schema are never reordered.
    """
    properties = schema.get("properties") or {}
    if FIELD not in properties:
        return schema
    ordered = {**schema, "properties": {key: value for key, value in properties.items() if key != FIELD}}
    ordered["properties"][FIELD] = properties[FIELD]
    return ordered


SCHEMA = {
    "type": ["array", "null"],
    "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["gap_id", "source_line", "decision", "reason_kind", "reason", "evidence_refs"],
        "properties": {
            "gap_id": {"type": "string"},
            # The text of the exact line gap_id binds, copied from its obligation's source_line (#2977).
            "source_line": {"type": "string"},
            "decision": {"type": "string", "enum": ["accept", "reject"]},
            "reason_kind": {"type": "string", "enum": list(KINDS)},
            "reason": {"type": "string"},
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
        },
    },
}


# A report generated before decisions quoted their line (#2977) was offered these fields alone.
LEGACY_FIELDS = frozenset(SCHEMA["items"]["properties"]) - {"source_line"}


def _stage(record):
    return str(record.get("original_stage") or record.get("stage", "")).removesuffix("_report_repair")


def _fields(schema, stage):
    fields = schema.get("properties", {})
    return fields.get("validation", {}).get("properties", {}) if stage == "review_checkpoint" else fields


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


def _names(gaps):
    """Each current gap's accepted names: its short handle (when it has one) and its full id."""
    result = {}
    for identity, row in gaps.items():
        result[identity] = identity
        handle = row.get("handle")
        if isinstance(handle, str) and handle and handle not in gaps and handle not in result:
            result[handle] = identity
    return result


def resolve(assessment, name):
    """The exact gap id a supplied handle or id names, or the name unchanged when it names none."""
    return _names(_gaps(assessment)).get(name, name) if isinstance(name, str) else name


def misquoted(assessment, decisions):
    """Each decision whose source_line is not the text of the exact line its gap_id binds (#2977).

    A handle names one authenticated path, file hash and line; a reason under it must justify that
    line. A decision that quotes another line describes another line, whatever its handle says, so
    it is returned to the Tester rather than accepted. Only a gap whose text was read from its
    measured bytes is compared. Matching text proves only that the reason names its own line; the
    reason's adequacy stays the independent reviewers' judgment.
    """
    gaps = _gaps(assessment)
    names = _names(gaps)
    result = []
    for row in decisions if isinstance(decisions, list) else []:
        if not isinstance(row, dict) or "source_line" not in row:
            continue
        gap = gaps.get(names.get(row.get("gap_id"))) if isinstance(row.get("gap_id"), str) else None
        expected = gap.get("source_line") if gap else None
        if isinstance(expected, str) and line_evidence.quoted(row["source_line"]) != expected:
            result.append((row, gap))
    return result


def misquote_message(mismatches):
    """The report-repair error naming each decision's quoted line and the line its handle binds."""
    listed = "; ".join(
        f"{row.get('gap_id')} quotes {json.dumps(line_evidence.quoted(row.get('source_line')) or '')} but binds "
        f"{gap['path']}:{gap['line']}, which is {json.dumps(gap['source_line'])}"
        for row, gap in mismatches
    )
    return (
        f"A changed_code_exceptions decision describes a different line than the one its handle binds: {listed}. "
        "Each decision's source_line must be the source_line of its own gap, and its reason must justify "
        "that exact line. Re-read each named line and decide it on its own, or give the decision under "
        "the handle of the line you described."
    )


def generation_schema(schema, assessment, stage):
    """Offer only current gaps to an independent Tester; never mutate a shared schema."""
    result = copy.deepcopy(schema)
    if stage == "builder":
        # The Builder's final-only self-assessment borrows the Tester shape but
        # cannot independently approve its own reasons.
        own = result.get("properties", {}).get("continuation", {}).get("properties", {}).get("self_assessment", {})
        own.get("properties", {}).pop(FIELD, None)
        if "required" in own:
            own["required"] = [key for key in own["required"] if key != FIELD]
        return result
    if stage not in ("tester", "review_checkpoint"):
        return result
    field = _fields(result, stage).get(FIELD)
    if not isinstance(field, dict):
        return result
    gaps = _gaps(assessment)
    field["maxItems"] = len(gaps)
    if gaps:
        # Handles first: a model copies "L12" reliably where it mistypes one of thirty 64-hex ids.
        # The full ids stay accepted, so saved reports and scripted Testers bind as before.
        names = [name for name in _names(gaps) if name not in gaps] + list(gaps)
        props = field["items"]["properties"]
        props["gap_id"] = {**props["gap_id"], "enum": names}
    return result


def normalize_absent(value, schema, record):
    """Legacy omissions mean no decision; supplied decisions remain untouched."""
    stage = _stage(record)
    if stage not in ("tester", "review_checkpoint"):
        return value
    field = _fields(schema, stage).get(FIELD, {})
    kind = field.get("type")
    if not isinstance(kind, list) or "array" not in kind or "null" not in kind:
        return value
    result = copy.deepcopy(value)
    target = result.get("validation") if stage == "review_checkpoint" else result
    if isinstance(target, dict):
        target.setdefault(FIELD, None)
    return result


def tester_only_gaps(assessment, decisions, offered_schema, stage):
    """Offered gaps no Builder test can close and no supplied decision settles.

    A ``reason_required`` line with no mutation outcome has no mutant any test could kill: its
    statement has no supported mutation site, or its language has no mutation check. Sending the
    Builder back cannot change it, so only the independent Tester's accept or reject settles it.
    Returns those obligations in proof order when the saved generation schema offered them; a
    legacy schema without the field returns nothing.
    """
    field = _fields(offered_schema if isinstance(offered_schema, dict) else {}, stage).get(FIELD)
    if not isinstance(field, dict):
        return []
    offered = field.get("items", {}).get("properties", {}).get("gap_id", {}).get("enum")
    if not isinstance(offered, list):
        return []
    gaps = _gaps(assessment)
    names = _names(gaps)
    decided = {names.get(row.get("gap_id"), row.get("gap_id")) for row in decisions or [] if isinstance(row, dict)}
    return [
        row
        for identity, row in gaps.items()
        if identity in offered and identity not in decided and not row.get("mutation_outcomes")
    ]


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
    if (stage, record.get("role")) not in (("tester", "tester"), ("review_checkpoint", "task_planner")):
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
    names = _names(gaps)
    if (
        not isinstance(offered, list)
        or not offered
        or len(offered) != len(set(offered))
        or field.get("maxItems") != len(gaps)
        # A legacy schema offered the full ids alone; a current one offers each handle too.
        or set(offered) not in (set(gaps), set(names))
        or not isinstance(verified_refs, dict)
    ):
        raise ValueError("Changed-code reason review has no exact current offered gap binding")
    fields = set(field.get("items", {}).get("properties") or ())
    if fields not in (set(SCHEMA["items"]["properties"]), LEGACY_FIELDS):
        raise ValueError("Changed-code reason review has no exact current offered gap binding")
    mismatches = misquoted(assessment, decisions)
    if mismatches:
        raise ValueError(misquote_message(mismatches))
    result, seen = [], set()
    for row in decisions:
        if not isinstance(row, dict) or set(row) != fields:
            raise ValueError("Malformed changed-code exception decision")
        supplied = row.get("gap_id")
        identity = names.get(supplied) if isinstance(supplied, str) and supplied in offered else None
        if not isinstance(identity, str) or identity in seen:
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
                **({"source_line": gap["source_line"]} if isinstance(gap.get("source_line"), str) else {}),
                "accepted": row["decision"] == "accept",
                "reason_kind": row["reason_kind"],
                "reason": reason.strip(),
                "evidence": evidence,
                "reviewer": {"stage": stage, "role": record["role"], "output": record["output"]},
            }
        )
    return result
