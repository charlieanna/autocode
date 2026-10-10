"""Which saved text counts as user intent, and how it splits into cue sentences.

The requirement tracer must not oblige a report to quote formatting. A Markdown
heading such as ``## Required behavior`` is a label the author wrote, not a
requirement the user voiced, and having no sentence-ending punctuation it fuses
with the text after it into one un-quotable obligation. Substantive headings
(``## Files must be encrypted``) stay obligations, as clean sentences. A
delegated answer stores the model's own proposed default as its text: it stays
a source a report may quote, but it is not itself a must-quote obligation.
A fenced code block, opened and closed by lines of their own, is left out of
the obligations: pasted code, data or a quoted plan is context, not sentences
the user voiced, and it stays quotable. A ``` inside a line, or a fence that
never closes, hides nothing: what follows stays owed.
Pure functions over the state dict; conversation source projection uses only
the independent handoff protocol.
"""

from __future__ import annotations

import re

try:
    from .autocode_conversation import task_user_texts
except ImportError:
    from autocode_conversation import task_user_texts

CUE = re.compile(r"\b(must not|must|never|do not|don't|required|exactly|only)\b", re.I)
_CUE_WORDS = {"must", "not", "never", "do", "don't", "dont", "required", "requires", "requiring", "exactly", "only"}
# A heading made only of cue words and these generic section-label words is
# formatting; any other word in it makes it substantive, so it stays enforced.
_LABEL_WORDS = {
    "required",
    "requirement",
    "requirements",
    "behavior",
    "behaviour",
    "scope",
    "overview",
    "notes",
    "background",
    "context",
    "goals",
    "goal",
    "objective",
    "objectives",
    "summary",
    "details",
    "constraints",
    "acceptance",
    "criteria",
    "non-goals",
    "nongoals",
    "assumptions",
    "questions",
    "changes",
    "tasks",
    "outcomes",
    "implementation",
    "approach",
    "design",
}
_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.+?)\s*$")
# Unlike the brief-literal FENCE, an inline ``` or an unclosed fence would otherwise hide every obligation after it.
_FENCE = re.compile(r"^[ \t]{0,3}```.*?^[ \t]{0,3}```[^\n]*$", re.M | re.S)
# A paragraph or new list item cannot complete the preceding item's sentence.
# Horizontal whitespace after a marker distinguishes lists from -I and 3.14.
_BLOCK_BOUNDARY = re.compile(r"\r?\n[ \t]*\r?\n|(?<=\n)(?=[ \t]*(?:[-*+]|\d+[.)])[ \t]+)")


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
    for match in _FENCE.finditer(text):
        pieces.append(text[start : match.start()])
        start = match.end()
    return [*pieces, text[start:]]


def cue_sentences(text):
    """Sentences carrying a requirement cue; heading labels and fenced blocks are never obligations."""
    # A program workstream's brief quotes the whole parent plan as a fenced JSON block; a live skeleton's
    # Requirements were rejected three times for not quoting other workstreams' notes from it (2026-10-06).
    # A block also ends the sentence before it: joined to the text after it, that sentence appeared nowhere
    # in the brief, so no verbatim quote could cover it.
    parts = [
        part
        for piece in _outside_fences(str(text or ""))
        for block in _BLOCK_BOUNDARY.split(piece)
        for part in re.split(r"(?<=[.!?])\s+", _without_label_headings(block).strip())
    ]
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


def new_workflow_turn(state):
    """A receipted follow-up changing jobs; same-kind revisions retain their obligations."""
    turns = state.get("turns") or []
    turn = turns[-1] if turns else {}
    previous = (turn.get("previous") or {}).get("workflow")
    current = (state.get("workflow") or {}).get("kind")
    if not previous or not current or current == previous:
        return None
    event = next(
        (
            row
            for row in state.get("brief_feedback") or []
            if row.get("id") == turn.get("event_id")
            and row.get("text") == turn.get("say")
            and row.get("kind") == "brief_feedback"
            and row.get("actor") == "user_cli"
        ),
        None,
    )
    return turn if event and event in (state.get("user_events") or []) else None


def scan_texts(state):
    """source_texts minus delegated answers: a model default is quotable, never owed."""
    turn = new_workflow_turn(state)
    feedback = state.get("brief_feedback") or []
    answers = list((state.get("answers") or {}).values())
    texts = _task_sources(state)
    if turn:
        # Completed-job outputs stay quotable context, not fresh literal obligations.
        texts = [turn["say"]]
        start = next(i for i, event in enumerate(feedback) if event.get("id") == turn["event_id"])
        feedback = feedback[start:]
        events = state.get("user_events") or []
        start = next(i for i, event in enumerate(events) if event == feedback[0])
        answers = [event for event in answers if event in events[start:]]
    texts += [event.get("text", "") for event in feedback]
    texts += [
        event.get("text", "") for event in answers if isinstance(event, dict) and event.get("kind") != "delegated"
    ]
    return [text for text in texts if text]
