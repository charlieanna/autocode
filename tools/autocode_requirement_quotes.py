"""Ground a Requirements source_quote in the exact text of the brief or a saved user event.

A requirement is user-backed only when its source_quote is the user's own text. The runner's
coverage checklist (autocode_requirement_cues.cue_sentences) renders that text slightly
differently: a substantive Markdown heading gains a final period, and a hard-wrapped sentence keeps
its line breaks, which a model quoting it writes as spaces. Both prompts tell the model to copy each
checklist sentence in full, so a plain substring check refused the runner's own sentences, and the
bounded report repairs, told the same thing, repeated the quote until the run paused as invalid
output (2026-10-25 run, R3).

``ground`` maps such a quote back to the source's own characters, or returns None. What it returns
is always a verbatim slice of a source, so nothing the user did not write is ever saved as a quote.
``closest`` names the nearest source sentence, and ``unsupported`` writes the rejection that lists every
unsupported row with it, so a repair can copy the text or drop the row.
Pure functions; imports only autocode_requirement_cues.
"""

from __future__ import annotations

import difflib
import json
import re

try:
    from .autocode_requirement_cues import coverage_checklist, sentences
except ImportError:
    from autocode_requirement_cues import coverage_checklist, sentences


def _span(quote: str, text: str) -> str | None:
    """``quote`` in ``text`` with any run of whitespace matching any other (#1777), as the source writes it."""
    words = quote.split()
    if not words:
        return None
    match = re.search(r"\s+".join(map(re.escape, words)), text)
    return match.group(0) if match else None


def ground(quote, sources) -> str | None:
    """The exact source text ``quote`` stands for: the quote itself, the same words with the source's
    whitespace, or the same text without the period the checklist adds to a heading. None when no
    source holds it. A quote that differs only by dropped markup (backticks, quotes, whitespace)
    resolves to the source span itself (#2835)."""
    quote = str(quote or "").strip()
    if not quote:
        return None
    texts = [str(text) for text in sources if text]
    if any(quote in text for text in texts):
        return quote
    for candidate in (quote, quote.removesuffix(".").rstrip()) if quote.endswith(".") else (quote,):
        for text in texts:
            span = _span(candidate, text)
            if span:
                return span
    # A quote that dropped markup (backticks, quotes) still matches the source's words (#2835).
    # Strip markup from both and match; then map back to the source's exact text.
    stripped = _strip_markup(quote)
    if stripped:
        for text in texts:
            stripped_text = _strip_markup(text)
            if _span(stripped, stripped_text):
                # Find the source's exact span: locate the stripped words in the original text.
                span = _find_source_span(stripped, text)
                if span:
                    return span
    return None


def _markup_flexible_match(stripped: str, text: str) -> bool:
    """Whether ``stripped`` matches ``text`` ignoring markup characters and whitespace."""
    words = stripped.split()
    if not words:
        return False
    # Between words, allow markup and whitespace; at word boundaries, allow markup only.
    parts = []
    for i, word in enumerate(words):
        if i > 0:
            parts.append(r"[`'\"\u201c\u201d\u2018\u2019\s]*")
        parts.append(re.escape(word))
    # After the last word, allow trailing markup (e.g. quote drops a closing backtick).
    parts.append(r"[`'\"\u201c\u201d\u2018\u2019]*")
    pattern = "".join(parts)
    return re.search(pattern, text) is not None


def _find_source_span(stripped: str, text: str) -> str | None:
    """The exact source text for ``stripped``'s words, with the source's own markup and whitespace."""
    words = stripped.split()
    if not words:
        return None
    # Find each word's position in the source; words may be wrapped in markup.
    # Match each word allowing surrounding markup, and take the span from first to last.
    positions = []
    for word in words:
        core = word.strip(".,;:!?")
        if not core:
            continue
        # Find the core word in the source, allowing markup around it.
        match = re.search(
            r"[`'\"\u201c\u201d\u2018\u2019]*" + re.escape(core) + r"[`'\"\u201c\u201d\u2018\u2019]*",
            text,
        )
        if not match:
            return None
        positions.append(match)
    if not positions:
        return None
    start = positions[0].start()
    end = positions[-1].end()
    # Extend to include trailing punctuation from the source.
    while end < len(text) and text[end] in ".,;:!?":
        end += 1
    return text[start:end]


