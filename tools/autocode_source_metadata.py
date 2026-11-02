"""Documentary source labels, distinct from the values a brief asks code to implement.

Only a leading Markdown file:line label is a location. Inline paths, file-scope
instructions and output strings remain literals. Source-record references are
unquoted prose references to an ID in the authenticated inventory, never an
arbitrary task-like value. Original source text and citations are not rewritten.
"""

from __future__ import annotations

import re

_LOCATION_LABEL = re.compile(
    r"^[ \t]*(?:[-*+]\s+)?`(?P<location>(?:[\w.-]+/)*[\w.-]+\.[A-Za-z][\w-]*:[1-9]\d*(?:[-–][1-9]\d*)?)`:(?=\s)",
    re.M,
)
_INLINE_CODE = re.compile(r"(`[^`\n]*`)")
_OUTPUT = re.compile(r"\b(?:print|emit|output|return|literal|exactly)\b", re.I)
_TICKET_TITLE = re.compile(
    r"\A\s*(?:Implement a joint regression for this original bug report:[ \t]*\r?\n[ \t]*)?"
    r"(?:#{1,6}[ \t]+)?(?P<key>[A-Z][A-Z0-9]+-[1-9]\d*):[ \t]+(?=\S)"
)
_TITLE_VALUE = re.compile(r"\b(?:code|identifier|key|version|sku)\b", re.I)
_TICKET_KEY = r"[A-Z][A-Z0-9]+-[1-9]\d*"
_TICKET_REFERENCE = re.compile(
    r"\b(?:(?i:ticket|issue|bug report)\s+|(?i:original|reported)\s+(?="
    + _TICKET_KEY
    + r"\s+(?i:report|task|outcome|issue|ticket|bug(?: report)?)\b))(?P<key>"
    + _TICKET_KEY
    + r")(?![\w-])"
)
_QUOTED_REFERENCE = re.compile(r"`[^`\n]*`|\"[^\"\n]*\"|'[^'\n]*'")
_REFERENCE_OUTPUT = re.compile(r"\b(?:print|emit|output|return|literal|display|show|write|quote|say)\b", re.I)


def ticket_reference_keys(text: str) -> set[str]:
    """Admit explicit documentary ticket references, never output or product values.

    Every use of a key in the whole source must be an unquoted reference. An
    ambiguous second use therefore keeps the key's numbers in every sentence.
    """
    counts: dict[str, int] = {}
    output_context = False
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        matches = list(_TICKET_REFERENCE.finditer(line))
        if not matches:
            if line.strip():
                output_context = bool(_REFERENCE_OUTPUT.search(line))
            continue
        if fenced or output_context or _REFERENCE_OUTPUT.search(line) or _TITLE_VALUE.search(line):
            continue
        quoted = [match.span() for match in _QUOTED_REFERENCE.finditer(line)]
        for match in matches:
            if not any(start <= match.start("key") < end for start, end in quoted):
                key = match["key"]
                counts[key] = counts.get(key, 0) + 1
    return {key for key, count in counts.items() if text.count(key) == count}


def ticket_title_key(text: str) -> str:
    """A leading ticket-title label, unless the source also uses it as a value.

    Only a leading bare KEY-number: title convention is documentary, including
    the original title immediately after the coordinator's fixed joint-task intro.
    Inline codes, titles describing a value/output and a key used again anywhere
    in the source remain ambiguous and keep their numbers. The prose stays owed.
    """
    match = _TICKET_TITLE.match(text)
    if not match:
        return ""
    title = text[match.start("key") :].splitlines()[0]
    if _OUTPUT.search(title) or _TITLE_VALUE.search(title):
        return ""
    key = match["key"]
    return "" if key in text[match.end() :] else key


def output_locations(text: str) -> set[str]:
    """Keep labels introduced as output, including a list under an output heading.

    Ambiguity is conservative: an output use anywhere protects the same location
    throughout this source record. Blank lines and sibling labels keep that context.
    """
    protected = set()
    output_context = False
    for line in text.splitlines():
        label = _LOCATION_LABEL.match(line)
        if label:
            if output_context or _OUTPUT.search(line[label.end() :]):
                protected.add(label["location"])
        elif line.strip():
            output_context = bool(_OUTPUT.search(line))
    return protected


def without_location_labels(text: str, *, protected_locations=()) -> str:
    """Remove only documentary labels for the obligation scan, retaining their prose."""
    protected = output_locations(text) | set(protected_locations)
    return _LOCATION_LABEL.sub(lambda match: match.group() if match["location"] in protected else " ", text)


def value_text(text: str, source_ids, *, protected_locations=(), ticket_title_key="", ticket_reference_keys=()) -> str:
    """Text whose numbers denote values, with known source-reference metadata removed."""
    text = without_location_labels(text, protected_locations=protected_locations)
    title = _TICKET_TITLE.match(text)
    if title and title["key"] == ticket_title_key:
        text = text[: title.start("key")] + text[title.end("key") :]
    text = _TICKET_REFERENCE.sub(
        lambda match: (
            match.group().replace(match["key"], "") if match["key"] in ticket_reference_keys else match.group()
        ),
        text,
    )
    ids = "|".join(re.escape(value) for value in sorted(set(source_ids), key=len, reverse=True))
    if not ids:
        return text
    # A request to cite a sentence of an authenticated feedback record names its
    # provenance, not a quantity hidden inside the record's opaque ID (#2197).
    reference = re.compile(r"\b(?:original|from|source|sentences?\s+(?:of|in))\s+(?:" + ids + r")(?=$|[\s,.;:])")
    parts = _INLINE_CODE.split(text)
    for index in range(0, len(parts), 2):
        part = parts[index]
        parts[index] = reference.sub(
            lambda match: match.group() if _OUTPUT.search(part[: match.start()]) else " ", part
        )
    return "".join(parts)
