"""Mechanical adequacy of JSON-bearing acceptance examples (#3912).

The DISCRIMINATING policy (autocode_acceptance_policy) tells every stage that writes acceptance
examples to tell the requested behavior apart from a plausible broken version. That is prompt
guidance; nothing checked it. A Requirements card can propose a JSON example a plausible-wrong
implementation still satisfies, and the person is asked to approve it (live run
20261119-155920-vscode-b00788 on python-semver):

- a duplicate-key rejection fixture missing a required field, e.g. ``{"major":1,"major":2,"patch":3}``:
  a decoder that ACCEPTS duplicate keys (ordinary ``json.loads``, last wins) reads ``{"major":2,"patch":3}``
  and still raises for the missing ``minor``, so the example passes for the wrong reason. The fixture
  must carry every field a valid example carries, so only the duplicate key is wrong;
- a build-metadata preservation case checked only by value equality, e.g.
  ``result == Version.parse("1.2.3-rc.1+007")``: SemVer value equality ignores build metadata, so a
  decoder that DROPS the build field still satisfies the equality. The example must assert the full
  version string or the build component explicitly.

This is the shared, decidable form of those two gaps: read the JSON object and version literals the
examples actually contain, and flag an example a duplicate-accepting or build-dropping decoder still
satisfies. It guesses no domain: a duplicate fixture is flagged only against a valid companion fixture
that carries strictly more of the same fields, and a build case only when a build-metadata literal is
checked by equality with no string/build assertion. Where the literals to decide it are absent it says
nothing, so an ordinary prose example is untouched.

Domain layer: pure over the example strings; imports nothing from AutoCode. The one answer is read by
the Requirements admission gate (autocode_requirements_handoff._accept) and the decidable-faults list
(autocode_requirements_faults.faults, also ``autocode report-check``), so an inadequate example is sent
back to its writer through the existing rewrite route, never a new person question.
"""

from __future__ import annotations

import json
import re

# A JSON object literal's top-level pairs, kept as written so a repeated key is observable.
_PAIRS_DECODER = json.JSONDecoder(object_pairs_hook=lambda pairs: pairs)
_OBJECT_START = re.compile(r"\{")
# A SemVer core with a build-metadata suffix (``+build``); the part value equality ignores.
_BUILD_VERSION = re.compile(r"\b\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?\+[0-9A-Za-z.-]+\b")
_REJECTION = re.compile(
    r"\b(?:raise\w*|reject\w*|error\w*|invalid|valueerror|fail\w*|refus\w*|exception\w*|throws?|"
    r"must not accept|not accept|does not accept|cannot accept|disallow\w*)\b",
    re.I,
)
_EQUALITY = re.compile(r"==|\b(?:equals?|equal to|is equal|equality|equivalent)\b", re.I)
# An explicit assertion of the rendered string or the build component discriminates a build-dropping
# decoder; value equality alone does not.
_STRING_ASSERTION = re.compile(
    r"\bstr\(|\bfull (?:version )?string\b|\bexact (?:version )?string\b|\.build\b|"
    r"\bbuild (?:component|metadata|part|field|suffix)\b|\bas a string\b|\bstring (?:equals|comparison|is)\b|"
    r"\bthe (?:full |exact |version )?string\b|\brendered string\b|\bserializ\w* string\b|\bserialis\w* string\b",
    re.I,
)


def _object_fixtures(text: str) -> list[dict]:
    """Each JSON object literal in ``text``: its distinct top-level keys and whether a key repeats."""
    found: list[dict] = []
    for match in _OBJECT_START.finditer(text):
        try:
            value, end = _PAIRS_DECODER.raw_decode(text, match.start())
        except ValueError:
            continue
        if (
            isinstance(value, list)
            and value
            and all(isinstance(pair, tuple) and len(pair) == 2 and isinstance(pair[0], str) for pair in value)
        ):
            keys = [pair[0] for pair in value]
            found.append(
                {
                    "distinct": frozenset(keys),
                    "duplicate": len(keys) != len(set(keys)),
                    "raw": text[match.start() : end],
                }
            )
    return found


def _duplicate_key_faults(examples: list[str]) -> list[str]:
    """A duplicate-key rejection fixture missing fields a valid companion fixture carries."""
    valid: list[frozenset] = []
    rejects: list[dict] = []
    for text in examples:
        rejection = bool(_REJECTION.search(text))
        for fixture in _object_fixtures(text):
            if fixture["duplicate"] and rejection:
                rejects.append(fixture)
            elif not fixture["duplicate"] and not rejection:
                valid.append(fixture["distinct"])
    faults: list[str] = []
    for fixture in rejects:
        keys = fixture["distinct"]
        # A valid fixture carrying strictly more of the same fields: the duplicate fixture is that
        # object minus required fields, so a duplicate-accepting decoder rejects it for the wrong reason.
        supersets = [other for other in valid if keys < other]
        if not supersets:
            continue
        missing = sorted(min(supersets, key=len) - keys)
        faults.append(
            f"Acceptance example duplicate-key fixture {fixture['raw']} is missing required field(s) "
            f"{', '.join(missing)} that a valid example carries; a decoder that accepts duplicate keys "
            "rejects it for the missing field(s), not the duplicate. Give the fixture every required field "
            "so only the repeated key is wrong."
        )
    return faults


def _build_metadata_faults(examples: list[str]) -> list[str]:
    """A build-metadata version checked only by value equality, which ignores the build field."""
    faults: list[str] = []
    for text in examples:
        literal = _BUILD_VERSION.search(text)
        if not literal or not _EQUALITY.search(text) or _STRING_ASSERTION.search(text):
            continue
        faults.append(
            f"Acceptance example checks build-metadata version {literal.group(0)} by value equality only; "
            "SemVer value equality ignores build metadata, so an implementation that drops the build field "
            "still passes. Assert the full version string or the build component explicitly."
        )
    return faults


def problems(examples) -> list[str]:
    """Every decidable adequacy fault of proposed acceptance examples; [] when none is decidable.

    ``examples`` is the report's ``acceptance_tests`` array of strings. Non-string entries are ignored.
    The faults are deterministic and deduplicated, ordered duplicate-key cases then build-metadata cases.
    """
    rows = [text for text in (examples or []) if isinstance(text, str) and text.strip()]
    if not rows:
        return []
    found = [*_duplicate_key_faults(rows), *_build_metadata_faults(rows)]
    return list(dict.fromkeys(found))