_MARKUP_CHARS = "`'\"\u201c\u201d\u2018\u2019"


def _strip_markup(text: str) -> str:
    """Remove backticks and quote marks, collapse whitespace: the words a dropped-markup quote holds."""
    cleaned = "".join(c for c in text if c not in _MARKUP_CHARS)
    return " ".join(cleaned.split())


# A requirement may cite a requirement_coverage_checklist sentence by its 1-based position in that
# list instead of retyping it (#3344). The runner holds the bytes, so it fills them in itself.
CHECKLIST_ID = re.compile(r"^c(?P<number>[1-9][0-9]*)$")


def resolve_checklist_id(quote, checklist):
    """The checklist sentence ``quote`` cites, or ``quote`` unchanged when it cites none.

    What comes back is the runner's own text, so grounding it still saves a verbatim slice of a human
    source and no admission check is weakened. An id that names no sentence is left as written, so the
    quote check refuses it and names the id rather than resolving to a neighbouring sentence."""
    text = str(quote or "").strip()
    match = CHECKLIST_ID.match(text)
    if match is None:
        return quote
    number = int(match["number"])
    return checklist[number - 1] if 1 <= number <= len(checklist) else quote


def ground_cited(quote, sources, state):
    """Ground a source_quote that may cite a checklist sentence by id, filling the runner's own bytes.

    Requirements are told to copy each requirement_coverage_checklist sentence in full, so a quote
    rejection was usually a retyping slip against text the runner already held: a wrong copy cost a
    25-minute repair (#2835) and six of seven measured passes on 2026-11-11 were rejected for one
    (#3344). Citing the sentence's id removes the copying. The resolved text is grounded exactly as a
    typed quote is, so nothing ungrounded is ever saved, and the checklist is built only for an
    id-shaped quote, which leaves the typed path as cheap as it was."""
    text = str(quote or "").strip()
    if CHECKLIST_ID.match(text) is None:
        return ground(quote, sources)
    return ground(resolve_checklist_id(text, coverage_checklist(state)), sources)


def closest(quote, sources) -> str | None:
    """The source sentence nearest ``quote``, for a rejection message; None when nothing is close."""
    quote = " ".join(str(quote or "").split())
    candidates = [" ".join(sentence.split()) for text in sources if text for sentence in sentences(text)]
    best = difflib.get_close_matches(quote, candidates, n=1, cutoff=0.5)
    return best[0] if best else None


def unsupported(rows, sources, *, inherited: bool) -> str:
    """One rejection naming every requirement whose source_quote no source holds, and what a repair does.

    Naming only the first row let each repair fix one and fail on the next, and the message never said
    that a requirement with no user text behind it is removed, which is what finally converged.
    ``inherited`` keeps the hint about a Builder task or approved contract for runs that have one."""
    ids = ", ".join(str(row["id"]) for row in rows)
    lines = [
        f"Requirement {ids} source_quote is not in the task or a saved user event"
        if len(rows) == 1
        else f"Requirements {ids}: source_quote is not in the task or a saved user event",
    ]
    for row in rows:
        quoted = str(row.get("source_quote", ""))
        if CHECKLIST_ID.match(quoted.strip()):
            lines.append(
                f"{row['id']} cites {json.dumps(quoted, ensure_ascii=False)}, "
                "which names no sentence of requirement_coverage_checklist"
            )
            continue
        near = closest(quoted, sources)
        lines.append(
            f"{row['id']} quotes {json.dumps(quoted, ensure_ascii=False)}"
            + (f"; the closest source sentence is {json.dumps(near, ensure_ascii=False)}" if near else "")
        )
    lines.append(
        "For each, copy the source text character for character if it says this requirement; otherwise remove "
        "that requirement (and every reference to its id), or record it as a proposed_assumption or an "
        "open_question. Never keep a requirement whose quote no source holds"
    )
    if inherited:
        lines.append(
            "If this text came from the current Builder task or approved contract, "
            "keep that existing obligation there instead of adding a new requirement"
        )
    return ". ".join(lines)
