"""What a person reads when AutoCode stopped because it could not accept a stage's answer.

Every pause of this kind (``PAUSED_INVALID_OUTPUT``, ``PAUSED_REPORT_REPAIR_LIMIT``,
``PAUSED_REPORT_REPAIR_INPUT``) used to show one fixed sentence, "The saved report could not be
accepted in its current form", and a "Resume after correction" button. The run had recorded which
stage's answer was refused, why, how often AutoCode had repaired it and what the Investigator found,
but none of that reached the chat, and the button asked the person to correct something that was
never theirs to correct (live runs, 2026-11-17). The same sentence covered a Tester whose change broke
the original tests, a Planner whose plan named no runnable test and a Requirements answer that cited
an unknown requirement.

``summary`` builds the sentence from what the run recorded: the stage, the first plain sentences of
its rejection, what AutoCode already tried, and what resuming does. ``reason`` turns a recorded
rejection into person text: it drops receipts, file paths of the run's own scratch, commands and
flags, and falls back to a plain category when nothing readable is left. Pure functions over the run
state; imports nothing above the domain layer.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_acceptance_retry_next as retry_next
    from . import autocode_person_text as person_text
    from . import autocode_roles as roles
except ImportError:
    import autocode_acceptance_retry_next as retry_next
    import autocode_person_text as person_text
    import autocode_roles as roles

STATUSES = frozenset({"PAUSED_INVALID_OUTPUT", "PAUSED_REPORT_REPAIR_LIMIT", "PAUSED_REPORT_REPAIR_INPUT"})
REPAIR_SUFFIX = "_report_repair"
LIMIT = 320
# The runner's own wrapper around a rejection: "Completed X output was rejected (REASON); attempt archived. ..."
WRAPPED = re.compile(r"output was rejected \((?P<reason>.*)\); attempt archived", re.DOTALL)
NATIVE = re.compile(r"^Original native output retained:\s*")
# A receipt or evidence path the runner appends for an operator; never for a person.
TRAILER = re.compile(r"\s*(?:Receipt|Evidence|Probe|Output probe):\s.*$", re.DOTALL | re.IGNORECASE)
# What a person's client refuses to show: flags, ALL_CAPS_IDENTIFIERS, and code spans (autocode_chat_view.said).
CLIENT_MACHINERY = re.compile(r"(?:^|\s)--[a-z]|\b[A-Z]+(?:_[A-Z]+)+\b|`")
CODE_SPAN = re.compile(r"`([^`]*)`")
ABSOLUTE_PATH = re.compile(r"(?<![\w.])/(?:tmp|private|var|home|Users)/\S+")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _rows(value) -> list[dict]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _base(stage) -> str:
    stage = str(stage or "")
    return stage[: -len(REPAIR_SUFFIX)] if stage.endswith(REPAIR_SUFFIX) else stage


def _sentence(text: str) -> str:
    """One sentence of recorded text, readable by a person, or "" when it is machinery."""
    text = CODE_SPAN.sub(lambda match: "“" + match.group(1).strip() + "”", text)
    text = ABSOLUTE_PATH.sub("a saved file", text)
    text = " ".join(text.split()).strip()
    if not text or CLIENT_MACHINERY.search(text) or person_text.machinery(text) or person_text.internal_machinery(text):
        return ""
    return text


def reason(raw: Any, *, sentences: int = 2) -> str:
    """The first plain ``sentences`` of a recorded rejection, ending with a full stop; "" when none is readable."""
    text = str(raw or "")
    wrapped = WRAPPED.search(text)
    if wrapped:
        text = wrapped.group("reason")
    text = TRAILER.sub("", NATIVE.sub("", text)).strip()
    if not text:
        return ""
    summary = person_text.human_summary(text)
    if summary:
        return summary + "."
    kept: list[str] = []
    for part in SENTENCE_END.split(text):
        sentence = _sentence(part)
        if not sentence:
            if kept:
                break
            continue
        if kept and len(" ".join([*kept, sentence])) > LIMIT:
            break
        kept.append(sentence)
        if len(kept) == sentences:
            break
    shown = " ".join(kept)
    if len(shown) > LIMIT:
        shown = shown[:LIMIT].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    if shown and not shown.endswith((".", "!", "?", "…")):
        shown += "."
    return shown


def _stage_of(row: dict) -> str:
    return _base(row.get("original_stage") or row.get("stage"))


def _rejected(state: RunState, stage: str | None) -> dict | None:
    """The latest rejected attempt of ``stage`` (its repairs included), else the latest of any stage, or None.

    An Investigator whose own report was refused while looking at the stage is not the stage's reason."""
    rows = [row for row in reversed(_rows(state.get("stages"))) if row.get("rejected")]
    return next((row for row in rows if stage and _stage_of(row) == stage), None) or (
        None if stage else next(iter(rows), None)
    )


