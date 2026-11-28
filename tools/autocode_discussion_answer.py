"""A discussion Requirements answers itself completes on findings, never on the request said back (#4237).

Requirements answers a discussion with nothing to write itself (autocode_requirements_answer), and before,
that answer was its free-text ``assistant_reply``. Admission checked the report's schema, its citations and
its requirement quotes, all of which a restatement of the request passes: a read-only review cited seven
source files, replied with what the person had asked, and reached TASK_COMPLETE. Nothing tied the answer
to the outcome that was asked for.

Such a report now answers in ``findings``, one row per thing it found (``finding``), looked for and did not
find (``negative_finding``), or could not settle from what it read (``evidence_gap``), each naming the
requirement ids it answers. ``problems`` lists what keeps them from answering the request:

- no rows at all, or a requirement no row answers, or an id that names no requirement;
- a finding or negative finding with no ``evidence`` among the files and pinned sources the report read
  (its ``source_refs``; the person's own inputs and web pages are what was asked, not where it was read);
- an evidence gap that does not say what would settle it (``would_settle``);
- a statement that restates the request: a copy of one of its sentences or clauses, or an unanswered
  question wrapped in asking or process framing ("you want to know whether ...", "Determine whether ...").

A supported negative finding and an explicit evidence gap are answers: the person asked a question, and
"no path calls it" or "this cannot be read from here, and this would settle it" says what is so.

The restatement test is lexical, not a semantic proof. Question-framed clauses supply no conclusion,
regardless of new words inside that framing; a separate assertion or explicit evidence gap must answer.
The remaining assertions must not merely copy the request, including its inflections ("staged" where it
said "stages"). Whether a claim is right stays with its evidence, the person and their follow-up. Which
reports are held to findings is ``autocode_requirements_answer.answers_itself``, the same predicate that decides Requirements
answers the discussion. Domain layer: pure over the report; imports only the standard library.
"""

from __future__ import annotations

import re

FIELD = "findings"
KINDS = ("finding", "negative_finding", "evidence_gap")
_TEXT = {"type": "string"}
ROW = {
    "type": "object",
    "properties": {
        "requirement_ids": {"type": "array", "items": _TEXT},
        "kind": {"type": "string", "enum": list(KINDS)},
        "statement": _TEXT,
        "evidence": {"type": "array", "items": _TEXT},
        "would_settle": _TEXT,
    },
    "required": ["requirement_ids", "kind", "statement", "evidence", "would_settle"],
    "additionalProperties": False,
}
SCHEMA = {"type": "array", "items": ROW}
LABELS = {"finding": "Finding", "negative_finding": "Not found", "evidence_gap": "Evidence gap"}

# Words that carry no claim of their own: articles, prepositions, pronouns, auxiliaries, question words and
# negations. A negation is filler, not framing: "it does not stage writes" answers the question.
_FILLER = frozenset(
    [
        "a",
        "an",
        "the",
        "this",
        "that",
        "these",
        "those",
        "there",
        "here",
        "it",
        "its",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "am",
        "do",
        "does",
        "did",
        "done",
        "has",
        "have",
        "had",
        "having",
        "can",
        "could",
        "would",
        "should",
        "will",
        "shall",
        "may",
        "might",
        "must",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "from",
        "by",
        "with",
        "into",
        "onto",
        "about",
        "over",
        "under",
        "between",
        "through",
        "during",
        "before",
        "after",
        "within",
        "without",
        "via",
        "out",
        "up",
        "as",
        "than",
        "then",
        "and",
        "or",
        "but",
        "nor",
        "so",
        "if",
        "also",
        "i",
        "me",
        "my",
        "we",
        "us",
        "our",
        "you",
        "your",
        "he",
        "him",
        "his",
        "she",
        "her",
        "they",
        "them",
        "their",
        "what",
        "which",
        "who",
        "whom",
        "whose",
        "when",
        "where",
        "why",
        "how",
        "not",
        "no",
        "never",
        "none",
        "nothing",
        "any",
        "some",
        "all",
        "each",
        "every",
    ]
)
# Verbs that name the work of answering rather than its result. An imperative one opening a statement makes
# it an instruction ("Check whether ..."), which is the request, not an answer.
_PROCESS = frozenset(
    [
        "assess",
        "assesses",
        "check",
        "checks",
        "checking",
        "examine",
        "examines",
        "inspect",
        "inspects",
        "look",
        "looks",
        "analyze",
        "analyzes",
        "analyse",
        "analyses",
        "analysis",
        "investigate",
        "investigates",
        "verify",
        "verifies",
        "determine",
        "determines",
        "evaluate",
        "evaluates",
        "focus",
        "cover",
        "covers",
        "review",
        "reviews",
        "reviewed",
        "trace",
        "traces",
        "report",
        "reports",
        "identify",
        "identifies",
        "diagnose",
        "diagnoses",
        "diagnosis",
        "confirm",
        "confirms",
        "find",
        "finds",
        "describe",
        "describes",
        "provide",
        "provides",
        "establish",
        "establishes",
        "explore",
        "explores",
        "answer",
        "answers",
        "please",
    ]
)
# Words that point at the asking rather than at what was found.
_FRAMING = frozenset(
    [
        "want",
        "wants",
        "wanted",
        "wanting",
        "ask",
        "asks",
        "asked",
        "asking",
        "request",
        "requests",
        "requested",
        "requesting",
        "question",
        "questions",
        "wonder",
        "wonders",
        "wondering",
        "curious",
        "understand",
        "understands",
        "know",
        "knows",
        "explain",
        "explains",
        "explanation",
        "whether",
        "goal",
    ]
)
_CLAUSES = re.compile(r"[.!?;:]\s+|[.!?;:]$|\n+")
_INTERROGATIVE = re.compile(r"\b(?:whether|why|how|what|which|where|when|who)\b", re.IGNORECASE)
_ACTIVITIES = frozenset(("review", "investigation", "analysis", "assessment", "inspection", "audit"))
_GAP_PREFIX = re.compile(
    r"\b(?:cannot|can't|could not|unable|unknown|unclear|undetermined|"
    r"not (?:know|known|settled|determined)|no evidence)\b",
    re.IGNORECASE,
)
_REPORTED_CLAIM = re.compile(
    r"\b(?:found|confirmed|observed|established|showed|shows|demonstrated|verified)\s+that\b",
    re.IGNORECASE,
)
_ASSERTION_CLAUSES = re.compile(
    _CLAUSES.pattern + r"|\s+(?=(?:and|but)\s+(?:(?:we|I)\s+)?" + _REPORTED_CLAIM.pattern + ")",
    re.IGNORECASE,
)
_LINE_SUFFIX = re.compile(r":\d+(?:-\d+)?$")
# A copied clause shorter than this is not evidence of copying: "Why?" or "Keep it" recur in answers.
_MIN_COPIED = 3


