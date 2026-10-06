"""Which durability and concurrency promises in a person's request no runner protocol proves (#451).

Disclosure only: it adds no obligation, check or completion gate, so an ordinary task never
acquires a crash-recovery battery. The runner proves process death only for the source-declared
APIs autocode_risk_acceptance recognizes, and it proves contention for none: its lifecycle
protocols run one worker at a time. Every other such promise rests on the Tester's own checks, and
the status view says so (``evidence.unverified_risk_claims``) instead of letting a PASS imply it.
Reads only the human source records autocode_brief_obligations derives from run state.
"""
from __future__ import annotations

import re

try:
    from . import autocode_brief_obligations as human, autocode_risk_acceptance as acceptance
except ImportError:
    import autocode_brief_obligations as human
    import autocode_risk_acceptance as acceptance

KINDS = {
    "durability": re.compile(r"\b(?:durabl[ey]|durability|survives?\s+(?:a\s+)?(?:restarts?|crash(?:es)?|reboots?)"
                             r"|crash[- ](?:safe|consistent)|power\s+loss|process\s+death)\b", re.I),
    "concurrency": re.compile(r"\b(?:concurren(?:t|tly|cy)|under\s+contention|thread[- ]safe|race[- ]free"
                              r"|race\s+conditions?|simultaneous(?:ly)?)\b", re.I),
}
# A sentence that puts the behavior out of scope promises nothing.
EXCLUDED = re.compile(r"\b(?:out\s+of\s+scope|not\s+(?:required|in\s+scope|needed|supported)|need\s+not|no\s+need)\b", re.I)
REASONS = {
    "durability": "No runner protocol exercises process death for this promise; only the Tester's own checks support it.",
    "concurrency": "No runner protocol exercises contention (the lifecycle protocols run one worker at a time); "
                   "only the Tester's own checks support it.",
}
LIMIT = 20


def claims(sources, declarations):
    """One row per sentence with an unproven durability or concurrency promise: kind, source_id, quote, reason."""
    proven = [(row["source_id"], *row["source_span"]) for row in declarations if row.get("supported")]
    rows = []
    for source in sources:
        text = source["text"]
        for match in re.finditer(r"[^.!?\n]+[.!?]?", text):
            sentence = match.group().strip()
            if not sentence or EXCLUDED.search(sentence):
                continue
            for kind, pattern in KINDS.items():
                if not pattern.search(sentence):
                    continue
                inside = any(source_id == source["id"] and start <= match.start() and match.end() <= end
                             for source_id, start, end in proven)
                if kind == "durability" and inside:
                    continue  # the source-declared lifecycle protocol hard-kills a worker for this one
                rows.append({"kind": kind, "source_id": source["id"], "quote": sentence[:300],
                             "reason": REASONS[kind]})
                if len(rows) >= LIMIT:
                    return rows
    return rows


def view(state):
    """``evidence.unverified_risk_claims`` for the status view; [] when the request makes no such promise."""
    try:
        sources = human.sources(state)
        return claims(sources, acceptance.inventory(sources, []))
    except (ValueError, TypeError, KeyError, AttributeError):
        return []