def _repairs(state: RunState, stage: str) -> int:
    """Report-only repairs of ``stage`` since its last own attempt."""
    count = 0
    for row in reversed(_rows(state.get("stages"))):
        name = str(row.get("stage") or "")
        if name == stage + REPAIR_SUFFIX or (row.get("report_only") and _base(row.get("original_stage")) == stage):
            count += 1
        elif _base(name) == stage:
            break
    return count


def _investigation(state: RunState, stage: str) -> dict | None:
    return next(
        (
            row
            for row in reversed(_rows(state.get("stuck_investigations")))
            if _base(row.get("stage")) == stage and row.get("diagnosis")
        ),
        None,
    )


def _times(count: int) -> str:
    return {1: "once", 2: "twice"}.get(count, f"{count} times")


def summary(
    state: RunState, status: str, role: str | None, stage: str | None = None, retained: dict | None = None
) -> dict | None:
    """``{"what_happened", "resume_label", "resume_effect"}`` for a rejected-answer stop, or None for any other.

    ``stage`` and ``role`` are the stopped stage and its screen name, as the recovery view names them.
    ``retained`` is the retained acceptance retry's answer (autocode_acceptance_retry_next.decide): a resume
    that only re-checks the saved tests is labelled as that, and a spent one says how often it ran."""
    if status not in STATUSES:
        return None
    stage = _base(stage) or None
    record = _rejected(state, stage)
    stage = stage or (_stage_of(record) if record else "")
    name = role or (roles.screen_name(stage, state) if stage else "")
    who = f"the {name} step" if name else "this step"
    why = reason(_dict(record).get("rejection_reason")) or reason(state.get("stop_reason"))
    if status == "PAUSED_REPORT_REPAIR_INPUT":
        head = (
            f"AutoCode could not repair the answer of {who}: the saved answer it would repair is missing or too large."
        )
    elif why:
        head = f"AutoCode did not accept {who}'s answer. {why}"
    else:
        head = f"AutoCode did not accept {who}'s answer; the reason it recorded names only internal details."
    tried = []
    repairs = _repairs(state, stage) if stage else 0
    if repairs:
        tried.append(f"AutoCode asked {who} to fix its answer {_times(repairs)}")
    looked = _investigation(state, stage) if stage else None
    diagnosis = reason(_dict(looked).get("diagnosis"), sentences=1)
    if diagnosis:
        tried.append(f"the Investigator found: {diagnosis[0].lower() + diagnosis[1:]}")
    elif looked:
        tried.append("the Investigator looked into it")
    parts = [head]
    if tried:
        joined = "; ".join(tried)
        parts.append(joined[0].upper() + joined[1:] + ("" if joined.endswith((".", "…")) else "."))
    parts.append("It stopped rather than spend more on the same answer.")
    following = (retained or {}).get("next") if status == retry_next.PAUSE and stage == retry_next.STAGE else None
    if following == retry_next.EXHAUSTED:
        parts.append(retry_next.sentence(retained or {}))
    if following == retry_next.RECHECK:
        return {
            "what_happened": " ".join(parts),
            "resume_label": "Re-check the saved tests",
            "resume_effect": (
                "AutoCode runs the saved tests again without a new Tester attempt, and checks them the same way. "
                "Earlier attempts and evidence are kept; existing limits and approval gates still apply."
            ),
        }
    return {
        "what_happened": " ".join(parts),
        "resume_label": f"Try {who} again",
        "resume_effect": (
            f"AutoCode runs {who} again from the saved work and checks its new answer the same way. Nothing needs "
            "correcting first. Earlier attempts and evidence are kept; existing model pins, limits and approval gates "
            "still apply."
        ),
    }