def _words(text) -> list[str]:
    return re.findall(r"[a-z0-9_]+", str(text or "").lower())


def _stem(word: str) -> str:
    """``word`` without its inflection, so "staged", "stages" and "staging" all compare as "stage" does: an
    inflection the request did not use is not a word of the statement's own."""
    if word.endswith("ies") and len(word) > 4:
        word = word[:-3] + "y"
    elif word.endswith("ing") and len(word) > 5:
        word = word[:-3]
    elif word.endswith("ed") and len(word) > 4:
        word = word[:-2]
    elif word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        word = word[:-1]
    return word[:-1] if word.endswith("e") and len(word) > 3 else word


def _tokens(text) -> list[tuple[str, str]]:
    return [(word, _stem(word)) for word in _words(text)]


_PROCESS_STEMS = frozenset(_stem(word) for word in _PROCESS)
_FRAMING_STEMS = frozenset(_stem(word) for word in _FRAMING)


def _noise(token: tuple[str, str]) -> bool:
    """Whether a word carries no claim: filler as written (a closed class), process or framing in any form."""
    word, stem = token
    return word in _FILLER or stem in _PROCESS_STEMS or stem in _FRAMING_STEMS


def rows(report: dict) -> list[dict]:
    """The report's finding rows that are objects; anything else is a problem ``problems`` names."""
    value = (report or {}).get(FIELD)
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def lines(report: dict) -> list[str]:
    """The findings as the answer's own lines, under a ``Findings:`` heading; none when there are none."""
    found = rows(report)
    if not found:
        return []
    out = ["Findings:"]
    for row in found:
        ids = [str(item) for item in row.get("requirement_ids") or [] if str(item).strip()]
        label = LABELS.get(str(row.get("kind")), "Finding") + (f" ({', '.join(ids)})" if ids else "")
        line = f"- {label}: {str(row.get('statement') or '').strip()}"
        if row.get("kind") == "evidence_gap" and str(row.get("would_settle") or "").strip():
            line += " Would settle: " + str(row["would_settle"]).strip()
        out.append(line)
    return out


def _request_texts(report: dict, request_texts) -> list[str]:
    """What was asked: the person's own texts, and the report's restatement of them (its intended outcome
    and requirement texts). The summary and reply are excluded: they may carry the answer."""
    texts = [str(text) for text in request_texts or [] if str(text).strip()]
    texts.append(str(report.get("intended_outcome") or ""))
    texts += [str(row.get("text") or "") for row in report.get("requirements") or [] if isinstance(row, dict)]
    return [text for text in texts if text.strip()]


def _copied_clauses(texts) -> list[list[str]]:
    clauses = []
    for text in texts:
        for part in [text, *_CLAUSES.split(text)]:
            stems = [stem for _, stem in _tokens(part)]
            if len(stems) >= _MIN_COPIED:
                clauses.append(stems)
    return sorted(clauses, key=len, reverse=True)


def _without(tokens: list[tuple[str, str]], clause: list[str]) -> list[tuple[str, str]]:
    out, index, size = [], 0, len(clause)
    while index < len(tokens):
        if [stem for _, stem in tokens[index : index + size]] == clause:
            index += size
        else:
            out.append(tokens[index])
            index += 1
    return out


