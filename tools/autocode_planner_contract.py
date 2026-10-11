"""Versioned machine-readable contract for structured Planner drafts.

The single machine-readable source of truth is the mirrored JSON Schema at
``docs/schemas/continuous-planner-contract.schema.json``.  This module loads,
self-checks and enforces that file: production commit code must validate every
structured Planner result through :func:`validate_structured_draft` before the
result may become an accepted plan-draft revision.  Schema-invalid drafts are
rejected loudly so the previous usable draft is preserved upstream.

The validator deliberately implements a self-contained subset of JSON Schema
(no third-party dependency) so offline runner environments enforce exactly the
same machine-readable contract the dashboard task consumes.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

PLANNER_CONTRACT_VERSION = 1
STRUCTURED_DRAFT_KIND = "autocode.planner-structured-draft"
SCHEMA_ID = "autocode:schemas/continuous-planner-contract"
SCHEMA_RELPATH = Path("docs") / "schemas" / "continuous-planner-contract.schema.json"
MAX_STRUCTURED_DRAFT_BYTES = 262_144

# Bounds mirrored from the conversation protocol's plan-draft transport layer
# (tools/autocode_conversation.py) so a validated structured draft always fits
# the journal/handoff record it will be committed into.
_IDENTIFIER_PATTERN = r"[A-Za-z0-9_.:-]{1,160}"

_SCHEMA_CACHE: dict[Path, dict] = {}


class PlannerContractError(ValueError):
    """A structured Planner draft does not satisfy the versioned contract."""


def contract_schema_path() -> Path:
    """Absolute path of the mirrored machine-readable JSON Schema."""
    return Path(__file__).resolve().parent / "autocode-schemas" / SCHEMA_RELPATH.name


def load_contract_schema(path: Path | None = None) -> dict:
    """Load and self-check the machine-readable contract schema.

    The mirror check proves the JSON file and this module advertise the same
    version, kind and schema identity before any draft is validated against
    them; a stale or mismatched mirror fails loudly instead of silently
    validating against divergent rules.
    """
    resolved = Path(path).resolve() if path is not None else contract_schema_path()
    cached = _SCHEMA_CACHE.get(resolved)
    if cached is not None:
        return deepcopy(cached)
    try:
        schema = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PlannerContractError(f"The Planner contract schema could not be loaded from {resolved}.") from error
    _check_mirror(schema, resolved)
    _SCHEMA_CACHE[resolved] = deepcopy(schema)
    return schema


def _check_mirror(schema: object, resolved: Path) -> None:
    if not isinstance(schema, dict):
        raise PlannerContractError("The Planner contract schema must be a JSON object.")
    if schema.get("$id") != f"{SCHEMA_ID}@{PLANNER_CONTRACT_VERSION}":
        raise PlannerContractError("The Planner contract schema identity does not match this module version.")
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not isinstance(schema.get("required"), list):
        raise PlannerContractError("The Planner contract schema is missing its structured-draft rules.")
    version_rule = properties.get("contract_version")
    kind_rule = properties.get("kind")
    if not isinstance(version_rule, dict) or version_rule.get("const") != PLANNER_CONTRACT_VERSION:
        raise PlannerContractError("The Planner contract schema version constant is out of sync.")
    if not isinstance(kind_rule, dict) or kind_rule.get("const") != STRUCTURED_DRAFT_KIND:
        raise PlannerContractError("The Planner contract schema kind constant is out of sync.")


def validate_structured_draft(draft: object, *, schema: dict | None = None) -> dict:
    """Validate a structured Planner draft against the versioned contract.

    Returns a validated deep copy of the draft.  Raises
    :class:`PlannerContractError` for any schema-invalid draft (wrong kind or
    version, missing or malformed binding/attribution/freshness, out-of-bounds
    content, unknown fields) so callers preserve the previous usable draft and
    record the failure with this error as retained evidence.
    """
    rules = schema if schema is not None else load_contract_schema()
    _validate(draft, rules, "$")
    assert isinstance(draft, dict), "the schema's root type must be object"
    try:
        payload = json.dumps(draft, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise PlannerContractError("Structured Planner draft is not JSON-safe.") from error
    if len(payload.encode("utf-8")) > MAX_STRUCTURED_DRAFT_BYTES:
        raise PlannerContractError("Structured Planner draft exceeds the supported size.")
    return deepcopy(draft)


def _failure(path: str, message: str) -> NoReturn:
    raise PlannerContractError(f"{path}: {message}")


def _type_matches(value: object, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def _json_equal(left: object, right: object) -> bool:
    """JSON Schema equality keeps booleans distinct from numbers in Python."""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) or isinstance(right, (int, float)):
        return isinstance(left, (int, float)) and isinstance(right, (int, float)) and left == right
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_json_equal(a, b) for a, b in zip(left, right, strict=False))
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_json_equal(left[key], right[key]) for key in left)
    return type(left) is type(right) and left == right


def _validate(value: object, rules: object, path: str) -> None:
    if not isinstance(rules, dict):
        _failure(path, "schema rules must be an object")
    if "anyOf" in rules:
        if not any(_passes(value, option, path) for option in rules["anyOf"]):
            _failure(path, "value does not match any allowed shape")
        return
    if "oneOf" in rules:
        matches = sum(1 for option in rules["oneOf"] if _passes(value, option, path))
        if matches != 1:
            _failure(path, "value does not match exactly one allowed shape")
        return
    if "type" in rules:
        expected = rules["type"]
        names = expected if isinstance(expected, list) else [expected]
        if not any(_type_matches(value, name) for name in names):
            _failure(path, f"expected {' or '.join(names)}")
    if "enum" in rules and not any(_json_equal(value, member) for member in rules["enum"]):
        _failure(path, f"value must be one of {rules['enum']!r}")
    if "const" in rules and not _json_equal(value, rules["const"]):
        _failure(path, f"value must equal {rules['const']!r}")
    if isinstance(value, str):
        if "minLength" in rules and len(value) < rules["minLength"]:
            _failure(path, f"string shorter than {rules['minLength']} characters")
        if "maxLength" in rules and len(value) > rules["maxLength"]:
            _failure(path, f"string longer than {rules['maxLength']} characters")
        if "pattern" in rules and not re.search(rules["pattern"], value):
            _failure(path, f"string does not match {rules['pattern']!r}")
    if isinstance(value, int) and not isinstance(value, bool) or isinstance(value, float):
        if "minimum" in rules and value < rules["minimum"]:
            _failure(path, f"value below minimum {rules['minimum']}")
        if "maximum" in rules and value > rules["maximum"]:
            _failure(path, f"value above maximum {rules['maximum']}")
    if isinstance(value, list):
        if "minItems" in rules and len(value) < rules["minItems"]:
            _failure(path, f"array needs at least {rules['minItems']} items")
        if "maxItems" in rules and len(value) > rules["maxItems"]:
            _failure(path, f"array allows at most {rules['maxItems']} items")
        if rules.get("uniqueItems"):
            seen: list[object] = []
            for item in value:
                if any(_json_equal(item, previous) for previous in seen):
                    _failure(path, "array items must be unique")
                seen.append(item)
        if "items" in rules:
            for index, item in enumerate(value):
                _validate(item, rules["items"], f"{path}[{index}]")
    if isinstance(value, dict):
        if any(not isinstance(name, str) for name in value):
            _failure(path, "object keys must be strings")
        for name in rules.get("required", []):
            if name not in value:
                _failure(path, f"missing required property {name!r}")
        properties = rules.get("properties", {})
        additional = rules.get("additionalProperties", True)
        for name, item in value.items():
            if name in properties:
                _validate(item, properties[name], f"{path}.{name}")
            elif additional is False:
                _failure(path, f"unexpected property {name!r}")
            elif isinstance(additional, dict):
                _validate(item, additional, f"{path}.{name}")


def _passes(value: object, rules: object, path: str) -> bool:
    try:
        _validate(value, rules, path)
    except PlannerContractError:
        return False
    return True


def record_product_change(doc, *, detail, recorded_at=None):
    """Frozen-contract backend control: a post-approval product change.

    Appends a ``product_change`` requirements revision and marks the current
    structured draft stale/re-review-required so the exact complete revision
    must be re-reviewed and re-approved before build.  The build block itself
    stays in the runner's goal-contract gate (tools/autocode_goals.py
    approved()/execution_guard; brief feedback reverts approved contracts to
    draft); ordinary technical rework never calls this control.
    """
    if not isinstance(doc, dict):
        raise PlannerContractError("A conversation document is required.")
    if not isinstance(detail, str) or not detail.strip() or len(detail) > 4000:
        raise PlannerContractError("Describe the product change in 1-4000 characters.")
    at = recorded_at or datetime.now(UTC).isoformat(timespec="milliseconds")
    requirements = doc.setdefault("requirements", {"revisions": [], "provenance": []})
    revisions = requirements.setdefault("revisions", [])
    previous = revisions[-1] if revisions else {}
    revision = previous.get("revision", 0) + 1
    revisions.append(
        {
            "revision": revision,
            "created_at": at,
            "source": {"kind": "product_change", "detail": detail.strip()},
            "goal": previous.get("goal") or "",
            "requirements": list(previous.get("requirements", [])),
            "outstanding_questions": list(previous.get("outstanding_questions", [])),
            "status": "product_change",
        }
    )
    requirements.setdefault("provenance", []).append(
        {"revision": revision, "source": "product_change", "detail": detail.strip(), "recorded_at": at}
    )
    for row in doc.get("plan_drafts", []):
        if row.get("status") in ("current", "pending"):
            freshness = row.get("freshness") if isinstance(row.get("freshness"), dict) else {}
            row["freshness"] = {
                **freshness,
                "state": "stale",
                "updated_at": at,
                "reason": "product_change_requires_re_review",
                "product_change_revision": revision,
                "re_review_required": True,
            }
    return doc
