"""How Requirements would achieve the request, confirmed with what it understood before any plan (#4171).

A person used to see the approach first in the detailed plan, after the Planner's calls were spent. A
wrong direction (the wrong trigger, the wrong place for a service to run) was then a plan to reject
instead of a sentence to correct. Requirements now writes a short sketch beside its understanding,
``approach``: the mechanism end to end (``summary``), the two to five things built or changed
(``pieces``), the size and blast radius (``band``), the real architectural choices with a one-line
tradeoff each (``alternatives``, the other ways besides ``summary``) and what the task deliberately
leaves out (``out_of_scope``). It is a sketch, not a plan: no milestones, steps or file lists.

The person sees it in the same Requirements confirmation that shows the understanding
(``autocode_requirements_review.review_question``), and one reply confirms both: ``plan`` takes the
sketched approach, an alternative's label takes that one, anything else is a correction Requirements
reads. That confirmation is asked when it is needed and only then (``asks``):

- the handoff was written after the person answered this turn's questions (the request needed a
  clarification round, so its direction is worth one look before any planning call), or
- the sketch offers alternatives (a real choice that only the person makes).

A clear request with no alternatives asks nothing more: the sketch goes to the Planner, and the plan
approval shows its steps as before. An unattended ``--skip-requirements`` run never stops for it.

The confirmed approach is saved on the handoff (``confirmed_approach``), which the Planner and the
Plan Reviewer receive: the Planner plans that approach, and a plan that departs from it is a Plan
Reviewer concern. Pure over the run state; imports only ``autocode_util``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util

FIELD = "approach"
CONFIRMED = "confirmed_approach"
_TEXT = {"type": "string"}
_TEXTS = {"type": "array", "items": _TEXT}
ALTERNATIVE = {
    "type": "object",
    "properties": {"label": _TEXT, "tradeoff": _TEXT},
    "required": ["label", "tradeoff"],
    "additionalProperties": False,
}
SCHEMA = {
    "type": "object",
    "properties": {
        "summary": _TEXT,
        "pieces": _TEXTS,
        "band": _TEXT,
        "alternatives": {"type": "array", "items": ALTERNATIVE},
        "out_of_scope": _TEXTS,
    },
    "required": ["summary", "pieces", "band", "alternatives", "out_of_scope"],
    "additionalProperties": False,
}
EMPTY = {"summary": "", "pieces": [], "band": "", "alternatives": [], "out_of_scope": []}
SHOWN = 5


def _clean(value) -> str:
    return " ".join(str(value).split()) if isinstance(value, str) else ""


def sketch(report) -> dict:
    """The report's approach, normalized; EMPTY when it has none (a report saved before #4171, a triage)."""
    raw = report.get(FIELD) if isinstance(report, dict) else None
    if not isinstance(raw, dict) or not _clean(raw.get("summary")):
        return dict(EMPTY)
    alternatives = []
    for row in raw.get("alternatives") or []:
        label = _clean(row.get("label")) if isinstance(row, dict) else ""
        if label and label.lower() not in {item["label"].lower() for item in alternatives}:
            alternatives.append({"label": label, "tradeoff": _clean(row.get("tradeoff"))})
    return {
        "summary": _clean(raw["summary"]),
        "pieces": [text for row in raw.get("pieces") or [] if (text := _clean(row))],
        "band": _clean(raw.get("band")),
        "alternatives": alternatives,
        "out_of_scope": [text for row in raw.get("out_of_scope") or [] if (text := _clean(row))],
    }


def lines(report, approve: str) -> list[str]:
    """The sketch as the confirmation shows it, ending with how one reply picks an alternative."""
    shown = sketch(report)
    if not shown["summary"]:
        return []
    out = ["", f"How I would do it: {shown['summary']}"]
    if shown["pieces"]:
        out += ["", "Pieces:", *(f"  - {row}" for row in shown["pieces"][:SHOWN])]
    if shown["band"]:
        out += ["", f"Size: {shown['band']}"]
    if shown["out_of_scope"]:
        out += ["", "Not part of this task:", *(f"  - {row}" for row in shown["out_of_scope"][:SHOWN])]
    if shown["alternatives"]:
        out += ["", "Other ways to do it (reply with one's name to choose it instead):"]
        out += [
            f"  - {row['label']}" + (f": {row['tradeoff']}" if row["tradeoff"] else "") for row in shown["alternatives"]
        ]
        out += ["", f"Replying {approve} takes the approach above."]
    return out


def chosen(report, text) -> str | None:
    """The alternative a reply names by its label (case and surrounding spaces ignored), else None."""
    wanted = _clean(text).lower().rstrip(".")
    for row in sketch(report)["alternatives"]:
        if row["label"].lower() == wanted:
            return row["label"]
    return None


def _after_answers(state: RunState, handoff: dict) -> bool:
    """Whether ``handoff`` was written with answers the previous handoff of this run had not seen."""
    seen = set(handoff.get("answers_seen") or [])
    history = [row for row in state.get("requirements_history") or [] if isinstance(row, dict)]
    before = set(history[-1].get("answers_seen") or []) if history else set()
    return bool(seen - before)


def asks(state: RunState, handoff: dict, report: dict, *, skipped: bool) -> bool:
    """Whether this handoff's approach waits for the person's confirmation (see the module docstring)."""
    shown = sketch(report)
    if skipped or not shown["summary"] or confirmation(handoff, report):
        return False
    return bool(shown["alternatives"]) or _after_answers(state, handoff)


def confirmation(handoff: dict, report: dict) -> dict | None:
    """The saved confirmation of exactly this report's approach, else None."""
    record = handoff.get(CONFIRMED) if isinstance(handoff, dict) else None
    if not isinstance(record, dict) or record.get("sketch_sha256") != util.digest(sketch(report)):
        return None
    return record


def confirm(handoff: dict, report: dict, *, choice: str | None, source: str, question_id: str | None = None) -> None:
    """Save the confirmed approach on the handoff: the sketch's own, or the alternative ``choice`` names."""
    shown = sketch(report)
    if not shown["summary"]:
        return
    picked = next((row for row in shown["alternatives"] if row["label"] == choice), None)
    handoff[CONFIRMED] = {
        "source": source,
        **({"question_id": question_id} if question_id else {}),
        "choice": picked["label"] if picked else "sketch",
        "approach": f"{picked['label']}: {picked['tradeoff']}".rstrip(": ") if picked else shown["summary"],
        "sketch_sha256": util.digest(shown),
    }