def _question_only(clause: str) -> bool:
    """An asking/process frame does not assert the proposition inside its indirect question.

    Inspect the matrix clause, not vocabulary novelty inside the question. A negative matrix
    ("cannot determine whether ...") asserts an evidence gap; a code subject ("the worker checks
    whether ...") describes behavior rather than the reviewer's work.
    """
    question = _INTERROGATIVE.search(clause)
    if question is None:
        return False
    prefix_text = clause[: question.start()]
    if _GAP_PREFIX.search(prefix_text) or _REPORTED_CLAIM.search(prefix_text):
        return False
    prefix = _words(prefix_text)
    stems = [_stem(word) for word in prefix]
    subject = next((word for word in prefix if word not in _FILLER), "")
    return (
        not prefix
        or any(stem in _FRAMING_STEMS for stem in stems)
        or _stem(subject) in _PROCESS_STEMS
        or any(word in _ACTIVITIES for word in prefix)
    )


def restates(statement, request_texts) -> bool:
    """Whether ``statement`` says back what was asked instead of what was found.

    Discard question-only clauses before comparing assertions to the request: extra words inside
    "the question remains whether ..." are still part of a question, not a new finding.
    Words compare without their inflection (``_stem``)."""
    assertions = [clause for clause in _ASSERTION_CLAUSES.split(str(statement or "")) if not _question_only(clause)]
    tokens = _tokens(" ".join(assertions))
    if not tokens:
        return True
    remaining = tokens
    for clause in _copied_clauses(request_texts):
        remaining = _without(remaining, clause)
    if all(_noise(token) for token in remaining):
        return True
    vocabulary = {stem for text in request_texts for _, stem in _tokens(text)}
    content = [stem for word, stem in tokens if not _noise((word, stem))]
    framed = any(stem in _FRAMING_STEMS for _, stem in tokens) or tokens[0][1] in _PROCESS_STEMS
    return framed and all(stem in vocabulary for stem in content)


def ref_path(ref) -> str:
    """The file a reference names, without a ``:line`` or ``:start-end`` suffix or trailing words."""
    first = str(ref).strip().split()[0] if str(ref).strip() else ""
    return _LINE_SUFFIX.sub("", first)


def problems(report: dict, *, request_texts, local_refs) -> list[str]:
    """Why ``report``'s findings do not answer the request; empty when they do.

    ``request_texts`` are the person's own texts (autocode_requirement_cues.source_texts); ``local_refs`` the
    report's ``source_refs`` that name files or pinned sources it read, not the person's inputs or a web page
    (autocode_requirements_faults.cited_source_refs)."""
    value = (report or {}).get(FIELD)
    if not isinstance(value, list) or not value:
        return [
            "Requirements' answer to this discussion has no findings (findings is empty), so its reply "
            "restates the request instead of answering it: give each finding, negative finding (looked for "
            "and not found) or evidence gap (could not be settled from what was read) with the requirement "
            "ids it answers"
        ]
    asked = _request_texts(report, request_texts)
    requirement_ids = [
        str(row.get("id"))
        for row in report.get("requirements") or []
        if isinstance(row, dict) and str(row.get("id") or "").strip()
    ]
    known = set(requirement_ids)
    refs = [str(ref).strip() for ref in local_refs or [] if str(ref).strip()]
    paths = {ref_path(ref) for ref in refs}
    found: list[str] = []
    answered: set[str] = set()
    for index, row in enumerate(value):
        name = f"findings[{index}]"
        if not isinstance(row, dict):
            found.append(f"{name} is not an object")
            continue
        kind = row.get("kind")
        if kind not in KINDS:
            found.append(f"{name} kind must be one of {', '.join(KINDS)}")
        ids = [str(item) for item in row.get("requirement_ids") or [] if str(item).strip()]
        if known and not ids:
            found.append(f"{name} names no requirement id: say which requirements it answers")
        found += [f"{name} names unknown requirement id {item}" for item in ids if item not in known]
        answered.update(ids)
        statement = str(row.get("statement") or "").strip()
        if restates(statement, asked):
            found.append(
                f"{name} restates the request instead of answering it: say what was found, what was looked "
                "for and not found, or what could not be settled, in words of its own"
            )
        evidence = [str(ref).strip() for ref in row.get("evidence") or [] if str(ref).strip()]
        if kind in ("finding", "negative_finding") and not evidence:
            found.append(f"{name} ({kind}) has no evidence: cite the source_refs entries it was read from")
        found += [
            f"{name} evidence {ref!r} is not one of the files or pinned sources this report read (its source_refs)"
            for ref in evidence
            if ref not in refs and ref_path(ref) not in paths
        ]
        if kind == "evidence_gap" and not str(row.get("would_settle") or "").strip():
            found.append(f"{name} is an evidence_gap without would_settle: say what evidence would settle it")
    found += [
        f"Requirement {item} is answered by no finding: name it in requirement_ids of the finding, negative "
        "finding or evidence gap that answers it (name a constraint on the finding it shaped)"
        for item in requirement_ids
        if item not in answered
    ]
    return found
