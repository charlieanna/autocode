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

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import re

try:
    from . import autocode_linked_designs as linked_designs
    from . import autocode_requirements_review as requirements_review
    from . import autocode_source_log as source_log
    from .autocode_conversation import task_user_texts
except ImportError:
    import autocode_linked_designs as linked_designs
    import autocode_requirements_review as requirements_review
    import autocode_source_log as source_log
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
# The marker alone ("2." before "The panel did not ...") is what the sentence split leaves when an
# item's first sentence ends; it states nothing, so it is never a sentence to cite, exclude or keep
# the number of (#3118).
_LIST_MARKER = re.compile(r"(?:[-*+]|\d+[.)])")


def _without_label_headings(text, *, checklist=False):
    out = []
    for line in str(text or "").splitlines():
        match = _HEADING.match(line)
        if match:
            words = [word.strip(".,;:!?()\"'").lower() for word in match.group(1).split()]
            if all(word in _CUE_WORDS or word in _LABEL_WORDS for word in words if word):
                continue
            if checklist:
                # The coverage checklist shows a substantive heading as a clean sentence: its text with a
                # final period, so it never fuses with the next sentence (autocode_requirement_quotes.ground
                # maps a quote of it back to the source's own characters).
                line = match.group(1).rstrip(" .") + "."
            else:
                # Keep the heading's source text as-is (#3054): a citation must resolve
                # to a verbatim substring of the task, and `## Why` is what the task holds.
                line = line.rstrip()
        out.append(line)
    return "\n".join(out)


def _outside_fences(text):
    """The text between fenced blocks, piece by piece."""
    pieces, start = [], 0
    for match in _FENCE.finditer(text):
        pieces.append(text[start : match.start()])
        start = match.end()
    return [*pieces, text[start:]]


def sentences(text, *, checklist=False):
    """Every sentence in text, cued or not; heading labels and fenced blocks are left out.

    ``checklist`` renders a substantive heading as the coverage checklist shows it (``cue_sentences``);
    otherwise it stays the source's own text, as a citation needs it."""
    # A program workstream's brief quotes the whole parent plan as a fenced JSON block; a live skeleton's
    # Requirements were rejected three times for not quoting other workstreams' notes from it (2026-10-06).
    # A block also ends the sentence before it: joined to the text after it, that sentence appeared nowhere
    # in the brief, so no verbatim quote could cover it.
    parts = [
        part
        for piece in _outside_fences(str(text or ""))
        for block in _BLOCK_BOUNDARY.split(piece)
        for part in re.split(r"(?<=[.!?])\s+", _without_label_headings(block, checklist=checklist).strip())
    ]
    return [part.strip() for part in parts if part.strip() and not _LIST_MARKER.fullmatch(part.strip())]


def cue_sentences(text):
    """Sentences carrying a requirement cue; heading labels and fenced blocks are never obligations."""
    return [part for part in sentences(text, checklist=True) if CUE.search(part)]


def _task_sources(state: RunState):
    task = state.get("task") or ""
    human = task_user_texts(task)
    return human if human is not None else [task]


def source_texts(state: RunState):
    """Human task sources, brief feedback, answers, linked issues and user-named design docs a source_quote may cite."""
    user_texts = list(_task_sources(state))
    user_texts += [event.get("text", "") for event in state.get("brief_feedback", [])]
    user_texts += [event.get("text", "") for event in state.get("answers", {}).values() if isinstance(event, dict)]
    texts = list(user_texts)
    texts += [row["text"] for row in source_log.linked_issue_records(state) if row.get("text")]
    logged_designs = source_log.linked_design_records(state)
    texts += [row["text"] for row in logged_designs if row.get("text")]
    workspace = state.get("workspace")
    if workspace:
        seen_paths = {row.get("path") for row in logged_designs if isinstance(row.get("path"), str)}
        fallback_paths: list[str] = []
        if source_log.records(state) is None:
            for msg in user_texts:
                for rel in linked_designs.extract_paths(msg):
                    if rel not in seen_paths and rel not in fallback_paths:
                        fallback_paths.append(rel)
        for rel in linked_designs.buildable_turn_paths(state.get("turns")):
            if rel not in seen_paths and rel not in fallback_paths:
                fallback_paths.append(rel)
        for rel in fallback_paths:
            content = linked_designs.read_design_file(workspace, rel)
            if content:
                texts.append(content)
    return [text for text in texts if text]


def new_workflow_turn(state: RunState):
    """A receipted follow-up changing jobs; same-kind revisions retain their obligations.

    A follow-up whose job is not named yet counts too: Requirements, the first stage of every turn,
    names it (#1860), and until then the new message alone carries fresh obligations, as it did when
    the job recognizer ran first and named a different job."""
    turns = state.get("turns") or []
    turn = turns[-1] if turns else {}
    previous = (turn.get("previous") or {}).get("workflow")
    workflow = state.get("workflow")
    current = workflow.get("kind") if isinstance(workflow, dict) else None
    if not previous or not isinstance(workflow, dict) or current == previous:
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


def earlier_job_handoff(state: RunState, report) -> bool:
    """Whether the saved handoff is the earlier turn's and ``report`` names this follow-up a different job.

    Every turn starts at Requirements (#1860), so a follow-up's first report refreshes the handoff the
    earlier job left: "Fix them." after a review would have to keep the review's "Do not fix anything"
    as a requirement. A different job's requirements are context, not obligations (archived to
    requirements_history as every replaced handoff is); the same job's are retained as before. A report
    naming no job is planned as a build (autocode_workflows.from_requirements)."""
    turns = state.get("turns") or []
    if not turns or (state.get("workflow") or {}).get("kind"):
        return False
    previous = turns[-1].get("previous") or {}
    handoff = (state.get("requirements_handoff") or {}).get("output")
    named = report.get("job") if report.get("job") in requirements_review.JOBS else "build"
    return bool(handoff) and handoff == previous.get("requirements_output") and named != previous.get("workflow")


def scan_texts(state: RunState):
    """Distinct obligation sources; delegated defaults remain quotable, never owed."""
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
    # A follow-up is both the current task and receipted feedback. Scan it once without changing either
    # journal entry or source_texts: prompts, report repair and validation share this projection.
    return list(dict.fromkeys(text for text in texts if text))


def coverage_checklist(state: RunState):
    """The requirement-like sentences a Requirements report must cover, in the order the handoff lists them.

    One source of truth for the handoff packet, its report repair, and the id a requirement may cite
    instead of retyping a sentence (#3344): all three have to number the same list the same way."""
    return [sentence for source in scan_texts(state) for sentence in cue_sentences(source)]
