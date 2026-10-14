"""Narrow criterion wording recommendations, independent of approval and runtime.

A recommendation can replace only the text of already declared criteria. The
caller still owns request freshness, normal plan review and explicit approval;
composing a body or recognizing an acceptance phrase grants none of those.
"""

from __future__ import annotations

from copy import deepcopy

ACCEPTANCE = "Accept recommended revision"
MAX_CORRECTIONS = 32
MAX_ID_CHARS = 128
MAX_CRITERION_CHARS = 8192

# Optional when attached to a saved Resolver request; structured-output callers
# can require this nullable property without inventing a recommendation.
SCHEMA = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": ["acceptance_criteria"],
    "properties": {
        "acceptance_criteria": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_CORRECTIONS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "criterion"],
                "properties": {
                    "id": {"type": "string", "minLength": 1, "maxLength": MAX_ID_CHARS, "pattern": r"\S"},
                    "criterion": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_CRITERION_CHARS,
                        "pattern": r"\S",
                    },
                },
            },
        }
    },
}


def accepts(text):
    """Recognize only the explicit option, with outer whitespace and case ignored."""
    return isinstance(text, str) and text.strip().casefold() == ACCEPTANCE.casefold()


def _text(value, name, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"Recommended revision {name} must be a nonempty string of at most {limit} characters")
    return value


def compose(body, recommendation):
    """Return an independent body with only named criterion text replaced.

    IDs must be distinct and already present, and each correction must change
    its target's wording. Only surrounding whitespace is ignored for no-op
    detection: internal whitespace can be part of an exact-output requirement.
    All other fields, including verification methods and source provenance,
    are copied unchanged. Invalid input never partially changes either input.
    """
    if not isinstance(recommendation, dict) or set(recommendation) != {"acceptance_criteria"}:
        raise ValueError("Recommended revision must contain only acceptance_criteria")
    patches = recommendation["acceptance_criteria"]
    if not isinstance(patches, list) or not 1 <= len(patches) <= MAX_CORRECTIONS:
        raise ValueError(f"Recommended revision needs 1 to {MAX_CORRECTIONS} criterion corrections")
    if not isinstance(body, dict) or not isinstance(body.get("acceptance_criteria"), list):
        raise ValueError("Recommended revision requires an existing acceptance checklist")
    originals = {}
    for row in body["acceptance_criteria"]:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"].strip():
            raise ValueError("Existing acceptance criteria need distinct nonempty IDs")
        if row["id"] in originals:
            raise ValueError("Existing acceptance criteria have duplicate IDs")
        originals[row["id"]] = row
    replacements = {}
    for patch in patches:
        if not isinstance(patch, dict) or set(patch) != {"id", "criterion"}:
            raise ValueError("Each recommended correction must contain only id and criterion")
        cid = _text(patch["id"], "criterion ID", MAX_ID_CHARS)
        wording = _text(patch["criterion"], "criterion text", MAX_CRITERION_CHARS)
        if cid in replacements:
            raise ValueError("Recommended revision repeats criterion " + cid)
        if cid not in originals:
            raise ValueError("Recommended revision names unknown criterion " + cid)
        before = originals[cid].get("criterion")
        if not isinstance(before, str) or not before.strip():
            raise ValueError("Recommended revision requires existing criterion text for " + cid)
        if before.strip() == wording.strip():
            raise ValueError("Recommended revision does not change criterion " + cid)
        replacements[cid] = wording
    revised = deepcopy(body)
    for row in revised["acceptance_criteria"]:
        if row["id"] in replacements:
            row["criterion"] = replacements[row["id"]]
    return revised
