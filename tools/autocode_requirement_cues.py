"""Which saved text counts as user intent, and how it splits into cue sentences.

The requirement tracer must not oblige a report to quote formatting. A Markdown
heading such as ``## Required behavior`` is a label the author wrote, not a
requirement the user voiced, and having no sentence-ending punctuation it fuses
with the text after it into one un-quotable obligation. Substantive headings
(``## Files must be encrypted``) stay obligations, as clean sentences. A
delegated answer stores the model's own proposed default as its text: it stays
a source a report may quote, but it is not itself a must-quote obligation.
Fenced code blocks are left out of the obligations, as the brief-literal rule
leaves them out (autocode_brief_literals): pasted code, data or a quoted plan
is context, not sentences the user voiced, and it stays quotable.
Pure functions over the state dict; conversation source projection uses only
the independent handoff protocol.
"""
from __future__ import annotations

import re

try:
    from .autocode_conversation import task_user_texts
    from .autocode_brief_literals import FENCE
except ImportError:
    from autocode_conversation import task_user_texts
    from autocode_brief_literals import FENCE

CUE = re.compile(r"\b(must not|must|never|do not|don't|required|exactly|only)\b", re.I)
_CUE_WORDS = {"must", "not", "never", "do", "don't", "dont", "required",
              "requires", "requiring", "exactly", "only"}
# A heading made only of cue words and these generic section-label words is
# formatting; any other word in it makes it substantive, so it stays enforced.
_LABEL_WORDS = {"required", "requirement", "requirements", "behavior", "behaviour", "scope",
                "overview", "notes", "background", "context", "goals", "goal", "objective",
                "objectives", "summary", "details", "constraints", "acceptance", "criteria",
                "non-goals", "nongoals", "assumptions", "questions", "changes", "tasks",
                "outcomes", "implementation", "approach", "design"}
_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.+?)\s*$")


def _without_label_headings(text):
    out = []
    for line in str(text or "").splitlines():
        match = _HEADING.match(line)
        if match:
            words = [word.strip(".,;:!?()\"'").lower() for word in match.group(1).split()]
            if all(word in _CUE_WORDS or word in _LABEL_WORDS for word in words if word):
                continue
            line = match.group(1).rstrip(".!?") + "."
        out.append(line)
    return "\n".join(out)


def _outside_fences(text):
    """The text between fenced blocks, piece by piece."""
    pieces, start = [], 0
    for match in FENCE.finditer(text):
        pieces.append(text[start:match.start()])
        start = match.end()
    return [*pieces, text[start:]]


def cue_sentences(text):
    """Sentences carrying a requirement cue; heading labels and fenced blocks are never obligations."""
    # A program workstream's brief quotes the whole parent plan as a fenced JSON block; a live skeleton's
    # Requirements were rejected three times for not quoting other workstreams' notes from it (2026-10-06).
    # A block also ends the sentence before it: joined to the text after it, that sentence appeared nowhere
    # in the brief, so no verbatim quote could cover it.
    parts = [part for piece in _outside_fences(str(text or ""))
             for part in re.split(r"(?<=[.!?])\s+", _without_label_headings(piece).strip())]
    return [part.strip() for part in parts if part.strip() and CUE.search(part)]


def _task_sources(state):
    task = state.get("task") or ""
    human = task_user_texts(task)
    return human if human is not None else [task]


def source_texts(state):
    """Human task sources, brief feedback and answers a source_quote may cite."""
    texts = _task_sources(state)
    texts += [event.get("text", "") for event in state.get("brief_feedback", [])]
    texts += [event.get("text", "") for event in state.get("answers", {}).values() if isinstance(event, dict)]
    return [text for text in texts if text]


def scan_texts(state):
    """source_texts minus delegated answers: a model default is quotable, never owed."""
    texts = _task_sources(state)
    texts += [event.get("text", "") for event in state.get("brief_feedback", [])]
    texts += [event.get("text", "") for event in state.get("answers", {}).values()
              if isinstance(event, dict) and event.get("kind") != "delegated"]
    return [text for text in texts if text]
